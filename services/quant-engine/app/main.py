"""Service B — Quantitative Analytics & Risk Engine."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional

import httpx
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.backtest_store import BacktestStore
from app.backtesting import (
    ExecutionCosts,
    StrategyParameters,
    ValidationThresholds,
    WindowConfiguration,
    run_walk_forward_backtest,
)
from app.config import settings
from app.opportunities import OpportunityInput, build_blocked_opportunity, build_opportunity
from app.regimes import (
    classify_breadth,
    classify_regime,
    regime_controls,
    timeframe_confluence,
)
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
_opportunity_actions: dict[str, dict[str, object]] = {}
_backtest_store: BacktestStore | None = None


def _get_backtest_store() -> BacktestStore:
    global _backtest_store
    if _backtest_store is None:
        _backtest_store = BacktestStore(settings.BACKTEST_DATABASE_PATH)
    return _backtest_store


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
    market_regime: dict[str, object]
    timeframe_agreement: dict[str, object]
    regime_controls: dict[str, object]


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
    timeframes: list[str] = Field(default_factory=lambda: ["1d", "4h", "1h"])


class DataQualityResponse(BaseModel):
    ticker: str
    asset_type: str
    interval: str
    status: str
    is_eligible: bool
    candle_count: int
    latest_timestamp: Optional[str]
    age_hours: Optional[float]
    stale: bool
    duplicate_timestamps: int
    missing_periods: int
    invalid_timestamps: int
    invalid_ohlc: int
    anomaly_count: int
    issues: list[str]


class ScanConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = None
    market_hours_only: Optional[bool] = None


class TradePlanEdit(BaseModel):
    entry_zone_low: Optional[float] = Field(default=None, gt=0)
    entry_zone_high: Optional[float] = Field(default=None, gt=0)
    stop_loss: Optional[float] = Field(default=None, gt=0)
    targets: Optional[list[float]] = Field(default=None, min_length=1, max_length=4)
    quantity: Optional[float] = Field(default=None, gt=0)
    time_stop: Optional[str] = Field(default=None, min_length=1, max_length=100)


class OpportunityActionRequest(BaseModel):
    action: Literal["approve", "reject", "snooze", "edit"]
    snooze_minutes: int = Field(default=60, ge=5, le=10_080)
    edit: Optional[TradePlanEdit] = None


class BacktestStrategyParameters(BaseModel):
    risk_reward_ratio: float = Field(default=3.0, gt=0)
    atr_stop_multiplier: float = Field(default=1.5, gt=0)
    volatility_threshold: float = Field(default=2.0, gt=0)


class BacktestExecutionCosts(BaseModel):
    commission_bps: float = Field(default=2.0, ge=0)
    spread_bps: float = Field(default=4.0, ge=0)
    slippage_bps: float = Field(default=3.0, ge=0)
    fill_delay_bars: int = Field(default=1, ge=1, le=10)


class BacktestWindowConfiguration(BaseModel):
    warmup_bars: int = Field(default=201, ge=201)
    train_bars: int = Field(default=60, ge=10)
    validation_bars: int = Field(default=20, ge=5)
    test_bars: int = Field(default=20, ge=5)
    step_bars: int = Field(default=20, ge=5)


class BacktestValidationThresholds(BaseModel):
    minimum_trades: int = Field(default=3, ge=0)
    minimum_after_cost_return_pct: float = 0.0
    minimum_sharpe: float = 0.0
    minimum_profit_factor: float = Field(default=1.0, ge=0)
    maximum_drawdown_pct: float = Field(default=25.0, ge=0)


class BacktestRequest(BaseModel):
    ticker: str
    asset_type: str = "stock"
    period: str = "max"
    available_capital: float = Field(default=10_000.0, gt=0)
    strategy_version: str = Field(default="ema-rsi-atr-v1", min_length=1, max_length=100)
    parameter_grid: list[BacktestStrategyParameters] = Field(default_factory=lambda: [
        BacktestStrategyParameters(risk_reward_ratio=2.0, atr_stop_multiplier=1.0),
        BacktestStrategyParameters(risk_reward_ratio=2.0, atr_stop_multiplier=1.5),
        BacktestStrategyParameters(risk_reward_ratio=3.0, atr_stop_multiplier=1.0),
        BacktestStrategyParameters(risk_reward_ratio=3.0, atr_stop_multiplier=1.5),
    ], min_length=1, max_length=20)
    costs: BacktestExecutionCosts = Field(default_factory=BacktestExecutionCosts)
    windows: BacktestWindowConfiguration = Field(default_factory=BacktestWindowConfiguration)
    thresholds: BacktestValidationThresholds = Field(default_factory=BacktestValidationThresholds)
    benchmark_tickers: list[str] = Field(default_factory=list, max_length=5)


async def fetch_candles_from_service_a(ticker: str, interval: str = "1d") -> pd.DataFrame:
    url = f"{settings.DATA_INGESTION_URL}/api/candles/{ticker}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params={"interval": interval}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    if not data:
        return pd.DataFrame()
    return pd.DataFrame(data)


async def _fetch_timeframe_frames(
    ticker: str,
    timeframes: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    requested = list(dict.fromkeys(timeframes or ["1d", "4h", "1h"]))

    async def fetch_one(timeframe: str) -> tuple[str, pd.DataFrame]:
        try:
            return timeframe, await fetch_candles_from_service_a(ticker, timeframe)
        except Exception as error:
            logger.warning(f"{timeframe} candles unavailable for {ticker}: {error}")
            return timeframe, pd.DataFrame()

    return dict(await asyncio.gather(*(fetch_one(timeframe) for timeframe in requested)))


def _regime_analysis(
    asset_type: str,
    direction: str,
    frames: dict[str, pd.DataFrame],
    breadth_pct_above_50: float | None = None,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    snapshot = classify_regime(
        frames.get("1d", pd.DataFrame()),
        asset_type,
        breadth_pct_above_50,
    )
    confluence = timeframe_confluence(frames, direction, asset_type)
    controls = regime_controls(snapshot, direction, confluence)
    return snapshot.to_dict(), confluence, controls


async def _fetch_assets() -> list[dict]:
    url = f"{settings.DATA_INGESTION_URL}/api/assets"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=15)
        resp.raise_for_status()
    return resp.json()


async def _fetch_data_quality(ticker: str, interval: str = "1d") -> DataQualityResponse:
    url = f"{settings.DATA_INGESTION_URL}/api/data-quality/{ticker}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params={"interval": interval}, timeout=15)
        resp.raise_for_status()
    return DataQualityResponse.model_validate(resp.json())


async def _fetch_all_data_quality(interval: str = "1d") -> dict[str, DataQualityResponse]:
    url = f"{settings.DATA_INGESTION_URL}/api/data-quality"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params={"interval": interval}, timeout=30)
        resp.raise_for_status()
    return {
        ticker: DataQualityResponse.model_validate(report)
        for ticker, report in resp.json().items()
    }


def _quality_rejection_reason(quality: DataQualityResponse) -> str:
    if quality.issues:
        return "; ".join(quality.issues)
    return f"Data quality status is {quality.status}"


async def _require_eligible_data(ticker: str, interval: str = "1d") -> DataQualityResponse:
    try:
        quality = await _fetch_data_quality(ticker, interval)
    except Exception as e:
        logger.error(f"Data quality unavailable for {ticker}: {e}")
        raise HTTPException(503, f"Data quality unavailable for {ticker}") from e
    if not quality.is_eligible:
        raise HTTPException(
            422, f"Data quality rejected for {ticker}: {_quality_rejection_reason(quality)}"
        )
    return quality


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


async def _fetch_upcoming_earnings() -> dict[str, dict[str, object]]:
    url = f"{settings.DATA_INGESTION_URL}/api/earnings/upcoming/all"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=30)
            response.raise_for_status()
        payload = response.json()
    except Exception as error:
        logger.warning(f"Earnings calendar unavailable for scan: {error}")
        return {}
    if not isinstance(payload, list):
        return {}
    results: dict[str, dict[str, object]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker", "")).strip().upper()
        if ticker:
            results[ticker] = item
    return results


def _latest_backtest_result(ticker: str) -> dict[str, object] | None:
    try:
        return _get_backtest_store().latest(ticker)
    except Exception as error:
        logger.warning(f"Backtest history unavailable for {ticker}: {error}")
        return None


def _apply_opportunity_state(opportunity: dict[str, object], now: datetime) -> None:
    opportunity_id = str(opportunity.get("id", ""))
    state = _opportunity_actions.get(opportunity_id)
    if state is None:
        return
    decision = str(state.get("user_decision", "pending"))
    snoozed_until = state.get("snoozed_until")
    if decision == "snoozed" and isinstance(snoozed_until, str):
        try:
            if datetime.fromisoformat(snoozed_until) <= now:
                _opportunity_actions.pop(opportunity_id, None)
                return
        except ValueError:
            _opportunity_actions.pop(opportunity_id, None)
            return
    opportunity.update(state)


def _notification_allowed(opportunity: dict[str, object]) -> bool:
    if not bool(opportunity.get("eligible", False)):
        return False
    return str(opportunity.get("user_decision", "pending")) not in {"rejected", "snoozed"}


def _edit_trade_plan(plan: dict[str, object], edit: TradePlanEdit) -> None:
    entry_zone = plan.get("entry_zone")
    if not isinstance(entry_zone, dict):
        entry_zone = {}
        plan["entry_zone"] = entry_zone
    if edit.entry_zone_low is not None:
        entry_zone["low"] = edit.entry_zone_low
    if edit.entry_zone_high is not None:
        entry_zone["high"] = edit.entry_zone_high
    low = float(entry_zone.get("low", 0.0))
    high = float(entry_zone.get("high", 0.0))
    if low <= 0 or high <= 0 or low > high:
        raise HTTPException(400, "Entry zone must contain positive prices with low <= high")
    if edit.stop_loss is not None:
        plan["stop_loss"] = edit.stop_loss
    if edit.quantity is not None:
        plan["quantity"] = edit.quantity
    if edit.time_stop is not None:
        plan["time_stop"] = edit.time_stop
    if edit.targets is not None:
        if any(target <= 0 for target in edit.targets):
            raise HTTPException(400, "Targets must contain positive prices")
        plan["targets"] = [
            {
                "price": target,
                "exit_pct": round(100 / len(edit.targets), 2),
                "label": f"Target {index + 1}",
            }
            for index, target in enumerate(edit.targets)
        ]
    average_entry = (low + high) / 2
    stop_loss = float(plan.get("stop_loss", 0.0))
    quantity = float(plan.get("quantity", 0.0))
    plan["position_size_usd"] = round(average_entry * quantity, 2)
    plan["maximum_planned_loss_usd"] = round(abs(average_entry - stop_loss) * quantity, 2)


async def _run_scan() -> dict:
    """Scan all active assets for signals and send notifications."""
    now = datetime.now(timezone.utc)
    _scan_state["last_scan_at"] = now.isoformat()

    assets = await _fetch_assets()
    try:
        quality_by_ticker = await _fetch_all_data_quality()
        quality_service_error = None
    except Exception as e:
        logger.error(f"Data quality unavailable for scan: {e}")
        quality_by_ticker = {}
        quality_service_error = "Data quality service unavailable"
    earnings_by_ticker = await _fetch_upcoming_earnings()
    scanned = 0
    signals_found = 0
    notifications_sent = 0
    errors = 0
    quality_rejections = []
    signal_details = []

    eligible_assets = [
        asset for asset in assets
        if asset.get("is_active", True)
        and (quality_by_ticker.get(str(asset.get("ticker"))) is not None)
        and bool(quality_by_ticker[str(asset.get("ticker"))].is_eligible)
    ]

    async def fetch_daily(asset: dict[str, object]) -> tuple[str, pd.DataFrame]:
        ticker = str(asset.get("ticker", ""))
        try:
            return ticker, await fetch_candles_from_service_a(ticker)
        except Exception as error:
            logger.warning(f"Daily candles unavailable for {ticker}: {error}")
            return ticker, pd.DataFrame()

    daily_frames = dict(await asyncio.gather(*(fetch_daily(asset) for asset in eligible_assets)))
    breadth_by_asset_type: dict[str, dict[str, object]] = {}
    for asset_type in ("stock", "crypto"):
        matching_frames = [
            daily_frames.get(str(asset.get("ticker")), pd.DataFrame())
            for asset in eligible_assets
            if str(asset.get("asset_type", "stock")) == asset_type
        ]
        breadth_by_asset_type[asset_type] = classify_breadth(matching_frames)

    for asset in assets:
        if not asset.get("is_active", True):
            continue
        ticker = asset["ticker"]
        asset_type = asset.get("asset_type", "stock")
        quality = quality_by_ticker.get(ticker)
        if quality is None or not quality.is_eligible:
            reason = quality_service_error or (
                _quality_rejection_reason(quality) if quality else "Data quality result missing"
            )
            quality_rejections.append({"ticker": ticker, "reason": reason})
            blocked = build_blocked_opportunity(ticker, asset_type, reason, now)
            _apply_opportunity_state(blocked, now)
            signal_details.append({
                "ticker": ticker,
                "direction": "NONE",
                "status": "Data Blocked",
                "approved": False,
                "suppressed": True,
                "action": "blocked",
                "reason": reason,
                "recommended_size_usd": 0.0,
                "score": 0.0,
                "eligible": False,
                "opportunity": blocked,
            })
            logger.warning(f"Scan skipped {ticker}: {reason}")
            continue
        try:
            df = daily_frames.get(ticker, pd.DataFrame())
            if df.empty or len(df) < 201:
                continue
            scanned += 1
            result = evaluate_signals(df, 10_000.0)
            if result is None:
                continue

            signals_found += 1
            intraday_frames = await _fetch_timeframe_frames(ticker, ["4h", "1h"])
            frames = {"1d": df, **intraday_frames}
            breadth = breadth_by_asset_type.get(asset_type, {})
            breadth_pct_value = breadth.get("pct_above_50")
            breadth_pct = float(breadth_pct_value) if isinstance(breadth_pct_value, (int, float)) else None
            market_regime, timeframe_agreement, controls = _regime_analysis(
                asset_type,
                str(result.direction) if result.direction else "NONE",
                frames,
                breadth_pct,
            )
            adjusted_size = float(result.optimal_size_usd) * float(controls["size_multiplier"])
            signal_suppressed = bool(result.suppressed) or not bool(controls["allowed"])
            regime_reason = (
                f"{result.reason}. Regime: {market_regime['label']}. "
                f"Timeframe agreement: {float(timeframe_agreement['score']):.1f}%"
            )
            dedup_key = f"{ticker}:{result.direction}"
            last_signal = _recent_signals.get(dedup_key)
            deduplicated = False
            if last_signal:
                last_time = datetime.fromisoformat(last_signal)
                deduplicated = (now - last_time) < timedelta(hours=24)
            if not deduplicated:
                _recent_signals[dedup_key] = now.isoformat()

            signal_data = {
                "ticker": ticker,
                "direction": str(result.direction) if result.direction else "NONE",
                "status": str(result.status),
                "trigger_price": float(result.trigger_price),
                "stop_loss": float(result.stop_loss),
                "target_price": float(result.target_price),
                "reason": regime_reason,
                "risk_reward": float(result.risk_reward),
                "atr_value": float(result.atr_value),
                "rsi_value": float(result.rsi_value),
                "suppressed": signal_suppressed,
                "kelly_pct": float(result.kelly_pct),
                "optimal_size_usd": adjusted_size,
                "volatility_scalar": float(result.volatility_scalar),
                "asset_type": asset_type,
                "market_regime": market_regime,
                "timeframe_agreement": timeframe_agreement,
                "regime_controls": controls,
            }

            decision = await _process_signal_via_portfolio(signal_data)
            approved = bool(decision and decision.get("approved", False))
            risk_decision_value = decision.get("risk_decision") if decision else None
            risk_decision = risk_decision_value if isinstance(risk_decision_value, dict) else None
            decision_action = str(risk_decision.get("action", "rejected")) if risk_decision else "unavailable"
            decision_reason = (
                str(decision.get("reason", "Portfolio risk decision unavailable"))
                if decision else "Portfolio risk decision unavailable"
            )
            if risk_decision is not None:
                risk_decision["summary"] = decision_reason
            quality_payload = quality.model_dump()
            opportunity = build_opportunity(OpportunityInput(
                ticker=ticker,
                asset_type=asset_type,
                direction=str(result.direction) if result.direction else "NONE",
                status=str(result.status),
                trigger_price=float(result.trigger_price),
                stop_loss=float(result.stop_loss),
                target_price=float(result.target_price),
                signal_reason=regime_reason,
                risk_reward=float(result.risk_reward),
                atr_value=float(result.atr_value),
                suppressed=signal_suppressed,
                quality=quality_payload,
                candles=df,
                risk_decision=risk_decision,
                backtest=_latest_backtest_result(ticker),
                earnings=earnings_by_ticker.get(ticker),
                regime=market_regime,
                timeframe_agreement=timeframe_agreement,
                regime_controls=controls,
                evaluated_at=now,
            ))
            _apply_opportunity_state(opportunity, now)
            trade_plan_value = opportunity.get("trade_plan")
            trade_plan = trade_plan_value if isinstance(trade_plan_value, dict) else {}
            recommended_size = float(trade_plan.get("position_size_usd", 0.0))
            eligibility_reasons_value = opportunity.get("eligibility_reasons")
            eligibility_reasons = (
                [str(reason) for reason in eligibility_reasons_value]
                if isinstance(eligibility_reasons_value, list) else []
            )
            opportunity_reason = (
                decision_reason
                if decision is not None and not approved
                else decision_reason
                if bool(opportunity.get("eligible"))
                else "; ".join(eligibility_reasons)
            )
            action = decision_action if bool(opportunity.get("eligible")) else "ineligible"

            if not deduplicated and _notification_allowed(opportunity):
                notify_data = {
                    "ticker": ticker,
                    "direction": str(result.direction) if result.direction else "NONE",
                    "status": str(result.status),
                    "trigger_price": float(result.trigger_price),
                    "target_price": float(result.target_price),
                    "stop_loss": float(result.stop_loss),
                    "optimal_size_usd": recommended_size,
                    "kelly_pct": float(result.kelly_pct),
                    "opportunity_score": float(opportunity.get("score", 0.0)),
                    "trade_plan": trade_plan,
                    "market_regime": market_regime,
                    "timeframe_agreement": timeframe_agreement,
                    "paper_trade_executed": False,
                }
                sent = await _send_notification(notify_data)
                if sent:
                    notifications_sent += 1

            signal_details.append({
                "ticker": ticker,
                "direction": str(result.direction) if result.direction else "NONE",
                "status": str(result.status),
                "approved": approved,
                "suppressed": signal_suppressed,
                "action": action,
                "reason": opportunity_reason,
                "recommended_size_usd": recommended_size,
                "score": float(opportunity.get("score", 0.0)),
                "eligible": bool(opportunity.get("eligible", False)),
                "market_regime": market_regime,
                "timeframe_agreement": timeframe_agreement,
                "regime_controls": controls,
                "opportunity": opportunity,
            })

        except Exception as e:
            errors += 1
            logger.error(f"Scan error for {ticker}: {e}")

    signal_details.sort(key=lambda item: (-float(item["score"]), str(item["ticker"])))
    result_summary = {
        "scanned": scanned,
        "signals_found": signals_found,
        "notifications_sent": notifications_sent,
        "errors": errors,
        "quality_rejected": len(quality_rejections),
        "quality_rejections": quality_rejections,
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
    await _require_eligible_data(req.ticker)
    frames = await _fetch_timeframe_frames(req.ticker, req.timeframes)
    df = frames.get("1d", pd.DataFrame())
    if df.empty or len(df) < 201:
        raise HTTPException(400, f"Insufficient data for {req.ticker} (need 201+ daily candles)")

    result = evaluate_signals(df, req.available_capital)
    if result is None:
        return None

    market_regime, timeframe_agreement, controls = _regime_analysis(
        req.asset_type,
        str(result.direction) if result.direction else "NONE",
        frames,
    )
    reason = (
        f"{result.reason}. Regime: {market_regime['label']}. "
        f"Timeframe agreement: {float(timeframe_agreement['score']):.1f}%"
    )
    return SignalResponse(
        ticker=req.ticker,
        direction=result.direction,
        status=result.status,
        trigger_price=result.trigger_price,
        stop_loss=result.stop_loss,
        target_price=result.target_price,
        reason=reason,
        risk_reward=result.risk_reward,
        atr_value=result.atr_value,
        rsi_value=result.rsi_value,
        suppressed=result.suppressed or not bool(controls["allowed"]),
        kelly_pct=result.kelly_pct,
        optimal_size_usd=result.optimal_size_usd * float(controls["size_multiplier"]),
        volatility_scalar=result.volatility_scalar,
        asset_type=req.asset_type,
        market_regime=market_regime,
        timeframe_agreement=timeframe_agreement,
        regime_controls=controls,
    )


@app.get("/api/regime/{ticker}")
async def get_regime(
    ticker: str,
    asset_type: str = "stock",
    direction: Literal["BUY", "SELL"] = "BUY",
):
    normalized = ticker.strip().upper()
    await _require_eligible_data(normalized)
    frames = await _fetch_timeframe_frames(normalized)
    market_regime, timeframe_agreement, controls = _regime_analysis(
        asset_type,
        direction,
        frames,
    )
    return {
        "ticker": normalized,
        "direction": direction,
        "market_regime": market_regime,
        "timeframe_agreement": timeframe_agreement,
        "regime_controls": controls,
    }


@app.post("/api/risk-profile", response_model=Optional[RiskProfileResponse])
async def risk_profile(req: AnalyzeRequest):
    await _require_eligible_data(req.ticker)
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


@app.post("/api/opportunities/{opportunity_id}/action")
async def update_opportunity_action(
    opportunity_id: str,
    request: OpportunityActionRequest,
):
    latest_result = _scan_state.get("last_scan_result")
    if not isinstance(latest_result, dict):
        raise HTTPException(404, "Run a scan before managing opportunities")
    signals = latest_result.get("signals")
    if not isinstance(signals, list):
        raise HTTPException(404, "No opportunities are available")
    opportunity: dict[str, object] | None = None
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        candidate = signal.get("opportunity")
        if isinstance(candidate, dict) and str(candidate.get("id")) == opportunity_id:
            opportunity = candidate
            break
    if opportunity is None:
        raise HTTPException(404, "Opportunity not found in the latest scan")
    if request.action == "approve" and not bool(opportunity.get("eligible", False)):
        raise HTTPException(409, "Ineligible opportunities cannot be approved")

    existing = _opportunity_actions.get(opportunity_id, {})
    state = dict(existing)
    if request.action == "approve":
        state.update({"user_decision": "approved", "snoozed_until": None})
    elif request.action == "reject":
        state.update({"user_decision": "rejected", "snoozed_until": None})
    elif request.action == "snooze":
        snoozed_until = datetime.now(timezone.utc) + timedelta(minutes=request.snooze_minutes)
        state.update({
            "user_decision": "snoozed",
            "snoozed_until": snoozed_until.isoformat(),
        })
    else:
        if request.edit is None:
            raise HTTPException(400, "Edit details are required")
        plan = opportunity.get("trade_plan")
        if not isinstance(plan, dict):
            raise HTTPException(409, "This opportunity has no editable trade plan")
        _edit_trade_plan(plan, request.edit)
        state.update({
            "user_decision": "edited",
            "snoozed_until": None,
            "trade_plan": plan,
        })

    _opportunity_actions[opportunity_id] = state
    opportunity.update(state)
    return opportunity


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
    """Analyze a ticker across daily, 4h, and 1h frames."""
    await _require_eligible_data(req.ticker)
    frames = await _fetch_timeframe_frames(req.ticker, req.timeframes)
    results_by_tf: dict[str, dict[str, object]] = {}
    buy_signals = 0
    sell_signals = 0
    for timeframe in req.timeframes:
        frame = frames.get(timeframe, pd.DataFrame())
        if frame.empty or len(frame) < 201:
            results_by_tf[timeframe] = {
                "status": "insufficient_data",
                "candles": len(frame),
            }
            continue
        result = evaluate_signals(frame, req.available_capital)
        if result is None:
            results_by_tf[timeframe] = {"status": "no_signal", "candles": len(frame)}
            continue
        if result.direction == "BUY":
            buy_signals += 1
        elif result.direction == "SELL":
            sell_signals += 1
        results_by_tf[timeframe] = {
            "direction": result.direction,
            "status": result.status,
            "trigger_price": result.trigger_price,
            "stop_loss": result.stop_loss,
            "target_price": result.target_price,
            "risk_reward": result.risk_reward,
            "rsi_value": result.rsi_value,
            "kelly_pct": result.kelly_pct,
            "reason": result.reason,
            "candles": len(frame),
        }

    consensus_direction: str | None = None
    if buy_signals > sell_signals:
        consensus_direction = "BUY"
    elif sell_signals > buy_signals:
        consensus_direction = "SELL"
    analysis_direction = consensus_direction or "BUY"
    market_regime, timeframe_agreement, controls = _regime_analysis(
        req.asset_type,
        analysis_direction,
        frames,
    )
    return {
        "ticker": req.ticker,
        "asset_type": req.asset_type,
        "timeframes": results_by_tf,
        "confluence_score": timeframe_agreement["score"],
        "consensus_direction": consensus_direction,
        "buy_signals": buy_signals,
        "sell_signals": sell_signals,
        "total_timeframes_analyzed": timeframe_agreement["available_timeframes"],
        "market_regime": market_regime,
        "timeframe_agreement": timeframe_agreement,
        "regime_controls": controls,
    }


# --- Phase 5: Backtesting ---


@app.post("/api/backtest")
async def backtest(req: BacktestRequest):
    """Run and persist a leakage-safe walk-forward validation."""
    ticker = req.ticker.strip().upper()
    await _require_eligible_data(ticker)
    frame = await fetch_candles_from_service_a(ticker)
    period_map = {"6mo": 126, "1y": 252, "2y": 504, "3y": 756}
    if req.period != "max":
        if req.period not in period_map:
            raise HTTPException(400, "period must be one of: 6mo, 1y, 2y, 3y, max")
        frame = frame.tail(period_map[req.period]).reset_index(drop=True)

    benchmark_frames: dict[str, pd.DataFrame] = {}
    for symbol in dict.fromkeys(item.strip().upper() for item in req.benchmark_tickers):
        if not symbol or symbol == ticker:
            continue
        try:
            benchmark = await fetch_candles_from_service_a(symbol)
            if not benchmark.empty:
                benchmark_frames[symbol] = benchmark
        except Exception as error:
            logger.warning(f"Benchmark data unavailable for {symbol}: {error}")

    parameters = [
        StrategyParameters(
            risk_reward_ratio=item.risk_reward_ratio,
            atr_stop_multiplier=item.atr_stop_multiplier,
            volatility_threshold=item.volatility_threshold,
        )
        for item in req.parameter_grid
    ]
    costs = ExecutionCosts(**req.costs.model_dump())
    windows = WindowConfiguration(**req.windows.model_dump())
    thresholds = ValidationThresholds(**req.thresholds.model_dump())
    try:
        result = run_walk_forward_backtest(
            ticker=ticker,
            candles=frame,
            strategy_version=req.strategy_version,
            parameters=parameters,
            costs=costs,
            windows=windows,
            thresholds=thresholds,
            initial_capital=req.available_capital,
            benchmark_frames=benchmark_frames,
            asset_type=req.asset_type,
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    store = _get_backtest_store()
    run_id = store.save(
        ticker=ticker,
        strategy_version=req.strategy_version,
        request=req.model_dump(mode="json"),
        result=result,
    )
    stored = store.get(run_id)
    if stored is None:
        raise HTTPException(500, "Backtest run could not be persisted")
    return stored


@app.get("/api/backtest/runs")
async def list_backtest_runs(ticker: Optional[str] = None, limit: int = 20):
    if limit < 1 or limit > 100:
        raise HTTPException(400, "limit must be between 1 and 100")
    normalized = ticker.strip().upper() if ticker else None
    return _get_backtest_store().list(normalized, limit)


@app.get("/api/backtest/runs/{run_id}")
async def get_backtest_run(run_id: str):
    result = _get_backtest_store().get(run_id)
    if result is None:
        raise HTTPException(404, "Backtest run not found")
    return result
