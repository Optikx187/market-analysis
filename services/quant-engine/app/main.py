"""Service B — Quantitative Analytics & Risk Engine."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import settings
from app.risk_engine import evaluate_risk_profile
from app.signals import evaluate_signals

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Scanner state
_scanner_task: Optional[asyncio.Task] = None
_scan_state = {
    "enabled": True,
    "interval_minutes": 15,
    "market_hours_only": True,
    "last_scan_at": None,
    "next_scan_at": None,
    "last_scan_result": None,
    "total_scans": 0,
    "total_signals_found": 0,
}
# Dedup: ticker -> last signal direction+timestamp (don't re-alert within 24h)
_recent_signals: dict[str, str] = {}


def _is_market_hours() -> bool:
    """Check if US stock market is open (9:30-16:00 ET, Mon-Fri)."""
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scanner_task
    _scan_state["enabled"] = settings.SCAN_ENABLED
    _scan_state["interval_minutes"] = settings.SCAN_INTERVAL_MINUTES
    _scan_state["market_hours_only"] = settings.MARKET_HOURS_ONLY
    if _scan_state["enabled"]:
        _scanner_task = asyncio.create_task(_scanner_loop())
        logger.info(f"Scanner started: every {_scan_state['interval_minutes']}min")
    yield
    if _scanner_task and not _scanner_task.done():
        _scanner_task.cancel()
        try:
            await _scanner_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Quant Engine Service", version="3.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


class SignalResponse(BaseModel):
    ticker: str
    direction: Optional[str]
    status: str
    trigger_price: float
    stop_loss: float
    target_price: float
    reason: str
    risk_reward: float
    atr_value: float
    rsi_value: float
    asset_type: str = "stock"
    suppressed: bool
    kelly_pct: float
    optimal_size_usd: float
    volatility_scalar: float


class RiskProfileResponse(BaseModel):
    ticker: str
    is_tanking: bool
    tanking_reason: Optional[str]
    win_rate_30d: float
    risk_reward_ratio: float
    kelly_fraction: float
    volatility_scalar: float
    optimal_position_pct: float
    optimal_position_usd: float
    atr_current: float
    atr_avg_30: float
    ema_20: float
    ema_50: float
    ema_200: float
    rsi: float
    current_price: float
    recommend_liquidate: bool


class AnalyzeRequest(BaseModel):
    ticker: str
    available_capital: float = 10_000.0
    asset_type: str = "stock"
    timeframes: list[str] = ["1d"]


class ScanConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = None
    market_hours_only: Optional[bool] = None


class BacktestRequest(BaseModel):
    ticker: str
    period: str = "6mo"
    available_capital: float = 10_000.0


async def fetch_candles_from_service_a(ticker: str) -> pd.DataFrame:
    url = f"{settings.DATA_INGESTION_URL}/api/candles/{ticker}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    if not data:
        return pd.DataFrame()
    return pd.DataFrame(data)


async def _fetch_assets() -> list[dict]:
    url = f"{settings.DATA_INGESTION_URL}/api/assets"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=15)
        resp.raise_for_status()
    return resp.json()


async def _send_notification(signal_data: dict) -> bool:
    url = f"{settings.NOTIFICATION_GATEWAY_URL}/api/notify"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=signal_data, timeout=15)
            resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send notification: {e}")
        return False


async def _process_signal_via_portfolio(signal_data: dict) -> Optional[dict]:
    url = f"{settings.PORTFOLIO_ENGINE_URL}/api/process-signal"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=signal_data, timeout=15)
            resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Failed to process signal: {e}")
        return None


async def _run_scan() -> dict:
    """Scan all active assets for signals and send notifications."""
    now = datetime.now(timezone.utc)
    _scan_state["last_scan_at"] = now.isoformat()

    assets = await _fetch_assets()
    scanned = 0
    signals_found = 0
    notifications_sent = 0
    errors = 0
    signal_details = []

    for asset in assets:
        if not asset.get("is_active", True):
            continue
        ticker = asset["ticker"]
        asset_type = asset.get("asset_type", "stock")
        try:
            df = await fetch_candles_from_service_a(ticker)
            if df.empty or len(df) < 201:
                continue
            scanned += 1
            result = evaluate_signals(df, 10_000.0)
            if result is None:
                continue

            signals_found += 1
            dedup_key = f"{ticker}:{result.direction}"
            last_signal = _recent_signals.get(dedup_key)
            if last_signal:
                last_time = datetime.fromisoformat(last_signal)
                if (now - last_time) < timedelta(hours=24):
                    continue

            _recent_signals[dedup_key] = now.isoformat()

            signal_data = {
                "ticker": ticker,
                "direction": result.direction or "NONE",
                "status": result.status,
                "trigger_price": result.trigger_price,
                "stop_loss": result.stop_loss,
                "target_price": result.target_price,
                "reason": result.reason,
                "risk_reward": result.risk_reward,
                "atr_value": result.atr_value,
                "rsi_value": result.rsi_value,
                "suppressed": result.suppressed,
                "kelly_pct": result.kelly_pct,
                "optimal_size_usd": result.optimal_size_usd,
                "volatility_scalar": result.volatility_scalar,
                "asset_type": asset_type,
            }

            decision = await _process_signal_via_portfolio(signal_data)
            approved = decision and decision.get("approved", False)

            if approved and not result.suppressed:
                notify_data = {
                    "ticker": ticker,
                    "direction": result.direction or "NONE",
                    "status": result.status,
                    "trigger_price": result.trigger_price,
                    "target_price": result.target_price,
                    "stop_loss": result.stop_loss,
                    "optimal_size_usd": result.optimal_size_usd,
                    "kelly_pct": result.kelly_pct,
                    "paper_trade_executed": False,
                }
                sent = await _send_notification(notify_data)
                if sent:
                    notifications_sent += 1

            signal_details.append({
                "ticker": ticker,
                "direction": result.direction,
                "status": result.status,
                "approved": approved,
                "suppressed": result.suppressed,
            })

        except Exception as e:
            errors += 1
            logger.error(f"Scan error for {ticker}: {e}")

    result_summary = {
        "scanned": scanned,
        "signals_found": signals_found,
        "notifications_sent": notifications_sent,
        "errors": errors,
        "signals": signal_details,
        "timestamp": now.isoformat(),
    }
    _scan_state["last_scan_result"] = result_summary
    _scan_state["total_scans"] += 1
    _scan_state["total_signals_found"] += signals_found
    return result_summary


async def _scanner_loop():
    await asyncio.sleep(30)  # initial delay to let services boot
    while True:
        try:
            if not _scan_state["enabled"]:
                await asyncio.sleep(60)
                continue

            if _scan_state["market_hours_only"] and not _is_market_hours():
                await asyncio.sleep(60)
                continue

            interval = _scan_state["interval_minutes"]
            last = _scan_state.get("last_scan_at")
            if last:
                last_time = datetime.fromisoformat(last)
                elapsed = (datetime.now(timezone.utc) - last_time).total_seconds()
                if elapsed < interval * 60:
                    _scan_state["next_scan_at"] = (
                        last_time + timedelta(minutes=interval)
                    ).isoformat()
                    await asyncio.sleep(30)
                    continue

            logger.info("Starting scheduled scan...")
            result = await _run_scan()
            logger.info(
                f"Scan complete: {result['scanned']} scanned, "
                f"{result['signals_found']} signals, "
                f"{result['notifications_sent']} notifications"
            )
            _scan_state["next_scan_at"] = (
                datetime.now(timezone.utc) + timedelta(minutes=interval)
            ).isoformat()

        except Exception as e:
            logger.error(f"Scanner loop error: {e}")

        await asyncio.sleep(30)


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "quant-engine"}


@app.post("/api/analyze", response_model=Optional[SignalResponse])
async def analyze(req: AnalyzeRequest):
    df = await fetch_candles_from_service_a(req.ticker)
    if df.empty or len(df) < 201:
        raise HTTPException(400, f"Insufficient data for {req.ticker} (need 201+ candles)")

    result = evaluate_signals(df, req.available_capital)
    if result is None:
        return None

    return SignalResponse(
        ticker=req.ticker,
        direction=result.direction,
        status=result.status,
        trigger_price=result.trigger_price,
        stop_loss=result.stop_loss,
        target_price=result.target_price,
        reason=result.reason,
        risk_reward=result.risk_reward,
        atr_value=result.atr_value,
        rsi_value=result.rsi_value,
        suppressed=result.suppressed,
        kelly_pct=result.kelly_pct,
        optimal_size_usd=result.optimal_size_usd,
        volatility_scalar=result.volatility_scalar,
        asset_type=req.asset_type,
    )


@app.post("/api/risk-profile", response_model=Optional[RiskProfileResponse])
async def risk_profile(req: AnalyzeRequest):
    df = await fetch_candles_from_service_a(req.ticker)
    if df.empty or len(df) < 201:
        raise HTTPException(400, f"Insufficient data for {req.ticker}")

    profile = evaluate_risk_profile(df, req.ticker, req.available_capital)
    if profile is None:
        return None

    return RiskProfileResponse(
        ticker=profile.ticker,
        is_tanking=profile.is_tanking,
        tanking_reason=profile.tanking_reason,
        win_rate_30d=profile.win_rate_30d,
        risk_reward_ratio=profile.risk_reward_ratio,
        kelly_fraction=profile.kelly_fraction,
        volatility_scalar=profile.volatility_scalar,
        optimal_position_pct=profile.optimal_position_pct,
        optimal_position_usd=profile.optimal_position_usd,
        atr_current=profile.atr_current,
        atr_avg_30=profile.atr_avg_30,
        ema_20=profile.ema_20,
        ema_50=profile.ema_50,
        ema_200=profile.ema_200,
        rsi=profile.rsi,
        current_price=profile.current_price,
        recommend_liquidate=profile.recommend_liquidate,
    )


# --- Phase 1: Scheduled Signal Scanning ---

@app.post("/api/scan-all")
async def scan_all():
    """Manually trigger a full watchlist scan."""
    result = await _run_scan()
    return result


@app.get("/api/scanner/status")
async def scanner_status():
    """Return current scanner configuration and state."""
    return {
        "enabled": _scan_state["enabled"],
        "interval_minutes": _scan_state["interval_minutes"],
        "market_hours_only": _scan_state["market_hours_only"],
        "last_scan_at": _scan_state["last_scan_at"],
        "next_scan_at": _scan_state["next_scan_at"],
        "last_scan_result": _scan_state["last_scan_result"],
        "total_scans": _scan_state["total_scans"],
        "total_signals_found": _scan_state["total_signals_found"],
    }


@app.post("/api/scanner/config")
async def update_scanner_config(config: ScanConfigUpdate):
    """Update scanner configuration at runtime."""
    if config.enabled is not None:
        _scan_state["enabled"] = config.enabled
    if config.interval_minutes is not None:
        if config.interval_minutes < 5:
            raise HTTPException(400, "Minimum scan interval is 5 minutes")
        _scan_state["interval_minutes"] = config.interval_minutes
    if config.market_hours_only is not None:
        _scan_state["market_hours_only"] = config.market_hours_only
    return {
        "message": "Scanner config updated",
        "enabled": _scan_state["enabled"],
        "interval_minutes": _scan_state["interval_minutes"],
        "market_hours_only": _scan_state["market_hours_only"],
    }


# --- Phase 4: Multi-Timeframe Analysis ---

@app.post("/api/analyze/multi-timeframe")
async def analyze_multi_timeframe(req: AnalyzeRequest):
    """Analyze a ticker across multiple timeframes and score confluence."""
    results_by_tf = {}
    buy_signals = 0
    sell_signals = 0
    total_tf = 0

    for tf in req.timeframes:
        try:
            if tf == "1d":
                df = await fetch_candles_from_service_a(req.ticker)
            else:
                url = f"{settings.DATA_INGESTION_URL}/api/candles/{req.ticker}?interval={tf}"
                async with httpx.AsyncClient() as client:
                    resp = await client.get(url, timeout=30)
                    resp.raise_for_status()
                    data = resp.json()
                df = pd.DataFrame(data) if data else pd.DataFrame()

            if df.empty or len(df) < 201:
                results_by_tf[tf] = {"status": "insufficient_data", "candles": len(df) if not df.empty else 0}
                continue

            result = evaluate_signals(df, req.available_capital)
            if result is None:
                results_by_tf[tf] = {"status": "no_signal"}
                continue

            total_tf += 1
            if result.direction == "BUY":
                buy_signals += 1
            elif result.direction == "SELL":
                sell_signals += 1

            results_by_tf[tf] = {
                "direction": result.direction,
                "status": result.status,
                "trigger_price": result.trigger_price,
                "stop_loss": result.stop_loss,
                "target_price": result.target_price,
                "risk_reward": result.risk_reward,
                "rsi_value": result.rsi_value,
                "kelly_pct": result.kelly_pct,
                "reason": result.reason,
            }
        except Exception as e:
            results_by_tf[tf] = {"status": "error", "error": str(e)}

    confluence_score = 0
    if total_tf > 0:
        max_agreement = max(buy_signals, sell_signals)
        confluence_score = round((max_agreement / total_tf) * 100, 1)

    consensus_direction = None
    if buy_signals > sell_signals:
        consensus_direction = "BUY"
    elif sell_signals > buy_signals:
        consensus_direction = "SELL"

    return {
        "ticker": req.ticker,
        "timeframes": results_by_tf,
        "confluence_score": confluence_score,
        "consensus_direction": consensus_direction,
        "buy_signals": buy_signals,
        "sell_signals": sell_signals,
        "total_timeframes_analyzed": total_tf,
    }


# --- Phase 5: Backtesting ---

@app.post("/api/backtest")
async def backtest(req: BacktestRequest):
    """Run a historical backtest for a ticker using the signal model."""
    df = await fetch_candles_from_service_a(req.ticker)
    if df.empty or len(df) < 201:
        raise HTTPException(400, f"Insufficient data for {req.ticker} (need 201+ candles)")

    period_map = {"1mo": 21, "3mo": 63, "6mo": 126, "1y": 252}
    lookback = period_map.get(req.period, 126)
    start_idx = max(201, len(df) - lookback)

    trades = []
    current_position = None

    for i in range(start_idx, len(df)):
        window = df.iloc[:i + 1].copy()
        result = evaluate_signals(window, req.available_capital)
        row = df.iloc[i]
        price = row["close"]
        date_str = str(row.get("timestamp", row.get("date", i)))

        if current_position:
            pos = current_position
            if pos["direction"] == "BUY":
                if price <= pos["stop_loss"]:
                    pnl_pct = ((price - pos["entry"]) / pos["entry"]) * 100
                    trades.append({**pos, "exit": price, "exit_date": date_str, "pnl_pct": round(pnl_pct, 2), "outcome": "stop_loss"})
                    current_position = None
                elif price >= pos["target"]:
                    pnl_pct = ((price - pos["entry"]) / pos["entry"]) * 100
                    trades.append({**pos, "exit": price, "exit_date": date_str, "pnl_pct": round(pnl_pct, 2), "outcome": "target_hit"})
                    current_position = None
            elif pos["direction"] == "SELL":
                if price >= pos["stop_loss"]:
                    pnl_pct = ((pos["entry"] - price) / pos["entry"]) * 100
                    trades.append({**pos, "exit": price, "exit_date": date_str, "pnl_pct": round(pnl_pct, 2), "outcome": "stop_loss"})
                    current_position = None
                elif price <= pos["target"]:
                    pnl_pct = ((pos["entry"] - price) / pos["entry"]) * 100
                    trades.append({**pos, "exit": price, "exit_date": date_str, "pnl_pct": round(pnl_pct, 2), "outcome": "target_hit"})
                    current_position = None

        if current_position is None and result is not None and not result.suppressed:
            current_position = {
                "direction": result.direction,
                "entry": price,
                "entry_date": date_str,
                "stop_loss": result.stop_loss,
                "target": result.target_price,
                "reason": result.reason,
            }

    # Close any remaining open position at last price
    if current_position:
        last_price = df.iloc[-1]["close"]
        last_date = str(df.iloc[-1].get("timestamp", df.iloc[-1].get("date", len(df) - 1)))
        pos = current_position
        if pos["direction"] == "BUY":
            pnl_pct = ((last_price - pos["entry"]) / pos["entry"]) * 100
        else:
            pnl_pct = ((pos["entry"] - last_price) / pos["entry"]) * 100
        trades.append({**pos, "exit": last_price, "exit_date": last_date, "pnl_pct": round(pnl_pct, 2), "outcome": "still_open"})

    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    total = len(trades)
    win_rate = (len(wins) / total * 100) if total > 0 else 0
    avg_pnl = sum(t["pnl_pct"] for t in trades) / total if total > 0 else 0

    # Equity curve
    equity = req.available_capital
    equity_curve = [{"date": "start", "equity": equity}]
    max_equity = equity
    max_dd = 0
    for t in trades:
        equity *= (1 + t["pnl_pct"] / 100)
        equity_curve.append({"date": t.get("exit_date", ""), "equity": round(equity, 2)})
        max_equity = max(max_equity, equity)
        dd = ((max_equity - equity) / max_equity) * 100
        max_dd = max(max_dd, dd)

    return {
        "ticker": req.ticker,
        "period": req.period,
        "total_signals": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "avg_pnl_pct": round(avg_pnl, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "final_equity": round(equity, 2),
        "total_return_pct": round(((equity - req.available_capital) / req.available_capital) * 100, 2),
        "trades": trades,
        "equity_curve": equity_curve,
    }
