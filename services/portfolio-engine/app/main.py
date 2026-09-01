"""Service C — Portfolio & Integration Engine."""

import asyncio
import base64
import datetime
import json
import logging
import os
import stat
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, desc, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, init_db, async_session
from app.models import (
    Trade, TradeStatus, SignalDirection, Portfolio, EquitySnapshot, AlertLog, CredentialSecret, User,
    TradeExecution, TradeJournal, ExecutionKind, PaperOrder, PaperOrderEvent, PaperOrderFill,
    ActionItem, DashboardPreference, LiveTradingControl, LiveOrder, LiveOrderFill,
    LiveExecutionAudit, _utc_now,
)
from app import action_items as actions
from app import attribution as attribution_math
from app import paper_orders as paper
from app import live_execution as live
from app.brokers import AlpacaBroker, BrokerAdapter, BrokerError
from app.auth import create_token, hash_password, verify_password, get_current_user
from app.risk_engine import (
    ClosedTradeResult,
    PositionInput,
    RiskLimits,
    annualized_volatility,
    calculate_portfolio_risk,
    calculate_return_series,
    calculate_trade_pnl,
    evaluate_breakers,
    evaluate_proposed_position,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TradeResponse(BaseModel):
    id: int
    ticker: str
    direction: str
    entry_price: float
    exit_price: Optional[float]
    quantity: float
    stop_loss: float
    target_price: float
    trailing_stop: Optional[float]
    asset_type: str
    sector: str
    market_regime: str
    volatility_regime: str
    breadth_regime: str
    risk_regime: str
    regime_label: str
    timeframe_agreement: Optional[float]
    strategy_name: Optional[str]
    strategy_version: Optional[str]
    timeframe: Optional[str]
    signal_confidence: Optional[float]
    planned_entry_price: Optional[float]
    planned_exit_price: Optional[float]
    planned_quantity: Optional[float]
    entry_fees: float
    entry_slippage: float
    exit_fees_total: float
    exit_slippage_total: float
    costs_total: float
    realized_quantity: float
    remaining_quantity: float
    gross_pnl: Optional[float]
    mfe_usd: Optional[float]
    mae_usd: Optional[float]
    mfe_pct: Optional[float]
    mae_pct: Optional[float]
    excursion_status: str
    status: str
    pnl: Optional[float]
    pnl_pct: Optional[float]
    opened_at: Optional[datetime.datetime]
    closed_at: Optional[datetime.datetime]
    model_config = {"from_attributes": True}


class PortfolioResponse(BaseModel):
    balance: float
    equity: float
    total_pnl: float
    win_count: int
    loss_count: int
    win_rate: float
    max_drawdown: float
    profit_factor: float
    peak_equity: float
    open_positions: int
    equity_curve: list[dict]


class SignalInput(BaseModel):
    ticker: str
    direction: str
    status: str
    trigger_price: float
    stop_loss: float
    target_price: float
    reason: str
    risk_reward: float
    atr_value: float
    rsi_value: float
    suppressed: bool
    kelly_pct: float
    optimal_size_usd: float
    volatility_scalar: float
    asset_type: str = "stock"
    sector: str = "Unclassified"
    market_regime: dict[str, object] = Field(default_factory=dict)
    timeframe_agreement: dict[str, object] = Field(default_factory=dict)
    regime_controls: dict[str, object] = Field(default_factory=dict)


class SignalDecision(BaseModel):
    ticker: str
    direction: str
    status: str
    approved: bool
    trigger_price: float
    stop_loss: float
    target_price: float
    optimal_size_usd: float
    kelly_pct: float
    capital_overspend: bool
    reason: str
    paper_trade_executed: bool
    risk_decision: dict[str, object]


class AlertLogResponse(BaseModel):
    id: int
    ticker: str
    direction: str
    status: str
    trigger_price: float
    stop_loss: Optional[float]
    target_price: Optional[float]
    optimal_size_usd: Optional[float]
    kelly_pct: Optional[float]
    capital_overspend: bool
    approved: bool
    message: Optional[str]
    risk_decision_json: Optional[str]
    market_regime: str
    volatility_regime: str
    breadth_regime: str
    risk_regime: str
    regime_label: str
    timeframe_agreement: Optional[float]
    created_at: Optional[datetime.datetime]
    model_config = {"from_attributes": True}


class CredentialSaveRequest(BaseModel):
    credentials: dict[str, str]
    overwrite: bool = False


class CredentialRevealRequest(BaseModel):
    key: str


POSITION_SIZE_PCT = 0.02

PROVIDER_KEYS = {
    "binance": ["BINANCE_API_KEY", "BINANCE_API_SECRET"],
    "alpaca": ["ALPACA_API_KEY", "ALPACA_API_SECRET"],
    "telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
    "discord": ["DISCORD_WEBHOOK_URL"],
}

LOSS_TOLERANCE_KEY = "LOSS_TOLERANCE_PCT"


def _provider_for_key(key: str) -> str:
    for provider, keys in PROVIDER_KEYS.items():
        if key in keys:
            return provider
    return "unknown"


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "•" * len(value)
    return f"{value[:3]}{'•' * 6}{value[-3:]}"


def _encode_secret(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode()


def _decode_secret(value: str) -> str:
    try:
        return base64.urlsafe_b64decode(value.encode()).decode()
    except Exception:
        return value


async def _get_secret(db: AsyncSession, key: str) -> Optional[CredentialSecret]:
    result = await db.execute(select(CredentialSecret).where(CredentialSecret.key == key))
    return result.scalar_one_or_none()


async def _save_secret(
    db: AsyncSession, key: str, value: str, verified: bool, last_error: Optional[str] = None,
    overwrite: bool = False,
) -> bool:
    existing = await _get_secret(db, key)
    if existing and existing.verified and not overwrite:
        return False
    if existing is None:
        existing = CredentialSecret(provider=_provider_for_key(key), key=key, value=_encode_secret(value))
        db.add(existing)
    else:
        existing.value = _encode_secret(value)
    existing.verified = verified
    existing.last_error = last_error
    return True


async def get_or_create_portfolio(db: AsyncSession) -> Portfolio:
    result = await db.execute(select(Portfolio).limit(1))
    portfolio = result.scalar_one_or_none()
    if portfolio is None:
        portfolio = Portfolio(
            balance=settings.INITIAL_BALANCE,
            equity=settings.INITIAL_BALANCE,
            peak_equity=settings.INITIAL_BALANCE,
        )
        db.add(portfolio)
        await db.commit()
        await db.refresh(portfolio)
    return portfolio


def _risk_limits() -> RiskLimits:
    env = _read_env(_find_env_path())

    def percentage_value(key: str, default: float) -> float:
        try:
            candidate = float(env.get(key, default))
        except (TypeError, ValueError):
            return default
        return candidate if 0 < candidate <= 1 else default

    return RiskLimits(
        max_portfolio_heat_pct=percentage_value("MAX_PORTFOLIO_HEAT_PCT", settings.MAX_PORTFOLIO_HEAT_PCT),
        max_ticker_exposure_pct=percentage_value("MAX_TICKER_EXPOSURE_PCT", settings.MAX_TICKER_EXPOSURE_PCT),
        max_sector_exposure_pct=percentage_value("MAX_SECTOR_EXPOSURE_PCT", settings.MAX_SECTOR_EXPOSURE_PCT),
        max_asset_class_exposure_pct=percentage_value("MAX_ASSET_CLASS_EXPOSURE_PCT", settings.MAX_ASSET_CLASS_EXPOSURE_PCT),
        max_directional_exposure_pct=percentage_value("MAX_DIRECTIONAL_EXPOSURE_PCT", settings.MAX_DIRECTIONAL_EXPOSURE_PCT),
        max_correlated_exposure_pct=percentage_value("MAX_CORRELATED_EXPOSURE_PCT", settings.MAX_CORRELATED_EXPOSURE_PCT),
        correlation_threshold=percentage_value("CORRELATION_THRESHOLD", settings.CORRELATION_THRESHOLD),
        daily_loss_limit_pct=percentage_value("DAILY_LOSS_LIMIT_PCT", settings.DAILY_LOSS_LIMIT_PCT),
        weekly_loss_limit_pct=percentage_value("WEEKLY_LOSS_LIMIT_PCT", settings.WEEKLY_LOSS_LIMIT_PCT),
        max_drawdown_pct=percentage_value("MAX_DRAWDOWN_PCT", settings.MAX_DRAWDOWN_PCT),
        volatility_target_pct=percentage_value("VOLATILITY_TARGET_PCT", settings.VOLATILITY_TARGET_PCT),
        fractional_kelly_cap=percentage_value("FRACTIONAL_KELLY_CAP", settings.FRACTIONAL_KELLY_CAP),
        equity_shock_pct=percentage_value("EQUITY_SHOCK_PCT", settings.EQUITY_SHOCK_PCT),
        crypto_shock_pct=percentage_value("CRYPTO_SHOCK_PCT", settings.CRYPTO_SHOCK_PCT),
    )


async def _fetch_return_series(tickers: set[str]) -> dict[str, dict[str, float]]:
    if not tickers:
        return {}

    async def fetch_one(client: httpx.AsyncClient, ticker: str) -> tuple[str, dict[str, float]]:
        try:
            response = await client.get(
                f"{settings.DATA_INGESTION_URL}/api/candles/{ticker}",
                params={"interval": "1d"},
                timeout=8,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                return ticker, {}
            candles = [row for row in payload if isinstance(row, dict)]
            return ticker, calculate_return_series(candles[-91:])
        except Exception as exc:
            logger.warning("Correlation data unavailable for %s: %s", ticker, exc)
            return ticker, {}

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*(fetch_one(client, ticker) for ticker in sorted(tickers)))
    return dict(results)


def _position_from_trade(trade: Trade, returns: dict[str, float]) -> PositionInput:
    direction = trade.direction.value if isinstance(trade.direction, SignalDirection) else str(trade.direction)
    return PositionInput(
        ticker=trade.ticker,
        direction=direction,
        entry_price=trade.entry_price,
        quantity=trade.quantity,
        stop_loss=trade.stop_loss,
        asset_type=trade.asset_type or "stock",
        sector=trade.sector or "Unclassified",
        returns=returns,
    )


async def _risk_context(
    db: AsyncSession,
    additional_tickers: set[str] | None = None,
) -> tuple[Portfolio, list[PositionInput], list[ClosedTradeResult], dict[str, dict[str, float]]]:
    portfolio = await get_or_create_portfolio(db)
    open_result = await db.execute(select(Trade).where(Trade.status == TradeStatus.OPEN))
    open_trades = list(open_result.scalars().all())
    tickers = {trade.ticker for trade in open_trades}
    tickers.update(additional_tickers or set())
    returns_by_ticker = await _fetch_return_series(tickers)
    positions = [
        _position_from_trade(trade, returns_by_ticker.get(trade.ticker, {}))
        for trade in open_trades
    ]
    closed_result = await db.execute(
        select(Trade).where(Trade.status == TradeStatus.CLOSED, Trade.closed_at.is_not(None))
    )
    closed_trades = [
        ClosedTradeResult(pnl=trade.pnl or 0.0, closed_at=trade.closed_at)
        for trade in closed_result.scalars().all()
        if trade.closed_at is not None
    ]
    return portfolio, positions, closed_trades, returns_by_ticker


async def _portfolio_risk_status(db: AsyncSession) -> dict[str, object]:
    portfolio, positions, closed_trades, _ = await _risk_context(db)
    limits = _risk_limits()
    snapshot = calculate_portfolio_risk(positions, portfolio.equity, limits)
    snapshot["breaker"] = evaluate_breakers(
        closed_trades,
        portfolio.equity,
        portfolio.peak_equity,
        limits,
    ).as_dict(limits)
    snapshot["equity"] = round(portfolio.equity, 2)
    return snapshot


async def execute_paper_trade(
    db: AsyncSession, ticker: str, direction: SignalDirection,
    entry_price: float, stop_loss: float, target_price: float,
    quantity_override: Optional[float] = None,
    market_regime: Optional[dict[str, object]] = None,
    timeframe_agreement: Optional[dict[str, object]] = None,
) -> Optional[Trade]:
    portfolio = await get_or_create_portfolio(db)
    risk_per_share = abs(entry_price - stop_loss)
    if risk_per_share <= 0:
        return None
    max_risk = portfolio.equity * POSITION_SIZE_PCT
    quantity = quantity_override if quantity_override else max_risk / risk_per_share
    cost = quantity * entry_price
    if cost > portfolio.balance:
        quantity = portfolio.balance / entry_price
        cost = quantity * entry_price
    if quantity <= 0 or cost <= 0:
        return None
    regime = market_regime or {}
    timeframe = timeframe_agreement or {}
    trade = Trade(
        ticker=ticker, direction=direction, entry_price=entry_price,
        quantity=quantity, stop_loss=stop_loss, target_price=target_price,
        market_regime=str(regime.get("trend", "unknown")),
        volatility_regime=str(regime.get("volatility", "unknown")),
        breadth_regime=str(regime.get("breadth", "unknown")),
        risk_regime=str(regime.get("risk", "unknown")),
        regime_label=str(regime.get("label", "Unknown")),
        timeframe_agreement=float(timeframe.get("score", 0.0)) if timeframe else None,
        status=TradeStatus.OPEN,
    )
    db.add(trade)
    portfolio.balance -= cost
    await db.commit()
    await db.refresh(trade)
    return trade


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Portfolio Engine Service", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "portfolio-engine"}


@app.post("/api/process-signal", response_model=SignalDecision)
async def process_signal(signal: SignalInput, db: AsyncSession = Depends(get_db)):
    """Validate a signal against portfolio-wide risk controls and log the decision."""
    ticker = signal.ticker.strip().upper()
    portfolio, positions, closed_trades, returns_by_ticker = await _risk_context(db, {ticker})
    direction = signal.direction.upper() if signal.direction.upper() in ("BUY", "SELL") else "BUY"
    quantity = (
        signal.optimal_size_usd / signal.trigger_price
        if signal.trigger_price > 0 and signal.optimal_size_usd > 0
        else 0.0
    )
    proposal = PositionInput(
        ticker=ticker,
        direction=direction,
        entry_price=signal.trigger_price,
        quantity=quantity,
        stop_loss=signal.stop_loss,
        asset_type=signal.asset_type,
        sector=signal.sector,
        returns=returns_by_ticker.get(ticker, {}),
    )
    limits = _risk_limits()
    risk_decision = evaluate_proposed_position(
        positions,
        proposal,
        closed_trades,
        portfolio.equity,
        portfolio.peak_equity,
        limits,
        kelly_fraction=max(0.0, signal.kelly_pct / 100),
        annual_volatility=annualized_volatility(proposal.returns, proposal.asset_type),
    )
    risk_reasons = risk_decision.get("reasons")
    if not isinstance(risk_reasons, list):
        risk_reasons = []
        risk_decision["reasons"] = risk_reasons
    if signal.suppressed:
        risk_reasons.insert(0, {
            "code": "SIGNAL_SUPPRESSED",
            "message": f"Signal suppressed ({signal.status})",
        })
        risk_decision["approved"] = False
        risk_decision["action"] = "rejected"
        risk_decision["recommended_size_usd"] = 0.0

    approved = bool(risk_decision["approved"])
    recommended_size = float(risk_decision["recommended_size_usd"])
    hard_limit_codes = {
        "MAX_PORTFOLIO_HEAT",
        "MAX_TICKER_EXPOSURE",
        "MAX_SECTOR_EXPOSURE",
        "MAX_ASSET_CLASS_EXPOSURE",
        "MAX_DIRECTIONAL_EXPOSURE",
        "MAX_CORRELATED_EXPOSURE",
    }
    overspend = any(
        isinstance(reason, dict) and reason.get("code") in hard_limit_codes
        for reason in risk_reasons
    )
    reason_parts = [signal.reason]
    reason_parts.extend(
        str(reason.get("message"))
        for reason in risk_reasons
        if isinstance(reason, dict) and reason.get("message")
    )
    message = " | ".join(reason_parts)

    regime = signal.market_regime
    timeframe = signal.timeframe_agreement
    alert = AlertLog(
        ticker=ticker,
        direction=signal.direction or "NONE",
        status=signal.status,
        trigger_price=signal.trigger_price,
        stop_loss=signal.stop_loss,
        target_price=signal.target_price,
        optimal_size_usd=recommended_size,
        kelly_pct=signal.kelly_pct,
        capital_overspend=overspend,
        approved=approved,
        message=message,
        risk_decision_json=json.dumps(risk_decision),
        market_regime=str(regime.get("trend", "unknown")),
        volatility_regime=str(regime.get("volatility", "unknown")),
        breadth_regime=str(regime.get("breadth", "unknown")),
        risk_regime=str(regime.get("risk", "unknown")),
        regime_label=str(regime.get("label", "Unknown")),
        timeframe_agreement=float(timeframe.get("score", 0.0)) if timeframe else None,
    )
    db.add(alert)
    await db.commit()

    return SignalDecision(
        ticker=ticker,
        direction=signal.direction or "NONE",
        status=signal.status,
        approved=approved,
        trigger_price=signal.trigger_price,
        stop_loss=signal.stop_loss,
        target_price=signal.target_price,
        optimal_size_usd=recommended_size,
        kelly_pct=signal.kelly_pct,
        capital_overspend=overspend,
        reason=message,
        paper_trade_executed=False,
        risk_decision=risk_decision,
    )


@app.get("/api/portfolio", response_model=PortfolioResponse)
async def get_portfolio(db: AsyncSession = Depends(get_db)):
    portfolio = await get_or_create_portfolio(db)
    total_trades = portfolio.win_count + portfolio.loss_count
    win_rate = (portfolio.win_count / total_trades * 100) if total_trades > 0 else 0

    open_result = await db.execute(select(Trade).where(Trade.status == TradeStatus.OPEN))
    open_positions = len(open_result.scalars().all())

    result = await db.execute(select(Trade).where(Trade.status == TradeStatus.CLOSED))
    closed = result.scalars().all()
    gross_profit = sum(t.pnl for t in closed if t.pnl and t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in closed if t.pnl and t.pnl < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

    snap_result = await db.execute(
        select(EquitySnapshot).order_by(EquitySnapshot.timestamp).limit(500),
    )
    snapshots = snap_result.scalars().all()
    equity_curve = [
        {"timestamp": s.timestamp.isoformat() if s.timestamp else "", "equity": s.equity}
        for s in snapshots
    ]

    return PortfolioResponse(
        balance=round(portfolio.balance, 2),
        equity=round(portfolio.equity, 2),
        total_pnl=round(portfolio.total_pnl, 2),
        win_count=portfolio.win_count,
        loss_count=portfolio.loss_count,
        win_rate=round(win_rate, 2),
        max_drawdown=round(portfolio.max_drawdown, 2),
        profit_factor=round(profit_factor, 2),
        peak_equity=round(portfolio.peak_equity, 2),
        open_positions=open_positions,
        equity_curve=equity_curve,
    )


class RiskEvaluationInput(BaseModel):
    ticker: str
    direction: str
    entry_price: float
    quantity: float
    stop_loss: float
    asset_type: str = "stock"
    sector: str = "Unclassified"
    kelly_pct: float = 0.0
    intent: str = "increase"


@app.get("/api/portfolio/risk")
async def portfolio_risk(db: AsyncSession = Depends(get_db)):
    return await _portfolio_risk_status(db)


@app.post("/api/portfolio/risk/evaluate")
async def evaluate_portfolio_risk(
    payload: RiskEvaluationInput,
    db: AsyncSession = Depends(get_db),
):
    if payload.entry_price <= 0 or payload.quantity < 0 or payload.stop_loss <= 0:
        raise HTTPException(400, "Entry price and stop must be positive; quantity cannot be negative")
    direction = payload.direction.upper()
    if direction not in ("BUY", "SELL"):
        raise HTTPException(400, "Direction must be BUY or SELL")
    ticker = payload.ticker.strip().upper()
    portfolio, positions, closed_trades, returns_by_ticker = await _risk_context(db, {ticker})
    proposal = PositionInput(
        ticker=ticker,
        direction=direction,
        entry_price=payload.entry_price,
        quantity=payload.quantity,
        stop_loss=payload.stop_loss,
        asset_type=payload.asset_type,
        sector=payload.sector,
        returns=returns_by_ticker.get(ticker, {}),
    )
    limits = _risk_limits()
    return evaluate_proposed_position(
        positions,
        proposal,
        closed_trades,
        portfolio.equity,
        portfolio.peak_equity,
        limits,
        kelly_fraction=max(0.0, payload.kelly_pct / 100),
        annual_volatility=annualized_volatility(proposal.returns, proposal.asset_type),
        intent=payload.intent,
    )


def _widget_regime(scanner: dict[str, object]) -> dict[str, object]:
    """Current regime from the latest scan, or an explicit unavailable marker."""
    scan = scanner.get("last_scan_result")
    signals = scan.get("signals") if isinstance(scan, dict) else None
    if not isinstance(signals, list):
        return {"available": False, "reason": "No completed scan available"}
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        regime = signal.get("market_regime")
        if isinstance(regime, dict) and regime.get("label"):
            return {
                "available": True,
                "label": regime.get("label"),
                "trend": regime.get("trend"),
                "volatility": regime.get("volatility"),
                "breadth": regime.get("breadth"),
                "risk": regime.get("risk"),
                "scanned_at": scan.get("timestamp") if isinstance(scan, dict) else None,
            }
    return {"available": False, "reason": "Latest scan carried no regime snapshot"}


def _widget_top_opportunities(scanner: dict[str, object], limit: int = 5) -> dict[str, object]:
    scan = scanner.get("last_scan_result")
    signals = scan.get("signals") if isinstance(scan, dict) else None
    if not isinstance(signals, list):
        return {"available": False, "reason": "No completed scan available", "items": []}
    rows: list[dict[str, object]] = []
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        opportunity = signal.get("opportunity")
        if not isinstance(opportunity, dict) or not bool(opportunity.get("eligible")):
            continue
        rows.append({
            "id": opportunity.get("id"),
            "ticker": opportunity.get("ticker") or signal.get("ticker"),
            "direction": opportunity.get("direction"),
            "score": float(opportunity.get("score") or 0.0),
            "user_decision": opportunity.get("user_decision", "pending"),
        })
    rows.sort(key=lambda row: -float(row["score"] or 0.0))
    return {"available": True, "items": rows[:limit]}


@app.get("/api/dashboard-summary")
async def dashboard_summary(request: Request, db: AsyncSession = Depends(get_db)):
    """Return at-a-glance portfolio metrics for the dashboard widget."""
    portfolio = await get_or_create_portfolio(db)

    open_result = await db.execute(select(Trade).where(Trade.status == TradeStatus.OPEN))
    open_positions = len(open_result.scalars().all())

    today_start = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_alerts = await db.execute(
        select(func.count(AlertLog.id)).where(AlertLog.created_at >= today_start)
    )
    todays_signals = today_alerts.scalar() or 0

    today_approved = await db.execute(
        select(func.count(AlertLog.id)).where(
            AlertLog.created_at >= today_start,
            AlertLog.approved == True,
        )
    )
    todays_approved = today_approved.scalar() or 0

    total_trades = portfolio.win_count + portfolio.loss_count
    win_rate = (portfolio.win_count / total_trades * 100) if total_trades > 0 else 0
    risk = await _portfolio_risk_status(db)

    user_key = _action_user_key(request)
    await _expire_action_snoozes(db, user_key)
    action_result = await db.execute(select(ActionItem).where(ActionItem.user_key == user_key))
    action_counts = _action_counts([_action_item_dict(item) for item in action_result.scalars().all()])

    scanner = await _fetch_scanner_status()
    scanner_error = str(scanner["error"]) if "error" in scanner else None
    data_status = await _fetch_data_status()
    reserved_cash = await _reserved_paper_cash(db)

    return {
        "balance": round(portfolio.balance, 2),
        "equity": round(portfolio.equity, 2),
        "total_pnl": round(portfolio.total_pnl, 2),
        "total_pnl_pct": round((portfolio.total_pnl / portfolio.equity * 100) if portfolio.equity else 0, 2),
        "open_positions": open_positions,
        "todays_signals": todays_signals,
        "todays_approved": todays_approved,
        "win_rate": round(win_rate, 2),
        "total_trades": total_trades,
        "risk": risk,
        "cash": {
            "available": True,
            "balance": round(portfolio.balance, 2),
            "reserved": round(reserved_cash, 2),
            "free": round(portfolio.balance - reserved_cash, 2),
            "peak_equity": round(portfolio.peak_equity, 2),
        },
        "regime": (
            {"available": False, "reason": scanner_error}
            if scanner_error
            else _widget_regime(scanner)
        ),
        "top_opportunities": (
            {"available": False, "reason": scanner_error, "items": []}
            if scanner_error
            else _widget_top_opportunities(scanner)
        ),
        "provider_health": (
            {"available": False, "reason": str(data_status["error"])}
            if "error" in data_status
            else {
                "available": True,
                "connectivity": data_status.get("connectivity"),
                "data_quality": data_status.get("data_quality"),
                "current_time": data_status.get("current_time"),
            }
        ),
        "action_counts": action_counts,
    }


@app.get("/api/trades", response_model=list[TradeResponse])
async def list_trades(
    status: Optional[str] = None, limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    query = select(Trade).order_by(desc(Trade.opened_at)).limit(limit)
    if status:
        query = query.where(Trade.status == TradeStatus(status))
    result = await db.execute(query)
    return result.scalars().all()


class ManualTradeInput(BaseModel):
    ticker: str
    direction: str  # "BUY" or "SELL"
    entry_price: float
    quantity: float
    stop_loss: Optional[float] = None
    target_price: Optional[float] = None
    asset_type: str = "stock"
    sector: str = "Unclassified"
    market_regime: dict[str, object] = Field(default_factory=dict)
    timeframe_agreement: dict[str, object] = Field(default_factory=dict)
    strategy_name: Optional[str] = None
    strategy_version: Optional[str] = None
    timeframe: Optional[str] = None
    signal_confidence: Optional[float] = None
    signal_context: dict[str, object] = Field(default_factory=dict)
    execution_context: dict[str, object] = Field(default_factory=dict)
    planned_entry_price: Optional[float] = None
    planned_exit_price: Optional[float] = None
    planned_quantity: Optional[float] = None
    entry_fees: float = 0.0
    entry_slippage: float = 0.0


@app.post("/api/trades/manual", response_model=TradeResponse)
async def log_manual_trade(payload: ManualTradeInput, db: AsyncSession = Depends(get_db)):
    """Log a manually executed trade for tracking purposes."""
    portfolio = await get_or_create_portfolio(db)
    if payload.entry_price <= 0 or payload.quantity <= 0:
        raise HTTPException(400, "Entry price and quantity must be positive")
    direction_str = payload.direction.upper()
    if direction_str not in ("BUY", "SELL"):
        raise HTTPException(400, f"Direction must be BUY or SELL, got: {direction_str}")
    direction = SignalDirection(direction_str)
    if direction == SignalDirection.SELL:
        stop = payload.stop_loss if payload.stop_loss is not None else payload.entry_price * 1.05
        target = payload.target_price if payload.target_price is not None else payload.entry_price * 0.85
    else:
        stop = payload.stop_loss if payload.stop_loss is not None else payload.entry_price * 0.95
        target = payload.target_price if payload.target_price is not None else payload.entry_price * 1.15
    if direction == SignalDirection.BUY and stop >= payload.entry_price:
        raise HTTPException(400, "A long-position stop must be below the entry price")
    if direction == SignalDirection.SELL and stop <= payload.entry_price:
        raise HTTPException(400, "A short-position stop must be above the entry price")
    regime = payload.market_regime
    timeframe = payload.timeframe_agreement
    trade = Trade(
        ticker=payload.ticker.strip().upper(),
        direction=direction,
        entry_price=payload.entry_price,
        quantity=payload.quantity,
        stop_loss=stop,
        target_price=target,
        asset_type="crypto" if payload.asset_type.lower() == "crypto" else "stock",
        sector=payload.sector.strip() or "Unclassified",
        market_regime=str(regime.get("trend", "unknown")),
        volatility_regime=str(regime.get("volatility", "unknown")),
        breadth_regime=str(regime.get("breadth", "unknown")),
        risk_regime=str(regime.get("risk", "unknown")),
        regime_label=str(regime.get("label", "Unknown")),
        timeframe_agreement=float(timeframe.get("score", 0.0)) if timeframe else None,
        strategy_name=(payload.strategy_name or "").strip() or None,
        strategy_version=(payload.strategy_version or "").strip() or None,
        timeframe=(payload.timeframe or "").strip() or None,
        signal_confidence=payload.signal_confidence,
        signal_context_json=json.dumps(payload.signal_context) if payload.signal_context else None,
        execution_context_json=json.dumps(payload.execution_context) if payload.execution_context else None,
        planned_entry_price=payload.planned_entry_price,
        planned_exit_price=payload.planned_exit_price,
        planned_quantity=payload.planned_quantity,
        entry_fees=max(0.0, payload.entry_fees),
        entry_slippage=max(0.0, payload.entry_slippage),
        realized_quantity=0.0,
        status=TradeStatus.OPEN,
    )
    db.add(trade)
    position_cost = payload.entry_price * payload.quantity
    if position_cost > portfolio.balance:
        raise HTTPException(400, f"Insufficient balance: need ${position_cost:,.2f} but only ${portfolio.balance:,.2f} available")
    portfolio.balance -= position_cost
    await db.commit()
    await db.refresh(trade)
    db.add(TradeExecution(
        trade_id=trade.id,
        kind=ExecutionKind.ENTRY,
        price=trade.entry_price,
        quantity=trade.quantity,
        fees=trade.entry_fees,
        slippage=trade.entry_slippage,
    ))
    await db.commit()
    return trade


async def _reserved_paper_cash(db: AsyncSession) -> float:
    """Cash held aside by working paper orders; it is equity but not balance."""
    result = await db.execute(
        select(func.coalesce(func.sum(PaperOrder.reserved_cash), 0.0)).where(
            PaperOrder.status.in_(paper.FILLABLE_STATUSES)
        )
    )
    return float(result.scalar() or 0.0)


async def _recompute_portfolio_equity(
    db: AsyncSession,
    portfolio: Portfolio,
    *,
    snapshot: bool = True,
) -> float:
    """Equity is cash plus capital locked in open positions plus paper reservations."""
    result = await db.execute(select(Trade).where(Trade.status == TradeStatus.OPEN))
    locked = sum(
        position.entry_price * (position.quantity - (position.realized_quantity or 0.0))
        for position in result.scalars().all()
    )
    portfolio.equity = portfolio.balance + locked + await _reserved_paper_cash(db)
    if portfolio.equity > portfolio.peak_equity:
        portfolio.peak_equity = portfolio.equity
    drawdown = (
        ((portfolio.peak_equity - portfolio.equity) / portfolio.peak_equity * 100)
        if portfolio.peak_equity > 0
        else 0
    )
    if drawdown > portfolio.max_drawdown:
        portfolio.max_drawdown = round(drawdown, 2)
    if snapshot:
        db.add(EquitySnapshot(equity=portfolio.equity, balance=portfolio.balance))
    return portfolio.equity


async def _apply_position_exit(
    db: AsyncSession,
    trade: Trade,
    exit_price: float,
    quantity: Optional[float],
    fees: float,
    slippage: float,
    note: Optional[str],
) -> tuple[float, bool]:
    """Realize all or part of an open position, returning ``(net_pnl, final_exit)``.

    Manual closes and paper-order exit fills share this path so cash, costs and
    performance records are written exactly once per realized unit.
    """
    remaining = trade.quantity - (trade.realized_quantity or 0.0)
    if quantity is not None:
        if quantity <= 0:
            raise HTTPException(400, "Exit quantity must be positive")
        if quantity > remaining + 1e-9:
            raise HTTPException(400, f"Exit quantity exceeds the remaining {remaining:g} units")
        close_quantity = min(quantity, remaining)
    else:
        close_quantity = remaining
    is_final_exit = abs(remaining - close_quantity) <= 1e-9

    direction = trade.direction.value if isinstance(trade.direction, SignalDirection) else str(trade.direction)
    entry_costs = (trade.entry_fees or 0.0) + (trade.entry_slippage or 0.0)
    costs = attribution_math.ExitCosts(
        entry_costs_allocated=attribution_math.allocate_entry_costs(
            entry_costs,
            trade.entry_costs_allocated or 0.0,
            close_quantity,
            trade.quantity,
            is_final_exit,
        ),
        exit_fees=fees,
        exit_slippage=slippage,
    )
    gross_pnl, net_pnl = attribution_math.net_exit_pnl(
        direction,
        trade.entry_price,
        exit_price,
        close_quantity,
        costs,
    )

    trade.exit_price = exit_price
    trade.realized_quantity = (trade.realized_quantity or 0.0) + close_quantity
    trade.entry_costs_allocated = (trade.entry_costs_allocated or 0.0) + costs.entry_costs_allocated
    trade.exit_fees_total = (trade.exit_fees_total or 0.0) + fees
    trade.exit_slippage_total = (trade.exit_slippage_total or 0.0) + slippage
    trade.costs_total = (trade.costs_total or 0.0) + costs.total
    trade.gross_pnl = (trade.gross_pnl or 0.0) + gross_pnl
    trade.pnl = (trade.pnl or 0.0) + net_pnl
    trade.pnl_pct = round((trade.pnl / (trade.entry_price * trade.quantity)) * 100, 2)
    db.add(TradeExecution(
        trade_id=trade.id,
        kind=ExecutionKind.EXIT,
        price=exit_price,
        quantity=close_quantity,
        fees=fees,
        slippage=slippage,
        entry_costs_allocated=costs.entry_costs_allocated,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        note=note,
    ))
    if is_final_exit:
        trade.status = TradeStatus.CLOSED
        trade.closed_at = datetime.datetime.now(datetime.timezone.utc)

    portfolio = await get_or_create_portfolio(db)
    portfolio.balance += trade.entry_price * close_quantity + net_pnl
    portfolio.total_pnl += net_pnl
    if is_final_exit:
        if trade.pnl >= 0:
            portfolio.win_count += 1
        else:
            portfolio.loss_count += 1
    await _recompute_portfolio_equity(db, portfolio)
    return net_pnl, is_final_exit


class CloseTradeInput(BaseModel):
    exit_price: float
    quantity: Optional[float] = None
    fees: float = 0.0
    slippage: float = 0.0
    note: Optional[str] = None


@app.post("/api/trades/{trade_id}/close", response_model=TradeResponse)
async def close_trade(trade_id: int, payload: CloseTradeInput, db: AsyncSession = Depends(get_db)):
    """Close all or part of an open trade, calculating net realized P&L after costs.

    Omitting ``quantity`` closes the entire remaining position.
    """
    result = await db.execute(select(Trade).where(Trade.id == trade_id))
    trade = result.scalar_one_or_none()
    if trade is None:
        raise HTTPException(404, "Trade not found")
    if trade.status != TradeStatus.OPEN:
        raise HTTPException(400, "Trade is already closed")
    if payload.exit_price <= 0:
        raise HTTPException(400, "Exit price must be positive")
    if payload.fees < 0 or payload.slippage < 0:
        raise HTTPException(400, "Fees and slippage cannot be negative")

    _, is_final_exit = await _apply_position_exit(
        db,
        trade,
        payload.exit_price,
        payload.quantity,
        payload.fees,
        payload.slippage,
        payload.note,
    )
    await db.commit()
    await db.refresh(trade)
    if is_final_exit:
        await _finalize_closed_trade(db, trade)
        await db.refresh(trade)
    return trade


async def _fetch_candles(ticker: str, interval: str) -> list[dict[str, object]]:
    """Fetch candles for excursion math, returning an empty list when unavailable."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{settings.DATA_INGESTION_URL}/api/candles/{ticker}",
                params={"interval": interval},
                timeout=8,
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.warning("Candle data unavailable for %s: %s", ticker, exc)
        return []
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


def _json_field(raw: Optional[str]) -> Optional[dict[str, object]]:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _trade_regime(trade: Trade) -> Optional[str]:
    trend = attribution_math.normalize_label(trade.market_regime)
    volatility = attribution_math.normalize_label(trade.volatility_regime)
    if trend == attribution_math.UNKNOWN and volatility == attribution_math.UNKNOWN:
        return None
    return f"{trend} / {volatility}"


def _execution_dict(execution: TradeExecution) -> dict[str, object]:
    kind = execution.kind.value if isinstance(execution.kind, ExecutionKind) else str(execution.kind)
    return {
        "id": execution.id,
        "trade_id": execution.trade_id,
        "kind": kind,
        "price": execution.price,
        "quantity": execution.quantity,
        "fees": execution.fees,
        "slippage": execution.slippage,
        "entry_costs_allocated": round(execution.entry_costs_allocated or 0.0, 4),
        "gross_pnl": execution.gross_pnl,
        "net_pnl": execution.net_pnl,
        "note": execution.note,
        "executed_at": execution.executed_at.isoformat() if execution.executed_at else None,
    }


def _trade_attribution_dict(trade: Trade, executions: list[dict[str, object]]) -> dict[str, object]:
    direction = trade.direction.value if isinstance(trade.direction, SignalDirection) else str(trade.direction)
    status = trade.status.value if isinstance(trade.status, TradeStatus) else str(trade.status)
    exits = [row for row in executions if row["kind"] == "EXIT"]
    exit_quantity = sum(float(row["quantity"]) for row in exits)
    average_exit = (
        sum(float(row["price"]) * float(row["quantity"]) for row in exits) / exit_quantity
        if exit_quantity > 0
        else trade.exit_price
    )
    net_pnl = trade.pnl or 0.0
    return {
        "id": trade.id,
        "ticker": trade.ticker,
        "direction": direction,
        "status": status,
        "fully_closed": status == TradeStatus.CLOSED.value,
        "realized_quantity": trade.realized_quantity or 0.0,
        "remaining_quantity": trade.remaining_quantity,
        "strategy": trade.strategy_name,
        "strategy_version": trade.strategy_version,
        "asset_type": trade.asset_type,
        "sector": trade.sector,
        "timeframe": trade.timeframe,
        "regime": _trade_regime(trade),
        "regime_label": trade.regime_label,
        "signal_confidence": trade.signal_confidence,
        "signal_context": _json_field(trade.signal_context_json),
        "execution_context": _json_field(trade.execution_context_json),
        "entry_price": trade.entry_price,
        "planned_entry_price": trade.planned_entry_price,
        "average_exit_price": round(average_exit, 6) if average_exit is not None else None,
        "planned_exit_price": trade.planned_exit_price,
        "quantity": trade.quantity,
        "planned_quantity": trade.planned_quantity,
        "stop_loss": trade.stop_loss,
        "target_price": trade.target_price,
        "gross_pnl": trade.gross_pnl if trade.gross_pnl is not None else net_pnl,
        "costs": trade.costs_total or 0.0,
        "entry_fees": trade.entry_fees or 0.0,
        "exit_fees": trade.exit_fees_total or 0.0,
        "slippage": (trade.entry_slippage or 0.0) + (trade.exit_slippage_total or 0.0),
        "net_pnl": net_pnl,
        "net_pnl_pct": trade.pnl_pct,
        "mfe_usd": trade.mfe_usd,
        "mae_usd": trade.mae_usd,
        "mfe_pct": trade.mfe_pct,
        "mae_pct": trade.mae_pct,
        "excursion_status": trade.excursion_status,
        "exit_count": len(exits),
        "executions": executions,
        "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
        "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
    }


async def _trade_executions(db: AsyncSession, trade_ids: list[int]) -> dict[int, list[dict[str, object]]]:
    if not trade_ids:
        return {}
    result = await db.execute(
        select(TradeExecution)
        .where(TradeExecution.trade_id.in_(trade_ids))
        .order_by(TradeExecution.id)
    )
    grouped: dict[int, list[dict[str, object]]] = {}
    for execution in result.scalars().all():
        grouped.setdefault(execution.trade_id, []).append(_execution_dict(execution))
    return grouped


def _journal_summary(journal: dict[str, object]) -> str:
    setup = journal["setup"]
    result = journal["result"]
    return (
        f"{setup['direction']} {journal['ticker']} · {setup['strategy']} {setup['strategy_version']} · "
        f"{setup['regime']} regime · {result['outcome']} "
        f"net ${result['net_pnl']:,.2f} after ${result['costs']:,.2f} costs "
        f"across {result['exit_count']} exit(s)"
    )


async def _finalize_closed_trade(db: AsyncSession, trade: Trade) -> dict[str, object]:
    """Record excursions and one automated journal entry for a fully closed trade."""
    executions = (await _trade_executions(db, [trade.id])).get(trade.id, [])
    candles = attribution_math.select_window_candles(
        await _fetch_candles(trade.ticker, settings.ATTRIBUTION_CANDLE_INTERVAL),
        trade.opened_at.isoformat() if trade.opened_at else None,
        trade.closed_at.isoformat() if trade.closed_at else None,
    )
    direction = trade.direction.value if isinstance(trade.direction, SignalDirection) else str(trade.direction)
    excursions = attribution_math.calculate_excursions(
        direction, trade.entry_price, trade.quantity, candles,
    )
    if excursions:
        trade.mfe_usd = excursions["mfe_usd"]
        trade.mae_usd = excursions["mae_usd"]
        trade.mfe_pct = excursions["mfe_pct"]
        trade.mae_pct = excursions["mae_pct"]
        trade.excursion_status = "calculated"
    else:
        trade.excursion_status = "unavailable"

    journal = attribution_math.build_journal(
        _trade_attribution_dict(trade, executions), executions, excursions,
    )
    existing = await db.execute(select(TradeJournal).where(TradeJournal.trade_id == trade.id))
    if existing.scalar_one_or_none() is None:
        db.add(TradeJournal(
            trade_id=trade.id,
            ticker=trade.ticker,
            strategy_name=trade.strategy_name,
            outcome=str(journal["result"]["outcome"]),
            net_pnl=float(journal["result"]["net_pnl"]),
            summary=_journal_summary(journal),
            journal_json=json.dumps(journal),
        ))
    await db.commit()
    return journal


class AttributionFilters(BaseModel):
    strategy: Optional[str] = None
    ticker: Optional[str] = None
    asset_type: Optional[str] = None
    sector: Optional[str] = None
    timeframe: Optional[str] = None
    regime: Optional[str] = None

    def matches(self, trade: dict[str, object]) -> bool:
        for dimension in attribution_math.DIMENSIONS:
            wanted = getattr(self, dimension)
            if not wanted:
                continue
            actual = attribution_math.normalize_label(trade.get(dimension))
            if actual.lower() != wanted.strip().lower():
                return False
        return True


async def _attribution_payload(
    db: AsyncSession,
    filters: AttributionFilters,
) -> dict[str, object]:
    portfolio = await get_or_create_portfolio(db)
    result = await db.execute(
        select(Trade)
        .where(
            or_(
                Trade.status == TradeStatus.CLOSED,
                Trade.realized_quantity > 0,
            )
        )
        .order_by(desc(Trade.closed_at), desc(Trade.id))
    )
    realized = list(result.scalars().all())
    executions = await _trade_executions(db, [trade.id for trade in realized])
    all_trades = [
        _trade_attribution_dict(trade, executions.get(trade.id, []))
        for trade in realized
    ]
    journal_result = await db.execute(
        select(TradeJournal).order_by(desc(TradeJournal.created_at))
    )
    journals_by_trade = {
        row.trade_id: {
            "trade_id": row.trade_id,
            "ticker": row.ticker,
            "strategy_name": row.strategy_name,
            "outcome": row.outcome,
            "net_pnl": row.net_pnl,
            "summary": row.summary,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "journal": _json_field(row.journal_json),
        }
        for row in journal_result.scalars().all()
    }
    for trade in all_trades:
        journal = journals_by_trade.get(int(trade["id"]))
        detail = journal.get("journal") if journal else None
        trade["rule_adherence"] = detail.get("rule_adherence") if isinstance(detail, dict) else None
    filtered = [trade for trade in all_trades if filters.matches(trade)]
    payload = attribution_math.build_attribution(
        filtered,
        portfolio.total_pnl or 0.0,
        sum(float(trade["net_pnl"] or 0.0) for trade in all_trades),
        settings.ATTRIBUTION_MIN_SAMPLE_SIZE,
    )
    payload["filters"] = filters.model_dump()
    payload["filters_available"] = {
        dimension: sorted({
            attribution_math.normalize_label(trade.get(dimension)) for trade in all_trades
        })
        for dimension in attribution_math.DIMENSIONS
    }
    payload["trades"] = filtered
    payload["journals"] = [
        journals_by_trade[int(trade["id"])]
        for trade in filtered
        if int(trade["id"]) in journals_by_trade
    ]
    return payload


@app.get("/api/attribution")
async def performance_attribution(
    strategy: Optional[str] = None,
    ticker: Optional[str] = None,
    asset_type: Optional[str] = None,
    sector: Optional[str] = None,
    timeframe: Optional[str] = None,
    regime: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Performance attribution for closed trades, grouped by every dimension."""
    filters = AttributionFilters(
        strategy=strategy, ticker=ticker, asset_type=asset_type,
        sector=sector, timeframe=timeframe, regime=regime,
    )
    return await _attribution_payload(db, filters)


@app.get("/api/attribution/export")
async def export_attribution(
    format: str = "json",
    strategy: Optional[str] = None,
    ticker: Optional[str] = None,
    asset_type: Optional[str] = None,
    sector: Optional[str] = None,
    timeframe: Optional[str] = None,
    regime: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Export the filtered attribution set as JSON or CSV."""
    export_format = format.lower()
    if export_format not in ("json", "csv"):
        raise HTTPException(400, "Format must be json or csv")
    filters = AttributionFilters(
        strategy=strategy, ticker=ticker, asset_type=asset_type,
        sector=sector, timeframe=timeframe, regime=regime,
    )
    payload = await _attribution_payload(db, filters)
    if export_format == "json":
        return payload
    csv_body = attribution_math.attribution_csv(payload["trades"])
    return PlainTextResponse(
        csv_body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="attribution.csv"'},
    )


@app.get("/api/trades/{trade_id}/journal")
async def trade_journal(trade_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TradeJournal).where(TradeJournal.trade_id == trade_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "No journal entry for this trade yet")
    return {
        "trade_id": row.trade_id,
        "ticker": row.ticker,
        "strategy_name": row.strategy_name,
        "outcome": row.outcome,
        "net_pnl": row.net_pnl,
        "summary": row.summary,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "journal": _json_field(row.journal_json),
    }


@app.get("/api/trades/{trade_id}/executions")
async def trade_executions(trade_id: int, db: AsyncSession = Depends(get_db)):
    executions = (await _trade_executions(db, [trade_id])).get(trade_id, [])
    return {"trade_id": trade_id, "executions": executions}


PAPER_MODE = {
    "mode": "paper",
    "live_trading_enabled": False,
    "notice": "Simulated fills only — no broker order is ever submitted.",
}


class PaperCandleInput(BaseModel):
    timestamp: datetime.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class PaperOrderCreateRequest(BaseModel):
    idempotency_key: str
    ticker: str
    side: str
    order_type: str
    quantity: float
    asset_type: str = "stock"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    trail_percent: Optional[float] = None
    trail_amount: Optional[float] = None
    reference_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    time_in_force: str = "gtc"
    expires_at: Optional[datetime.datetime] = None


class PaperOrderCancelRequest(BaseModel):
    reason: Optional[str] = None


class PaperOrderProcessRequest(BaseModel):
    ticker: str
    candles: list[PaperCandleInput] = Field(default_factory=list)
    interval: Optional[str] = None
    order_id: Optional[int] = None
    spread_pct: Optional[float] = None
    slippage_pct: Optional[float] = None
    participation_pct: Optional[float] = None
    fee_pct: Optional[float] = None


def _naive_utc(value: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return value


def _paper_fill_config(payload: PaperOrderProcessRequest) -> paper.FillConfig:
    """Frictions default to configuration and can be overridden per request."""

    def override(value: Optional[float], default: float) -> float:
        return default if value is None else max(0.0, float(value))

    return paper.FillConfig(
        spread_pct=override(payload.spread_pct, settings.PAPER_SPREAD_PCT),
        slippage_pct=override(payload.slippage_pct, settings.PAPER_SLIPPAGE_PCT),
        participation_pct=override(payload.participation_pct, settings.PAPER_VOLUME_PARTICIPATION_PCT),
        fee_pct=override(payload.fee_pct, settings.PAPER_FEE_PCT),
    )


def _paper_audit(
    db: AsyncSession,
    order: PaperOrder,
    event_type: str,
    *,
    to_status: Optional[str] = None,
    message: Optional[str] = None,
    detail: Optional[dict[str, object]] = None,
) -> PaperOrderEvent:
    """Append one immutable audit event, enforcing the order state machine."""
    from_status = order.status
    if to_status is not None and to_status != from_status:
        if not paper.transition_allowed(from_status, to_status):
            raise HTTPException(
                409, f"Order {order.id} cannot move from {from_status} to {to_status}"
            )
        order.status = to_status
    event = PaperOrderEvent(
        order_id=order.id,
        event_type=event_type,
        from_status=from_status,
        to_status=order.status,
        message=message,
        detail_json=json.dumps(detail, default=str) if detail else None,
    )
    db.add(event)
    return event


async def _paper_order(db: AsyncSession, order_id: int) -> Optional[PaperOrder]:
    result = await db.execute(select(PaperOrder).where(PaperOrder.id == order_id))
    return result.scalar_one_or_none()


async def _paper_children(db: AsyncSession, parent_id: int) -> list[PaperOrder]:
    result = await db.execute(
        select(PaperOrder).where(PaperOrder.parent_id == parent_id).order_by(PaperOrder.id)
    )
    return list(result.scalars().all())


async def _release_paper_reservation(
    db: AsyncSession,
    order: PaperOrder,
    portfolio: Portfolio,
    quantity: Optional[float] = None,
) -> float:
    """Return reserved cash to the balance, pro-rata for a partial fill."""
    reserved = order.reserved_cash or 0.0
    if reserved <= 0:
        return 0.0
    if quantity is None:
        released = reserved
    else:
        released = min(reserved, quantity * (order.reservation_price or 0.0))
    order.reserved_cash = round(reserved - released, 6)
    portfolio.balance = round(portfolio.balance + released, 6)
    return round(released, 6)


async def _paper_open_positions(db: AsyncSession, ticker: str) -> list[Trade]:
    result = await db.execute(
        select(Trade)
        .where(
            Trade.ticker == ticker,
            Trade.status == TradeStatus.OPEN,
            Trade.direction == SignalDirection.BUY,
        )
        .order_by(Trade.id)
    )
    return [trade for trade in result.scalars().all() if trade.remaining_quantity > paper.QUANTITY_EPSILON]


async def _paper_exit_position(db: AsyncSession, order: PaperOrder) -> Optional[Trade]:
    """Position a sell order reduces: its own, its parent's, then the oldest open."""
    for candidate_id in (order.trade_id, None):
        if candidate_id is None:
            break
        trade = (await db.execute(select(Trade).where(Trade.id == candidate_id))).scalar_one_or_none()
        if trade is not None and trade.status == TradeStatus.OPEN:
            return trade
    if order.parent_id:
        parent = await _paper_order(db, order.parent_id)
        if parent is not None and parent.trade_id:
            trade = (
                await db.execute(select(Trade).where(Trade.id == parent.trade_id))
            ).scalar_one_or_none()
            if trade is not None and trade.status == TradeStatus.OPEN:
                return trade
    positions = await _paper_open_positions(db, order.ticker)
    return positions[0] if positions else None


async def _paper_sellable_quantity(
    db: AsyncSession,
    ticker: str,
    *,
    oco_group: Optional[str] = None,
    order_id: Optional[int] = None,
) -> float:
    """Open long quantity minus what other working sell orders already claim."""
    open_quantity = sum(trade.remaining_quantity for trade in await _paper_open_positions(db, ticker))
    working = await db.execute(
        select(PaperOrder).where(
            PaperOrder.ticker == ticker,
            PaperOrder.side == paper.SELL,
            PaperOrder.status.in_(paper.FILLABLE_STATUSES),
        )
    )
    committed = 0.0
    for candidate in working.scalars().all():
        if order_id is not None and candidate.id == order_id:
            continue
        if oco_group and candidate.oco_group == oco_group:
            continue
        committed += candidate.remaining_quantity
    return paper.round_quantity(open_quantity - committed)


def _paper_order_dict(
    order: PaperOrder,
    *,
    children: Optional[list[PaperOrder]] = None,
    fills: Optional[list[PaperOrderFill]] = None,
    events: Optional[list[PaperOrderEvent]] = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": order.id,
        "idempotency_key": order.idempotency_key,
        "ticker": order.ticker,
        "asset_type": order.asset_type,
        "side": order.side,
        "order_type": order.order_type,
        "role": order.role,
        "status": order.status,
        "quantity": order.quantity,
        "filled_quantity": order.filled_quantity,
        "remaining_quantity": order.remaining_quantity,
        "limit_price": order.limit_price,
        "stop_price": order.stop_price,
        "trail_percent": order.trail_percent,
        "trail_amount": order.trail_amount,
        "trail_reference_price": order.trail_reference_price,
        "effective_stop_price": order.effective_stop_price,
        "triggered": bool(order.triggered),
        "triggered_at": order.triggered_at,
        "time_in_force": order.time_in_force,
        "expires_at": order.expires_at,
        "reference_price": order.reference_price,
        "reserved_cash": order.reserved_cash,
        "reservation_price": order.reservation_price,
        "average_fill_price": order.average_fill_price,
        "filled_notional": order.filled_notional,
        "fees_total": order.fees_total,
        "slippage_total": order.slippage_total,
        "costs_total": order.costs_total,
        "parent_id": order.parent_id,
        "oco_group": order.oco_group,
        "trade_id": order.trade_id,
        "last_candle_at": order.last_candle_at,
        "reject_reason": order.reject_reason,
        "cancel_reason": order.cancel_reason,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
        "mode": PAPER_MODE["mode"],
    }
    if children is not None:
        payload["children"] = [_paper_order_dict(child) for child in children]
    if fills is not None:
        payload["fills"] = [
            {
                "id": fill.id,
                "quantity": fill.quantity,
                "price": fill.price,
                "fees": fill.fees,
                "slippage": fill.slippage,
                "notional": round(fill.quantity * fill.price, 6),
                "candle_timestamp": fill.candle_timestamp,
                "trade_id": fill.trade_id,
                "created_at": fill.created_at,
            }
            for fill in fills
        ]
    if events is not None:
        payload["events"] = [
            {
                "id": event.id,
                "event_type": event.event_type,
                "from_status": event.from_status,
                "to_status": event.to_status,
                "message": event.message,
                "detail": _json_field(event.detail_json),
                "created_at": event.created_at,
            }
            for event in events
        ]
    return payload


async def _paper_order_detail(db: AsyncSession, order: PaperOrder) -> dict[str, object]:
    fills = (
        await db.execute(
            select(PaperOrderFill)
            .where(PaperOrderFill.order_id == order.id)
            .order_by(PaperOrderFill.id)
        )
    ).scalars().all()
    events = (
        await db.execute(
            select(PaperOrderEvent)
            .where(PaperOrderEvent.order_id == order.id)
            .order_by(PaperOrderEvent.id)
        )
    ).scalars().all()
    return _paper_order_dict(
        order,
        children=await _paper_children(db, order.id),
        fills=list(fills),
        events=list(events),
    )


def _candle_detail(candle: paper.Candle) -> dict[str, object]:
    return {
        "timestamp": candle.timestamp.isoformat(),
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
    }


@app.get("/api/paper-orders/mode")
async def paper_mode():
    """Paper mode is the only execution mode this service exposes."""
    return {
        **PAPER_MODE,
        "spread_pct": settings.PAPER_SPREAD_PCT,
        "slippage_pct": settings.PAPER_SLIPPAGE_PCT,
        "participation_pct": settings.PAPER_VOLUME_PARTICIPATION_PCT,
        "fee_pct": settings.PAPER_FEE_PCT,
        "candle_interval": settings.PAPER_ORDER_CANDLE_INTERVAL,
    }


@app.get("/api/paper-orders/reconcile")
async def reconcile_paper_orders(db: AsyncSession = Depends(get_db)):
    """Prove paper fills reconcile to positions, cash and equity."""
    portfolio = await get_or_create_portfolio(db)
    orders = (await db.execute(select(PaperOrder).order_by(PaperOrder.id))).scalars().all()
    fills = (await db.execute(select(PaperOrderFill))).scalars().all()
    positions = (
        await db.execute(select(Trade).where(Trade.status == TradeStatus.OPEN))
    ).scalars().all()
    status_counts: dict[str, int] = {status: 0 for status in paper.ORDER_STATUSES}
    for order in orders:
        status_counts[order.status] = status_counts.get(order.status, 0) + 1
    filled_quantity = paper.round_quantity(sum(fill.quantity for fill in fills))
    order_filled_quantity = paper.round_quantity(sum(order.filled_quantity or 0.0 for order in orders))
    locked = paper.round_cash(
        sum(position.entry_price * position.remaining_quantity for position in positions)
    )
    reserved = paper.round_cash(await _reserved_paper_cash(db))
    balance = paper.round_cash(portfolio.balance)
    equity = paper.round_cash(portfolio.equity)
    return {
        **PAPER_MODE,
        "orders": len(orders),
        "status_counts": status_counts,
        "fills": len(fills),
        "filled_quantity": filled_quantity,
        "order_filled_quantity": order_filled_quantity,
        "filled_notional": paper.round_cash(sum(fill.quantity * fill.price for fill in fills)),
        "fees_total": round(sum(fill.fees for fill in fills), 6),
        "slippage_total": round(sum(fill.slippage for fill in fills), 6),
        "reserved_cash": reserved,
        "position_capital": locked,
        "balance": balance,
        "equity": equity,
        "expected_equity": paper.round_cash(balance + locked + reserved),
        "fills_match_orders": abs(filled_quantity - order_filled_quantity) <= paper.QUANTITY_EPSILON,
        "equity_balanced": paper.equity_balanced(equity, [balance, locked, reserved]),
    }


@app.post("/api/paper-orders", status_code=201)
async def create_paper_order(
    payload: PaperOrderCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a simulated order. Re-sending an idempotency key returns the original."""
    key = payload.idempotency_key.strip()
    if not key:
        raise HTTPException(400, "An idempotency key is required so retries cannot duplicate orders")
    existing = (
        await db.execute(select(PaperOrder).where(PaperOrder.idempotency_key == key))
    ).scalar_one_or_none()
    if existing is not None:
        return await _paper_order_detail(db, existing)

    ticker = payload.ticker.strip().upper()
    side = payload.side.strip().upper()
    order_type = payload.order_type.strip().lower()
    asset_type = "crypto" if payload.asset_type.strip().lower() == "crypto" else "stock"
    time_in_force = payload.time_in_force.strip().lower()
    if not ticker:
        raise HTTPException(400, "Ticker is required")
    errors = paper.validate_order(
        side=side,
        order_type=order_type,
        quantity=payload.quantity,
        limit_price=payload.limit_price,
        stop_price=payload.stop_price,
        trail_percent=payload.trail_percent,
        trail_amount=payload.trail_amount,
        take_profit_price=payload.take_profit_price,
        stop_loss_price=payload.stop_loss_price,
        time_in_force=time_in_force,
    )
    if errors:
        raise HTTPException(400, "; ".join(errors))
    reservation_price = paper.reference_price_for_reservation(
        order_type, payload.limit_price, payload.stop_price, payload.reference_price
    )
    if side == paper.BUY and not reservation_price:
        raise HTTPException(
            400,
            "A buy market or trailing order needs a reference_price so buying power can be reserved",
        )

    portfolio = await get_or_create_portfolio(db)
    order = PaperOrder(
        idempotency_key=key,
        ticker=ticker,
        asset_type=asset_type,
        side=side,
        order_type=order_type,
        role=paper.ENTRY if order_type == paper.BRACKET else paper.STANDALONE,
        status=paper.PENDING,
        quantity=paper.round_quantity(payload.quantity),
        filled_quantity=0.0,
        limit_price=payload.limit_price,
        stop_price=payload.stop_price,
        trail_percent=payload.trail_percent,
        trail_amount=payload.trail_amount,
        time_in_force=time_in_force,
        expires_at=_naive_utc(payload.expires_at),
        reference_price=payload.reference_price,
        reservation_price=reservation_price,
        reserved_cash=0.0,
    )
    db.add(order)
    await db.flush()
    _paper_audit(
        db,
        order,
        "created",
        message=f"Paper {order_type} {side} {order.quantity:g} {ticker} accepted for simulation",
        detail={
            "idempotency_key": key,
            "asset_type": asset_type,
            "limit_price": payload.limit_price,
            "stop_price": payload.stop_price,
            "trail_percent": payload.trail_percent,
            "trail_amount": payload.trail_amount,
            "time_in_force": time_in_force,
            "expires_at": order.expires_at,
        },
    )

    if side == paper.BUY:
        reserve = round(order.quantity * (reservation_price or 0.0), 6)
        if reserve > portfolio.balance + 1e-9:
            order.reject_reason = (
                f"Insufficient buying power: need ${reserve:,.2f} but only "
                f"${portfolio.balance:,.2f} is available"
            )
            _paper_audit(
                db,
                order,
                "rejected",
                to_status=paper.REJECTED,
                message=order.reject_reason,
                detail={"required_cash": reserve, "available_cash": round(portfolio.balance, 2)},
            )
            await db.commit()
            return await _paper_order_detail(db, order)
        portfolio.balance = round(portfolio.balance - reserve, 6)
        order.reserved_cash = reserve
        _paper_audit(
            db,
            order,
            "cash_reserved",
            message=f"Reserved ${reserve:,.2f} at ${reservation_price:,.4f} per unit",
            detail={"reserved_cash": reserve, "reservation_price": reservation_price},
        )
    elif order_type != paper.BRACKET:
        available = await _paper_sellable_quantity(db, ticker)
        if order.quantity > available + paper.QUANTITY_EPSILON:
            order.reject_reason = (
                f"Insufficient position: {available:g} unreserved {ticker} units are open but "
                f"{order.quantity:g} were requested"
            )
            _paper_audit(
                db,
                order,
                "rejected",
                to_status=paper.REJECTED,
                message=order.reject_reason,
                detail={"available_quantity": available, "requested_quantity": order.quantity},
            )
            await db.commit()
            return await _paper_order_detail(db, order)

    _paper_audit(
        db,
        order,
        "submitted",
        to_status=paper.SUBMITTED,
        message="Working in the deterministic paper simulator; no broker order was sent",
    )

    if order_type == paper.BRACKET:
        oco_group = f"bracket-{order.id}"
        exit_side = paper.SELL if side == paper.BUY else paper.BUY
        children = (
            (paper.TAKE_PROFIT, paper.LIMIT, payload.take_profit_price, None),
            (paper.STOP_LOSS, paper.STOP, None, payload.stop_loss_price),
        )
        for role, child_type, limit_price, stop_price in children:
            child = PaperOrder(
                idempotency_key=f"{key}:{role}",
                ticker=ticker,
                asset_type=asset_type,
                side=exit_side,
                order_type=child_type,
                role=role,
                status=paper.PENDING,
                quantity=order.quantity,
                filled_quantity=0.0,
                limit_price=limit_price,
                stop_price=stop_price,
                time_in_force=time_in_force,
                expires_at=order.expires_at,
                parent_id=order.id,
                oco_group=oco_group,
                reserved_cash=0.0,
            )
            db.add(child)
            await db.flush()
            _paper_audit(
                db,
                child,
                "created",
                message=f"Bracket {role} stays inactive until parent order {order.id} fills",
                detail={"parent_id": order.id, "oco_group": oco_group, "role": role},
            )
        order.oco_group = oco_group

    await db.commit()
    return await _paper_order_detail(db, order)


@app.get("/api/paper-orders")
async def list_paper_orders(
    status: Optional[str] = None,
    ticker: Optional[str] = None,
    asset_type: Optional[str] = None,
    side: Optional[str] = None,
    order_type: Optional[str] = None,
    role: Optional[str] = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    query = select(PaperOrder).order_by(desc(PaperOrder.id)).limit(max(1, min(limit, 500)))
    if status:
        if status not in paper.ORDER_STATUSES:
            raise HTTPException(400, f"Unknown status '{status}'")
        query = query.where(PaperOrder.status == status)
    if ticker:
        query = query.where(PaperOrder.ticker == ticker.strip().upper())
    if asset_type:
        query = query.where(PaperOrder.asset_type == asset_type.strip().lower())
    if side:
        query = query.where(PaperOrder.side == side.strip().upper())
    if order_type:
        query = query.where(PaperOrder.order_type == order_type.strip().lower())
    if role:
        query = query.where(PaperOrder.role == role.strip().lower())
    orders = (await db.execute(query)).scalars().all()
    return {
        **PAPER_MODE,
        "orders": [_paper_order_dict(order) for order in orders],
        "filters_available": {
            "status": list(paper.ORDER_STATUSES),
            "order_type": list(paper.ORDER_TYPES),
            "side": [paper.BUY, paper.SELL],
            "asset_type": ["stock", "crypto"],
        },
    }


@app.get("/api/paper-orders/{order_id}")
async def get_paper_order(order_id: int, db: AsyncSession = Depends(get_db)):
    order = await _paper_order(db, order_id)
    if order is None:
        raise HTTPException(404, "Paper order not found")
    return await _paper_order_detail(db, order)


@app.get("/api/paper-orders/{order_id}/audit")
async def paper_order_audit(order_id: int, db: AsyncSession = Depends(get_db)):
    order = await _paper_order(db, order_id)
    if order is None:
        raise HTTPException(404, "Paper order not found")
    detail = await _paper_order_detail(db, order)
    return {"order_id": order.id, "events": detail["events"]}


@app.get("/api/paper-orders/{order_id}/fills")
async def paper_order_fills(order_id: int, db: AsyncSession = Depends(get_db)):
    order = await _paper_order(db, order_id)
    if order is None:
        raise HTTPException(404, "Paper order not found")
    detail = await _paper_order_detail(db, order)
    return {
        "order_id": order.id,
        "filled_quantity": order.filled_quantity,
        "remaining_quantity": order.remaining_quantity,
        "average_fill_price": order.average_fill_price,
        "fills": detail["fills"],
    }


async def _cancel_paper_order(
    db: AsyncSession,
    order: PaperOrder,
    portfolio: Portfolio,
    reason: str,
    *,
    event_type: str = "canceled",
) -> None:
    released = await _release_paper_reservation(db, order, portfolio)
    order.cancel_reason = reason
    _paper_audit(
        db,
        order,
        event_type,
        to_status=paper.CANCELED,
        message=reason,
        detail={"released_cash": released, "remaining_quantity": order.remaining_quantity},
    )


@app.post("/api/paper-orders/{order_id}/cancel")
async def cancel_paper_order(
    order_id: int,
    payload: PaperOrderCancelRequest,
    db: AsyncSession = Depends(get_db),
):
    """Cancel a working order. Cancelling an already cancelled order is a no-op."""
    order = await _paper_order(db, order_id)
    if order is None:
        raise HTTPException(404, "Paper order not found")
    if order.status == paper.CANCELED:
        return await _paper_order_detail(db, order)
    if order.status in paper.TERMINAL_STATUSES:
        raise HTTPException(409, f"A {order.status} order can no longer be canceled")
    portfolio = await get_or_create_portfolio(db)
    await _cancel_paper_order(
        db, order, portfolio, payload.reason or "Canceled by user", event_type="canceled"
    )
    for child in await _paper_children(db, order.id):
        if child.status not in paper.TERMINAL_STATUSES:
            await _cancel_paper_order(
                db,
                child,
                portfolio,
                f"Parent order {order.id} was canceled",
                event_type="canceled",
            )
    await _recompute_portfolio_equity(db, portfolio, snapshot=False)
    await db.commit()
    return await _paper_order_detail(db, order)


async def _cancel_oco_siblings(
    db: AsyncSession,
    order: PaperOrder,
    portfolio: Portfolio,
) -> list[int]:
    """One exit child filling cancels its siblings so a position cannot exit twice."""
    if not order.oco_group:
        return []
    siblings = (
        await db.execute(
            select(PaperOrder).where(
                PaperOrder.oco_group == order.oco_group,
                PaperOrder.id != order.id,
                PaperOrder.role.in_((paper.TAKE_PROFIT, paper.STOP_LOSS)),
            )
        )
    ).scalars().all()
    canceled: list[int] = []
    for sibling in siblings:
        if sibling.status in paper.TERMINAL_STATUSES:
            continue
        await _cancel_paper_order(
            db,
            sibling,
            portfolio,
            f"OCO: sibling order {order.id} ({order.role}) filled",
            event_type="oco_canceled",
        )
        canceled.append(sibling.id)
    return canceled


async def _activate_bracket_children(
    db: AsyncSession,
    parent: PaperOrder,
    candle_timestamp: datetime.datetime,
) -> list[int]:
    activated: list[int] = []
    for child in await _paper_children(db, parent.id):
        if child.status != paper.PENDING:
            continue
        child.trade_id = parent.trade_id
        child.last_candle_at = candle_timestamp
        _paper_audit(
            db,
            child,
            "activated",
            to_status=paper.SUBMITTED,
            message=f"Parent order {parent.id} filled; exit child is now working",
            detail={"parent_id": parent.id, "trade_id": parent.trade_id},
        )
        activated.append(child.id)
    return activated


async def _paper_entry_position(
    db: AsyncSession,
    order: PaperOrder,
    price: float,
) -> Trade:
    """Open the position a buy fill creates, inheriting bracket exit levels."""
    children = await _paper_children(db, order.id)
    stop = next(
        (child.stop_price for child in children if child.role == paper.STOP_LOSS and child.stop_price),
        None,
    )
    target = next(
        (child.limit_price for child in children if child.role == paper.TAKE_PROFIT and child.limit_price),
        None,
    )
    trade = Trade(
        ticker=order.ticker,
        direction=SignalDirection.BUY,
        entry_price=price,
        quantity=0.0,
        stop_loss=stop if stop else round(price * 0.95, 6),
        target_price=target if target else round(price * 1.15, 6),
        asset_type=order.asset_type,
        strategy_name="Paper Order",
        execution_context_json=json.dumps(
            {
                "source": "paper_order",
                "paper_order_id": order.id,
                "idempotency_key": order.idempotency_key,
                "order_type": order.order_type,
            }
        ),
        realized_quantity=0.0,
        status=TradeStatus.OPEN,
    )
    db.add(trade)
    await db.flush()
    return trade


async def _apply_paper_buy_fill(
    db: AsyncSession,
    order: PaperOrder,
    outcome: paper.CandleOutcome,
    portfolio: Portfolio,
) -> Optional[dict[str, object]]:
    assert outcome.fill_price is not None
    released = await _release_paper_reservation(db, order, portfolio, outcome.fill_quantity)
    cost = round(outcome.fill_quantity * outcome.fill_price + outcome.fees, 6)
    if cost > portfolio.balance + 1e-6:
        portfolio.balance = round(portfolio.balance - released, 6)
        order.reserved_cash = round((order.reserved_cash or 0.0) + released, 6)
        _paper_audit(
            db,
            order,
            "fill_blocked",
            message=(
                f"Insufficient cash for {outcome.fill_quantity:g} units at "
                f"${outcome.fill_price:,.4f}"
            ),
            detail={"required_cash": cost, "available_cash": round(portfolio.balance, 2)},
        )
        return None
    portfolio.balance = round(portfolio.balance - cost, 6)

    trade: Optional[Trade] = None
    if order.trade_id:
        trade = (
            await db.execute(select(Trade).where(Trade.id == order.trade_id))
        ).scalar_one_or_none()
    if trade is None:
        trade = await _paper_entry_position(db, order, outcome.fill_price)
        order.trade_id = trade.id
    previous_quantity = trade.quantity or 0.0
    total_quantity = paper.round_quantity(previous_quantity + outcome.fill_quantity)
    trade.entry_price = paper.round_price(
        (trade.entry_price * previous_quantity + outcome.fill_price * outcome.fill_quantity)
        / total_quantity
    )
    trade.quantity = total_quantity
    trade.entry_fees = round((trade.entry_fees or 0.0) + outcome.fees, 6)
    trade.entry_slippage = round((trade.entry_slippage or 0.0) + outcome.slippage, 6)
    db.add(TradeExecution(
        trade_id=trade.id,
        kind=ExecutionKind.ENTRY,
        price=outcome.fill_price,
        quantity=outcome.fill_quantity,
        fees=outcome.fees,
        slippage=outcome.slippage,
        note=f"Paper order {order.id} ({order.order_type})",
    ))
    return {"trade_id": trade.id, "cash_spent": cost, "released_reservation": released}


async def _apply_paper_sell_fill(
    db: AsyncSession,
    order: PaperOrder,
    outcome: paper.CandleOutcome,
) -> Optional[dict[str, object]]:
    assert outcome.fill_price is not None
    trade = await _paper_exit_position(db, order)
    if trade is None or trade.remaining_quantity <= paper.QUANTITY_EPSILON:
        _paper_audit(
            db,
            order,
            "fill_blocked",
            message="No open long position remains to sell",
        )
        return None
    quantity = paper.round_quantity(min(outcome.fill_quantity, trade.remaining_quantity))
    net_pnl, is_final_exit = await _apply_position_exit(
        db,
        trade,
        outcome.fill_price,
        quantity,
        outcome.fees,
        outcome.slippage,
        f"Paper order {order.id} ({order.order_type})",
    )
    order.trade_id = trade.id
    return {
        "trade_id": trade.id,
        "quantity": quantity,
        "net_pnl": net_pnl,
        "final_exit": is_final_exit,
    }


async def _apply_paper_fill(
    db: AsyncSession,
    order: PaperOrder,
    outcome: paper.CandleOutcome,
    candle: paper.Candle,
    portfolio: Portfolio,
) -> Optional[dict[str, object]]:
    """Book one simulated execution against cash, positions and the audit trail."""
    if order.side == paper.BUY:
        applied = await _apply_paper_buy_fill(db, order, outcome, portfolio)
        quantity = outcome.fill_quantity
    else:
        applied = await _apply_paper_sell_fill(db, order, outcome)
        quantity = float(applied["quantity"]) if applied else 0.0
    if applied is None or quantity <= paper.QUANTITY_EPSILON:
        return None

    assert outcome.fill_price is not None
    order.filled_quantity = paper.round_quantity((order.filled_quantity or 0.0) + quantity)
    order.filled_notional = round(
        (order.filled_notional or 0.0) + quantity * outcome.fill_price, 6
    )
    order.fees_total = round((order.fees_total or 0.0) + outcome.fees, 6)
    order.slippage_total = round((order.slippage_total or 0.0) + outcome.slippage, 6)
    order.average_fill_price = paper.round_price(order.filled_notional / order.filled_quantity)
    db.add(PaperOrderFill(
        order_id=order.id,
        quantity=quantity,
        price=outcome.fill_price,
        fees=outcome.fees,
        slippage=outcome.slippage,
        candle_timestamp=candle.timestamp,
        trade_id=applied.get("trade_id"),
    ))
    complete = order.remaining_quantity <= paper.QUANTITY_EPSILON
    _paper_audit(
        db,
        order,
        "fill" if complete else "partial_fill",
        to_status=paper.FILLED if complete else paper.PARTIALLY_FILLED,
        message=(
            f"Filled {quantity:g} at ${outcome.fill_price:,.4f}; "
            f"{order.remaining_quantity:g} remaining"
        ),
        detail={
            "quantity": quantity,
            "price": outcome.fill_price,
            "fees": outcome.fees,
            "slippage": outcome.slippage,
            "average_fill_price": order.average_fill_price,
            "remaining_quantity": order.remaining_quantity,
            "candle": _candle_detail(candle),
            **applied,
        },
    )
    if complete:
        if order.role == paper.ENTRY:
            activated = await _activate_bracket_children(db, order, candle.timestamp)
            if activated:
                _paper_audit(
                    db,
                    order,
                    "children_activated",
                    message=f"Activated bracket children {activated}",
                    detail={"children": activated},
                )
        elif order.role in (paper.TAKE_PROFIT, paper.STOP_LOSS):
            await _cancel_oco_siblings(db, order, portfolio)
    return applied


async def _expire_paper_order(
    db: AsyncSession,
    order: PaperOrder,
    portfolio: Portfolio,
    candle: paper.Candle,
) -> None:
    released = await _release_paper_reservation(db, order, portfolio)
    _paper_audit(
        db,
        order,
        "expired",
        to_status=paper.EXPIRED,
        message=f"Time in force elapsed at {candle.timestamp.isoformat()} before the order filled",
        detail={
            "expires_at": order.expires_at,
            "released_cash": released,
            "candle": _candle_detail(candle),
        },
    )


async def _paper_process_candles(
    db: AsyncSession,
    ticker: str,
    candles: list[paper.Candle],
    config: paper.FillConfig,
    order_id: Optional[int],
) -> tuple[list[int], list[int]]:
    """Replay candles oldest-first; each candle is applied to an order only once.

    Returns the touched order ids and the trades this replay fully closed.
    """
    portfolio = await get_or_create_portfolio(db)
    touched: list[int] = []
    closed_trades: list[int] = []
    for candle in candles:
        query = select(PaperOrder).where(PaperOrder.ticker == ticker).order_by(PaperOrder.id)
        if order_id is not None:
            query = query.where(PaperOrder.id == order_id)
        orders = (await db.execute(query)).scalars().all()
        for order in orders:
            if order.status not in paper.FILLABLE_STATUSES:
                continue
            if order.last_candle_at is not None and candle.timestamp <= order.last_candle_at:
                continue
            if order.expires_at is None and order.time_in_force == paper.DAY:
                order.expires_at = paper.day_session_end(candle.timestamp)
                _paper_audit(
                    db,
                    order,
                    "day_session_anchored",
                    message=(
                        f"DAY order anchored to the session of {candle.timestamp.isoformat()}; "
                        f"expires at {order.expires_at.isoformat()}"
                    ),
                    detail={
                        "expires_at": order.expires_at,
                        "candle": _candle_detail(candle),
                    },
                )
            if order.expires_at is not None and candle.timestamp >= order.expires_at:
                await _expire_paper_order(db, order, portfolio, candle)
                touched.append(order.id)
                continue
            order.last_candle_at = candle.timestamp
            state = paper.OrderState(
                side=order.side,
                order_type=order.order_type,
                quantity=order.quantity,
                filled_quantity=order.filled_quantity or 0.0,
                limit_price=order.limit_price,
                stop_price=order.stop_price,
                trail_percent=order.trail_percent,
                trail_amount=order.trail_amount,
                trail_reference_price=order.trail_reference_price,
                triggered=bool(order.triggered),
                role=order.role,
            )
            outcome = paper.simulate_candle(state, candle, config)
            if outcome.trail_reference_price is not None:
                moved = order.trail_reference_price != outcome.trail_reference_price
                order.trail_reference_price = outcome.trail_reference_price
                order.effective_stop_price = outcome.effective_stop_price
                if moved:
                    _paper_audit(
                        db,
                        order,
                        "trail_updated",
                        message=(
                            f"Trail reference {outcome.trail_reference_price:g}; effective stop "
                            f"{outcome.effective_stop_price:g}"
                        ),
                        detail={
                            "trail_reference_price": outcome.trail_reference_price,
                            "effective_stop_price": outcome.effective_stop_price,
                            "candle": _candle_detail(candle),
                        },
                    )
            if outcome.newly_triggered:
                order.triggered = True
                order.triggered_at = datetime.datetime.now(datetime.timezone.utc)
                _paper_audit(
                    db,
                    order,
                    "triggered",
                    message=f"Stop crossed at {candle.timestamp.isoformat()}",
                    detail={
                        "trigger_price": outcome.effective_stop_price or order.stop_price,
                        "candle": _candle_detail(candle),
                    },
                )
            if outcome.filled:
                applied = await _apply_paper_fill(db, order, outcome, candle, portfolio)
                if applied and applied.get("final_exit"):
                    closed_trades.append(int(applied["trade_id"]))
            else:
                _paper_audit(
                    db,
                    order,
                    "no_fill",
                    message=f"No fill from {candle.timestamp.isoformat()}: {outcome.reason}",
                    detail={"reason": outcome.reason, "candle": _candle_detail(candle)},
                )
            touched.append(order.id)
    await _recompute_portfolio_equity(db, portfolio)
    return touched, closed_trades


@app.post("/api/paper-orders/process")
async def process_paper_orders(
    payload: PaperOrderProcessRequest,
    db: AsyncSession = Depends(get_db),
):
    """Advance every working order for a ticker through deterministic candles.

    Candles supplied in the request are used verbatim; otherwise they are read
    from the data-ingestion service. Candles are never fabricated.
    """
    ticker = payload.ticker.strip().upper()
    if not ticker:
        raise HTTPException(400, "Ticker is required")
    raw_candles: list[dict[str, object]]
    if payload.candles:
        raw_candles = [candle.model_dump() for candle in payload.candles]
        source = "request"
    else:
        interval = payload.interval or settings.PAPER_ORDER_CANDLE_INTERVAL
        raw_candles = await _fetch_candles(ticker, interval)
        source = f"data-ingestion:{interval}"
    if not raw_candles:
        raise HTTPException(
            400,
            f"No candles available for {ticker}; supply candles in the request or ingest data first",
        )
    try:
        candles = paper.parse_candles(raw_candles)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    touched, closed_trades = await _paper_process_candles(
        db, ticker, candles, _paper_fill_config(payload), payload.order_id
    )
    await db.commit()
    for trade_id in closed_trades:
        trade = (await db.execute(select(Trade).where(Trade.id == trade_id))).scalar_one_or_none()
        if trade is not None and trade.status == TradeStatus.CLOSED:
            await _finalize_closed_trade(db, trade)
    orders = (
        await db.execute(
            select(PaperOrder).where(PaperOrder.id.in_(set(touched))).order_by(PaperOrder.id)
        )
    ).scalars().all() if touched else []
    portfolio = await get_or_create_portfolio(db)
    return {
        **PAPER_MODE,
        "ticker": ticker,
        "candle_source": source,
        "processed_candles": len(candles),
        "orders": [_paper_order_dict(order) for order in orders],
        "portfolio": {
            "balance": round(portfolio.balance, 2),
            "equity": round(portfolio.equity, 2),
            "reserved_cash": round(await _reserved_paper_cash(db), 2),
        },
    }


async def _get_json(url: str, params: Optional[dict[str, object]] = None) -> dict[str, object]:
    """Fetch a service payload, returning ``{"error": ...}`` instead of raising."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=8)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.warning("Service payload unavailable at %s: %s", url, exc)
        return {"error": str(exc) or exc.__class__.__name__}
    if not isinstance(payload, dict):
        return {"error": "Unexpected payload shape"}
    return payload


async def _fetch_scanner_status() -> dict[str, object]:
    return await _get_json(f"{settings.QUANT_ENGINE_URL}/api/scanner/status")


async def _fetch_data_quality() -> dict[str, object]:
    return await _get_json(f"{settings.DATA_INGESTION_URL}/api/data-quality")


async def _fetch_upcoming_earnings() -> dict[str, object]:
    return await _get_json(f"{settings.DATA_INGESTION_URL}/api/earnings/upcoming/all")


async def _fetch_data_status() -> dict[str, object]:
    return await _get_json(f"{settings.DATA_INGESTION_URL}/api/status")


async def _latest_closes(tickers: list[str]) -> dict[str, Optional[float]]:
    """Latest stored candle close per ticker; ``None`` when data-ingestion has none."""
    closes: dict[str, Optional[float]] = {}
    for ticker in sorted(set(tickers)):
        candles = await _fetch_candles(ticker, settings.ACTION_ITEM_CANDLE_INTERVAL)
        close = None
        if candles:
            try:
                close = float(candles[-1]["close"])  # type: ignore[arg-type]
            except (KeyError, TypeError, ValueError):
                close = None
        closes[ticker] = close
    return closes


def _action_user_key(request: Request) -> str:
    return get_current_user(request) or actions.DEFAULT_USER_KEY


async def _collect_action_candidates(db: AsyncSession) -> list[actions.ActionCandidate]:
    candidates: list[actions.ActionCandidate] = []

    scanner = await _fetch_scanner_status()
    if "error" in scanner:
        candidates.append(actions.operational_candidate("quant-engine scanner", str(scanner["error"])))
    else:
        candidates.extend(actions.build_opportunity_candidates(scanner))

    quality = await _fetch_data_quality()
    if "error" in quality:
        candidates.append(actions.operational_candidate("data-ingestion data quality", str(quality["error"])))
    else:
        candidates.extend(actions.build_data_quality_candidates(quality))

    earnings = await _fetch_upcoming_earnings()
    if "error" in earnings:
        candidates.append(actions.operational_candidate("data-ingestion earnings", str(earnings["error"])))
    else:
        candidates.extend(actions.build_earnings_candidates(earnings, settings.ACTION_EARNINGS_WINDOW_DAYS))

    open_result = await db.execute(select(Trade).where(Trade.status == TradeStatus.OPEN))
    open_trades = list(open_result.scalars().all())
    trade_rows = [
        {
            "id": trade.id,
            "ticker": trade.ticker,
            "direction": trade.direction.value if isinstance(trade.direction, SignalDirection) else str(trade.direction),
            "entry_price": trade.entry_price,
            "stop_loss": trade.trailing_stop or trade.stop_loss,
        }
        for trade in open_trades
    ]
    closes = await _latest_closes([str(row["ticker"]) for row in trade_rows])
    candidates.extend(actions.build_stop_proximity_candidates(
        trade_rows,
        closes,
        settings.ACTION_STOP_PROXIMITY_PCT,
    ))

    risk = await _portfolio_risk_status(db)
    breaker = risk.get("breaker")
    if isinstance(breaker, dict):
        candidates.extend(actions.build_breaker_candidates(breaker))

    order_result = await db.execute(
        select(PaperOrder).where(PaperOrder.status.in_(actions.ORDER_REVIEW_STATUSES))
    )
    candidates.extend(actions.build_order_review_candidates([
        {
            "id": order.id,
            "ticker": order.ticker,
            "status": order.status,
            "side": order.side,
            "order_type": order.order_type,
            "quantity": order.quantity,
            "reject_reason": order.reject_reason,
            "cancel_reason": order.cancel_reason,
        }
        for order in order_result.scalars().all()
    ]))
    return candidates


async def _expire_action_snoozes(db: AsyncSession, user_key: str) -> None:
    now = _utc_now()
    result = await db.execute(
        select(ActionItem).where(
            ActionItem.user_key == user_key,
            ActionItem.status == actions.STATUS_SNOOZED,
        )
    )
    changed = False
    for item in result.scalars().all():
        if actions.expired_snooze(item.status, item.snoozed_until, now):
            item.status = actions.STATUS_OPEN
            item.snoozed_until = None
            changed = True
    if changed:
        await db.commit()


async def _refresh_action_items(db: AsyncSession, user_key: str) -> dict[str, int]:
    """Upsert every active source into the durable inbox without creating duplicates."""
    now = _utc_now()
    candidates = await _collect_action_candidates(db)
    existing_result = await db.execute(select(ActionItem).where(ActionItem.user_key == user_key))
    existing = {item.source_key: item for item in existing_result.scalars().all()}
    created = 0
    updated = 0
    for candidate in candidates:
        item = existing.get(candidate.source_key)
        if item is None:
            db.add(ActionItem(
                user_key=user_key,
                source_key=candidate.source_key,
                source_type=candidate.source_type,
                category=candidate.category,
                severity=candidate.severity,
                is_mandatory=candidate.is_mandatory,
                title=candidate.title,
                message=candidate.message,
                ticker=candidate.ticker,
                trade_id=candidate.trade_id,
                order_id=candidate.order_id,
                context_id=candidate.context_id,
                deep_link_tab=candidate.deep_link_tab,
                deep_link_json=json.dumps(candidate.deep_link, sort_keys=True, default=str),
                payload_json=json.dumps(candidate.payload, sort_keys=True, default=str),
                payload_hash=candidate.payload_hash,
                status=actions.STATUS_OPEN,
                source_active=True,
                first_seen_at=now,
                last_seen_at=now,
            ))
            created += 1
            continue
        changed_payload = item.payload_hash != candidate.payload_hash
        was_active = item.source_active
        item.source_type = candidate.source_type
        item.category = candidate.category
        item.severity = candidate.severity
        item.is_mandatory = candidate.is_mandatory
        item.title = candidate.title
        item.message = candidate.message
        item.ticker = candidate.ticker
        item.trade_id = candidate.trade_id
        item.order_id = candidate.order_id
        item.context_id = candidate.context_id
        item.deep_link_tab = candidate.deep_link_tab
        item.deep_link_json = json.dumps(candidate.deep_link, sort_keys=True, default=str)
        item.payload_json = json.dumps(candidate.payload, sort_keys=True, default=str)
        item.payload_hash = candidate.payload_hash
        item.source_active = True
        item.last_seen_at = now
        if actions.expired_snooze(item.status, item.snoozed_until, now):
            item.status = actions.STATUS_OPEN
            item.snoozed_until = None
        if item.status == actions.STATUS_RESOLVED and (changed_payload or not was_active):
            item.status = actions.STATUS_OPEN
            item.resolved_at = None
        updated += 1
    active_keys = {candidate.source_key for candidate in candidates}
    cleared = 0
    for source_key, item in existing.items():
        if source_key in active_keys or not item.source_active:
            continue
        item.source_active = False
        item.status = actions.STATUS_RESOLVED
        item.resolved_at = now
        item.snoozed_until = None
        cleared += 1
    await db.commit()
    return {"created": created, "updated": updated, "cleared": cleared}


def _action_item_dict(item: ActionItem) -> dict[str, object]:
    return {
        "id": item.id,
        "source_key": item.source_key,
        "source_type": item.source_type,
        "category": item.category,
        "severity": item.severity,
        "is_mandatory": bool(item.is_mandatory),
        "title": item.title,
        "message": item.message,
        "ticker": item.ticker,
        "trade_id": item.trade_id,
        "order_id": item.order_id,
        "context_id": item.context_id,
        "deep_link": {
            **(_json_field(item.deep_link_json) or {}),
            "tab": item.deep_link_tab,
        },
        "payload": _json_field(item.payload_json) or {},
        "payload_hash": item.payload_hash,
        "status": item.status,
        "source_active": bool(item.source_active),
        "snoozed_until": item.snoozed_until.isoformat() if item.snoozed_until else None,
        "first_seen_at": item.first_seen_at.isoformat() if item.first_seen_at else None,
        "last_seen_at": item.last_seen_at.isoformat() if item.last_seen_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "acknowledged_at": item.acknowledged_at.isoformat() if item.acknowledged_at else None,
        "snoozed_at": item.snoozed_at.isoformat() if item.snoozed_at else None,
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
    }


def _csv_filter(raw: Optional[str], allowed: tuple[str, ...], name: str) -> Optional[set[str]]:
    if not raw:
        return None
    values = {value.strip() for value in raw.split(",") if value.strip()}
    unknown = values - set(allowed)
    if unknown:
        raise HTTPException(400, f"Unknown {name} filter: {', '.join(sorted(unknown))}")
    return values or None


async def _action_items_payload(
    db: AsyncSession,
    user_key: str,
    status: Optional[str],
    category: Optional[str],
    severity: Optional[str],
    source_type: Optional[str],
) -> dict[str, object]:
    await _expire_action_snoozes(db, user_key)
    statuses = _csv_filter(status, actions.STATUSES, "status")
    categories = _csv_filter(category, actions.CATEGORIES, "category")
    severities = _csv_filter(severity, actions.SEVERITIES, "severity")
    source_types = _csv_filter(source_type, tuple(actions.SOURCE_TYPES), "source_type")
    result = await db.execute(select(ActionItem).where(ActionItem.user_key == user_key))
    rows = [_action_item_dict(item) for item in result.scalars().all()]

    def keep(row: dict[str, object]) -> bool:
        if statuses and str(row["status"]) not in statuses:
            return False
        if row["is_mandatory"]:
            # Mandatory items ignore ordinary category/severity/source filters.
            return True
        if categories and str(row["category"]) not in categories:
            return False
        if severities and str(row["severity"]) not in severities:
            return False
        if source_types and str(row["source_type"]) not in source_types:
            return False
        return True

    visible = sorted((row for row in rows if keep(row)), key=actions.sort_key)
    return {
        "user_key": user_key,
        "items": visible,
        "counts": _action_counts(rows),
        "filters_applied": {
            "status": sorted(statuses) if statuses else None,
            "category": sorted(categories) if categories else None,
            "severity": sorted(severities) if severities else None,
            "source_type": sorted(source_types) if source_types else None,
        },
        "mandatory_note": (
            "Critical risk-breaker and blocked-data items stay visible even when "
            "opportunity or category filters are applied."
        ),
    }


def _action_counts(rows: list[dict[str, object]]) -> dict[str, object]:
    unresolved = [row for row in rows if row["status"] != actions.STATUS_RESOLVED]
    by_status = {status: sum(row["status"] == status for row in rows) for status in actions.STATUSES}
    by_severity = {
        level: sum(row["severity"] == level for row in unresolved) for level in actions.SEVERITIES
    }
    by_category = {
        name: sum(row["category"] == name for row in unresolved) for name in actions.CATEGORIES
    }
    return {
        "total": len(rows),
        "unresolved": len(unresolved),
        "open": by_status[actions.STATUS_OPEN],
        "mandatory": sum(bool(row["is_mandatory"]) for row in unresolved),
        "by_status": by_status,
        "by_severity": by_severity,
        "by_category": by_category,
    }


@app.get("/api/action-items")
async def list_action_items(
    request: Request,
    status: Optional[str] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    source_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    user_key = _action_user_key(request)
    return await _action_items_payload(db, user_key, status, category, severity, source_type)


@app.post("/api/action-items/refresh")
async def refresh_action_items(
    request: Request,
    status: Optional[str] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    source_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    user_key = _action_user_key(request)
    refreshed = await _refresh_action_items(db, user_key)
    payload = await _action_items_payload(db, user_key, status, category, severity, source_type)
    return {**payload, "refreshed": refreshed}


async def _action_item(db: AsyncSession, user_key: str, item_id: int) -> ActionItem:
    result = await db.execute(
        select(ActionItem).where(ActionItem.id == item_id, ActionItem.user_key == user_key)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "Action item not found")
    return item


@app.get("/api/action-items/{item_id}")
async def get_action_item(item_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user_key = _action_user_key(request)
    await _expire_action_snoozes(db, user_key)
    return _action_item_dict(await _action_item(db, user_key, item_id))


class ActionSnoozeRequest(BaseModel):
    minutes: int = Field(60, gt=0, le=60 * 24 * 30)


@app.post("/api/action-items/{item_id}/acknowledge")
async def acknowledge_action_item(item_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user_key = _action_user_key(request)
    item = await _action_item(db, user_key, item_id)
    if item.status != actions.STATUS_ACKNOWLEDGED:
        item.status = actions.STATUS_ACKNOWLEDGED
        item.acknowledged_at = _utc_now()
        item.snoozed_until = None
        await db.commit()
    return _action_item_dict(item)


@app.post("/api/action-items/{item_id}/snooze")
async def snooze_action_item(
    item_id: int,
    payload: ActionSnoozeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_key = _action_user_key(request)
    item = await _action_item(db, user_key, item_id)
    now = _utc_now()
    item.status = actions.STATUS_SNOOZED
    item.snoozed_at = now
    item.snoozed_until = now + datetime.timedelta(minutes=payload.minutes)
    await db.commit()
    return _action_item_dict(item)


@app.post("/api/action-items/{item_id}/resolve")
async def resolve_action_item(item_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user_key = _action_user_key(request)
    item = await _action_item(db, user_key, item_id)
    if item.status != actions.STATUS_RESOLVED:
        item.status = actions.STATUS_RESOLVED
        item.resolved_at = _utc_now()
        item.snoozed_until = None
        await db.commit()
    return _action_item_dict(item)


@app.post("/api/action-items/{item_id}/reopen")
async def reopen_action_item(item_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user_key = _action_user_key(request)
    item = await _action_item(db, user_key, item_id)
    if item.status != actions.STATUS_OPEN:
        item.status = actions.STATUS_OPEN
        item.resolved_at = None
        item.snoozed_until = None
        await db.commit()
    return _action_item_dict(item)


async def _get_or_create_preference(db: AsyncSession, user_key: str) -> DashboardPreference:
    result = await db.execute(
        select(DashboardPreference).where(DashboardPreference.user_key == user_key)
    )
    preference = result.scalar_one_or_none()
    if preference is None:
        preference = DashboardPreference(
            user_key=user_key,
            widgets_json=json.dumps(actions.default_widgets()),
            mode="detailed",
            layouts_json="{}",
        )
        db.add(preference)
        await db.commit()
        await db.refresh(preference)
    return preference


def _preference_dict(preference: DashboardPreference) -> dict[str, object]:
    layouts_raw = _json_field(preference.layouts_json) or {}
    layouts = {
        name: {
            "widgets": actions.normalize_widgets((layout or {}).get("widgets")),
            "mode": actions.normalize_mode((layout or {}).get("mode")),
        }
        for name, layout in layouts_raw.items()
        if isinstance(layout, dict)
    }
    try:
        stored_widgets = json.loads(preference.widgets_json)
    except ValueError:
        stored_widgets = []
    return {
        "user_key": preference.user_key,
        "widgets": actions.normalize_widgets(stored_widgets),
        "mode": actions.normalize_mode(preference.mode),
        "layouts": layouts,
        "available_widgets": list(actions.WIDGET_IDS),
        "updated_at": preference.updated_at.isoformat() if preference.updated_at else None,
    }


class DashboardWidgetPreference(BaseModel):
    id: str
    enabled: bool = True


class DashboardPreferenceRequest(BaseModel):
    widgets: list[DashboardWidgetPreference]
    mode: str = "detailed"


def _validated_widgets(widgets: list[DashboardWidgetPreference]) -> list[dict[str, object]]:
    unknown = [widget.id for widget in widgets if widget.id not in actions.WIDGET_IDS]
    if unknown:
        raise HTTPException(400, f"Unknown widget id: {', '.join(sorted(set(unknown)))}")
    return actions.normalize_widgets([widget.model_dump() for widget in widgets])


def _validated_mode(mode: str) -> str:
    if mode not in actions.MODES:
        raise HTTPException(400, f"Mode must be one of: {', '.join(actions.MODES)}")
    return mode


@app.get("/api/dashboard-preferences")
async def get_dashboard_preferences(request: Request, db: AsyncSession = Depends(get_db)):
    return _preference_dict(await _get_or_create_preference(db, _action_user_key(request)))


@app.put("/api/dashboard-preferences")
async def update_dashboard_preferences(
    payload: DashboardPreferenceRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    preference = await _get_or_create_preference(db, _action_user_key(request))
    preference.widgets_json = json.dumps(_validated_widgets(payload.widgets))
    preference.mode = _validated_mode(payload.mode)
    await db.commit()
    return _preference_dict(preference)


@app.post("/api/dashboard-preferences/reset")
async def reset_dashboard_preferences(request: Request, db: AsyncSession = Depends(get_db)):
    preference = await _get_or_create_preference(db, _action_user_key(request))
    preference.widgets_json = json.dumps(actions.default_widgets())
    preference.mode = "detailed"
    await db.commit()
    return _preference_dict(preference)


@app.put("/api/dashboard-preferences/layouts/{name}")
async def save_dashboard_layout(
    name: str,
    payload: DashboardPreferenceRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    layout_name = name.strip()
    if not layout_name or len(layout_name) > 60:
        raise HTTPException(400, "Layout name must be 1-60 characters")
    preference = await _get_or_create_preference(db, _action_user_key(request))
    layouts = _json_field(preference.layouts_json) or {}
    layouts[layout_name] = {
        "widgets": _validated_widgets(payload.widgets),
        "mode": _validated_mode(payload.mode),
    }
    preference.layouts_json = json.dumps(layouts, sort_keys=True)
    await db.commit()
    return _preference_dict(preference)


@app.delete("/api/dashboard-preferences/layouts/{name}")
async def delete_dashboard_layout(name: str, request: Request, db: AsyncSession = Depends(get_db)):
    preference = await _get_or_create_preference(db, _action_user_key(request))
    layouts = _json_field(preference.layouts_json) or {}
    if name not in layouts:
        raise HTTPException(404, "Saved layout not found")
    layouts.pop(name)
    preference.layouts_json = json.dumps(layouts, sort_keys=True)
    await db.commit()
    return _preference_dict(preference)


@app.get("/api/alerts", response_model=list[AlertLogResponse])
async def list_alerts(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlertLog).order_by(desc(AlertLog.created_at)).limit(limit),
    )
    return result.scalars().all()


@app.get("/api/settings/credentials")
async def credential_status(db: AsyncSession = Depends(get_db)):
    return await credential_status_all(db)


@app.get("/api/settings/credentials/all")
async def credential_status_all(db: AsyncSession = Depends(get_db)):
    """Aggregate masked credential status from DB and current env."""
    result = await db.execute(select(CredentialSecret))
    rows = result.scalars().all()
    by_key = {r.key: r for r in rows}
    env = _read_env(_find_env_path())
    providers = {}
    for provider, keys in PROVIDER_KEYS.items():
        configured = []
        verified = []
        masked = {}
        errors = {}
        for key in keys:
            row = by_key.get(key)
            raw = _decode_secret(row.value) if row else env.get(key, "")
            if raw:
                configured.append(key)
                masked[key] = _mask(raw)
            if row and row.verified:
                verified.append(key)
            if row and row.last_error:
                errors[key] = row.last_error
        providers[provider] = {
            "configured": len(configured) > 0,
            "verified": bool(verified) or (len(configured) == len(keys)),
            "configured_keys": configured,
            "verified_keys": verified,
            "masked": masked,
            "errors": errors,
        }
    return providers


def _find_env_path() -> Path:
    """Find the host-mounted .env when available, otherwise project root."""
    explicit = os.getenv("HOST_ENV_PATH")
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    candidates = [Path(explicit)] if explicit else []
    candidates.extend([project_root / ".env", Path("/workspace/.env"), Path(".env")])
    logger.info(f"Looking for .env file. HOST_ENV_PATH={explicit}, candidates={candidates}")
    for p in candidates:
        exists = p.exists()
        logger.info(f"Checking {p}: exists={exists}")
        if exists:
            logger.info(f"Found .env file at: {p}")
            return p
    logger.warning(f"No .env file found, returning default: {project_root / '.env'}")
    return project_root / ".env"


def _read_env(path: Path) -> dict[str, str]:
    """Read key=value pairs from a .env file."""
    env: dict[str, str] = {}
    logger.info(f"Reading env from: {path}, exists={path.exists()}")
    if not path.exists():
        logger.warning(f"Env file does not exist: {path}")
        return env
    content = path.read_text()
    logger.info(f"Env file content length: {len(content)} chars")
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _write_env(path: Path, env: dict[str, str]) -> None:
    """Write key=value pairs to .env, preserving comments from .env.example."""
    example_path = path.parent / ".env.example"
    lines: list[str] = []
    written_keys: set[str] = set()

    template_path = example_path if example_path.exists() else None
    if template_path:
        for line in template_path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                lines.append(line)
                continue
            if "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in env:
                    lines.append(f"{key}={env[key]}")
                    written_keys.add(key)
                else:
                    lines.append(line)

    for k, v in env.items():
        if k not in written_keys:
            lines.append(f"{k}={v}")

    path.write_text("\n".join(lines) + "\n")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


@app.post("/api/settings/credentials/save")
async def save_credentials(req: CredentialSaveRequest, db: AsyncSession = Depends(get_db)):
    """Save credentials to DB and .env without overwriting verified secrets by default."""
    allowed_keys = {key for keys in PROVIDER_KEYS.values() for key in keys}
    filtered = {k: v for k, v in req.credentials.items() if k in allowed_keys and v}
    if not filtered:
        raise HTTPException(400, "No valid credentials provided")

    env_path = _find_env_path()
    existing = _read_env(env_path)
    saved = []
    skipped = []
    for key, value in filtered.items():
        changed = await _save_secret(db, key, value, verified=True, overwrite=req.overwrite)
        if changed:
            existing[key] = value
            saved.append(key)
        else:
            skipped.append(key)
    await db.commit()
    _write_env(env_path, existing)

    logger.info(f"Credentials saved via UI: {saved}")
    return {
        "saved": saved,
        "skipped": skipped,
        "message": "Credentials saved and synced to .env.",
    }


@app.post("/api/settings/credentials/reveal")
async def reveal_credential(req: CredentialRevealRequest, db: AsyncSession = Depends(get_db)):
    if req.key not in {key for keys in PROVIDER_KEYS.values() for key in keys}:
        raise HTTPException(400, "Credential key is not allowed")
    row = await _get_secret(db, req.key)
    if row:
        return {"key": req.key, "value": _decode_secret(row.value)}
    return {"key": req.key, "value": _read_env(_find_env_path()).get(req.key, "")}




@app.get("/api/settings/onboarding")
async def onboarding_status():
    """Check if the user has completed onboarding (has market data API credentials)."""
    env_path = _find_env_path()
    env = _read_env(env_path)
    # Only require market data APIs to skip onboarding
    has_market_data_cred = any(
        env.get(k)
        for k in [
            "BINANCE_API_KEY",
            "ALPACA_API_KEY",
        ]
    )
    has_any_asset = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.DATA_INGESTION_URL}/api/assets")
            if resp.status_code == 200:
                has_any_asset = len(resp.json()) > 0
    except Exception:
        pass

    return {
        "completed": has_market_data_cred,
        "has_credentials": has_market_data_cred,
        "has_assets": has_any_asset,
    }


# Environment settings that can be viewed and adjusted
ADJUSTABLE_SETTINGS = {
    "RISK_REWARD_RATIO": {"type": "float", "default": 3.0, "description": "Target risk/reward ratio for signals"},
    "ATR_STOP_MULTIPLIER": {"type": "float", "default": 1.5, "description": "ATR multiplier for stop loss calculation"},
    "ATR_VOLATILITY_THRESHOLD": {"type": "float", "default": 2.0, "description": "ATR threshold for volatility filtering"},
    "TRAILING_STOP_PCT": {"type": "float", "default": 0.02, "description": "Trailing stop percentage (as decimal)"},
    "INITIAL_BALANCE": {"type": "float", "default": 10000.0, "description": "Initial paper trading balance"},
    "LOSS_TOLERANCE_PCT": {"type": "float", "default": 0.02, "description": "Max loss tolerance per trade as % of balance (e.g. 0.02 = 2%)"},
    "MAX_PORTFOLIO_HEAT_PCT": {"type": "float", "default": 0.08, "description": "Maximum correlation-adjusted risk-to-stop across open positions"},
    "MAX_TICKER_EXPOSURE_PCT": {"type": "float", "default": 0.20, "description": "Maximum notional exposure to one ticker"},
    "MAX_SECTOR_EXPOSURE_PCT": {"type": "float", "default": 0.35, "description": "Maximum notional exposure to one classified sector"},
    "MAX_ASSET_CLASS_EXPOSURE_PCT": {"type": "float", "default": 0.70, "description": "Maximum stock or crypto notional exposure"},
    "MAX_DIRECTIONAL_EXPOSURE_PCT": {"type": "float", "default": 0.80, "description": "Maximum long or short notional exposure"},
    "MAX_CORRELATED_EXPOSURE_PCT": {"type": "float", "default": 0.40, "description": "Maximum exposure in a highly correlated position cluster"},
    "CORRELATION_THRESHOLD": {"type": "float", "default": 0.75, "description": "Rolling return correlation treated as highly correlated"},
    "DAILY_LOSS_LIMIT_PCT": {"type": "float", "default": 0.03, "description": "Daily realized-loss circuit breaker"},
    "WEEKLY_LOSS_LIMIT_PCT": {"type": "float", "default": 0.06, "description": "Weekly realized-loss circuit breaker"},
    "MAX_DRAWDOWN_PCT": {"type": "float", "default": 0.12, "description": "Peak-to-current equity drawdown circuit breaker"},
    "VOLATILITY_TARGET_PCT": {"type": "float", "default": 0.15, "description": "Annualized volatility target used to reduce proposed size"},
    "FRACTIONAL_KELLY_CAP": {"type": "float", "default": 0.10, "description": "Maximum fraction of equity allocated by Kelly sizing"},
    "EQUITY_SHOCK_PCT": {"type": "float", "default": 0.05, "description": "Broad-equity stress scenario decline"},
    "CRYPTO_SHOCK_PCT": {"type": "float", "default": 0.20, "description": "Crypto stress scenario decline"},
}
RISK_PERCENTAGE_SETTINGS = {
    "MAX_PORTFOLIO_HEAT_PCT",
    "MAX_TICKER_EXPOSURE_PCT",
    "MAX_SECTOR_EXPOSURE_PCT",
    "MAX_ASSET_CLASS_EXPOSURE_PCT",
    "MAX_DIRECTIONAL_EXPOSURE_PCT",
    "MAX_CORRELATED_EXPOSURE_PCT",
    "CORRELATION_THRESHOLD",
    "DAILY_LOSS_LIMIT_PCT",
    "WEEKLY_LOSS_LIMIT_PCT",
    "MAX_DRAWDOWN_PCT",
    "VOLATILITY_TARGET_PCT",
    "FRACTIONAL_KELLY_CAP",
    "EQUITY_SHOCK_PCT",
    "CRYPTO_SHOCK_PCT",
}

@app.get("/api/settings/env")
async def get_env_settings():
    """Get current environment settings (non-sensitive, viewable/adjustable)."""
    env_path = _find_env_path()
    env = _read_env(env_path)
    
    settings = {}
    for key, meta in ADJUSTABLE_SETTINGS.items():
        value = env.get(key)
        if value is not None:
            try:
                if meta["type"] == "float":
                    value = float(value)
                elif meta["type"] == "int":
                    value = int(value)
            except ValueError:
                value = meta["default"]
        else:
            value = meta["default"]
        settings[key] = {
            "value": value,
            "default": meta["default"],
            "type": meta["type"],
            "description": meta["description"],
        }
    return settings


@app.get("/api/settings/env-debug")
async def debug_env_path():
    """Debug endpoint to check env file path detection."""
    explicit = os.getenv("HOST_ENV_PATH")
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    candidates = [Path(explicit)] if explicit else []
    candidates.extend([project_root / ".env", Path("/workspace/.env"), Path(".env")])
    
    results = []
    for p in candidates:
        results.append({
            "path": str(p),
            "exists": p.exists(),
            "is_file": p.is_file() if p.exists() else False,
        })
    
    chosen = _find_env_path()
    env_data = _read_env(chosen)
    
    return {
        "host_env_path": explicit,
        "project_root": str(project_root),
        "candidates": results,
        "chosen_path": str(chosen),
        "chosen_exists": chosen.exists(),
        "env_keys": list(env_data.keys()),
        "sample_values": {k: env_data.get(k) for k in list(env_data.keys())[:5]},
    }


@app.post("/api/settings/env")
async def update_env_setting(payload: dict):
    """Update a single environment setting."""
    key = payload.get("key")
    value = payload.get("value")
    
    if key not in ADJUSTABLE_SETTINGS:
        raise HTTPException(400, f"Setting {key} is not adjustable")
    
    meta = ADJUSTABLE_SETTINGS[key]
    try:
        if meta["type"] == "float":
            value = float(value)
        elif meta["type"] == "int":
            value = int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"Invalid value type for {key}, expected {meta['type']}")
    if key in RISK_PERCENTAGE_SETTINGS and not 0 < value <= 1:
        raise HTTPException(400, f"{key} must be greater than 0 and no more than 1")
    
    env_path = _find_env_path()
    env = _read_env(env_path)
    env[key] = str(value)
    _write_env(env_path, env)
    
    return {"key": key, "value": value, "message": f"{key} updated successfully"}


class BalanceUpdateRequest(BaseModel):
    balance: float


@app.post("/api/portfolio/balance")
async def update_portfolio_balance(req: BalanceUpdateRequest, db: AsyncSession = Depends(get_db)):
    """Update the user's available trading balance."""
    if req.balance < 0:
        raise HTTPException(400, "Balance cannot be negative")
    portfolio = await get_or_create_portfolio(db)
    old_balance = portfolio.balance

    # Compute capital committed to open positions
    open_result = await db.execute(select(Trade).where(Trade.status == TradeStatus.OPEN))
    open_trades = open_result.scalars().all()
    locked_capital = sum(t.quantity * t.entry_price for t in open_trades)

    # The user declares their total available capital; equity = balance + locked
    portfolio.balance = req.balance
    portfolio.equity = req.balance + locked_capital
    if portfolio.equity > portfolio.peak_equity:
        portfolio.peak_equity = portfolio.equity
    db.add(EquitySnapshot(equity=portfolio.equity, balance=portfolio.balance))
    await db.commit()
    return {
        "previous_balance": round(old_balance, 2),
        "new_balance": round(portfolio.balance, 2),
        "equity": round(portfolio.equity, 2),
        "locked_in_positions": round(locked_capital, 2),
        "message": f"Available balance updated to ${req.balance:,.2f}",
    }


@app.get("/api/portfolio/recommendation")
async def trade_recommendation(
    ticker: str,
    current_price: float,
    direction: str = "BUY",
    asset_type: str = "stock",
    sector: str = "Unclassified",
    db: AsyncSession = Depends(get_db),
):
    """Get a balance-aware trade recommendation with portfolio risk approval."""
    if current_price <= 0:
        raise HTTPException(400, "Current price must be positive")
    direction = direction.upper()
    if direction not in ("BUY", "SELL"):
        raise HTTPException(400, "Direction must be BUY or SELL")
    ticker = ticker.strip().upper()
    portfolio, positions, closed_trades, returns_by_ticker = await _risk_context(db, {ticker})
    env = _read_env(_find_env_path())
    loss_tolerance = float(env.get("LOSS_TOLERANCE_PCT", "0.02"))
    risk_reward = float(env.get("RISK_REWARD_RATIO", "3.0"))
    atr_multiplier = float(env.get("ATR_STOP_MULTIPLIER", "1.5"))
    trailing_stop_pct = float(env.get("TRAILING_STOP_PCT", "0.02"))

    max_loss_amount = portfolio.balance * loss_tolerance
    stop_distance_pct = trailing_stop_pct * atr_multiplier
    if direction == "BUY":
        suggested_stop = current_price * (1 - stop_distance_pct)
        suggested_target = current_price * (1 + stop_distance_pct * risk_reward)
    else:
        suggested_stop = current_price * (1 + stop_distance_pct)
        suggested_target = current_price * (1 - stop_distance_pct * risk_reward)
    risk_per_unit = abs(current_price - suggested_stop)
    suggested_quantity = max_loss_amount / risk_per_unit if risk_per_unit > 0 else 0
    suggested_position_usd = suggested_quantity * current_price
    if portfolio.balance > 0 and suggested_position_usd > portfolio.balance:
        suggested_quantity = portfolio.balance / current_price
        suggested_position_usd = portfolio.balance

    proposal = PositionInput(
        ticker=ticker,
        direction=direction,
        entry_price=current_price,
        quantity=suggested_quantity,
        stop_loss=suggested_stop,
        asset_type=asset_type,
        sector=sector,
        returns=returns_by_ticker.get(ticker, {}),
    )
    limits = _risk_limits()
    risk_decision = evaluate_proposed_position(
        positions,
        proposal,
        closed_trades,
        portfolio.equity,
        portfolio.peak_equity,
        limits,
        annual_volatility=annualized_volatility(proposal.returns, asset_type),
    )
    recommended_size = float(risk_decision["recommended_size_usd"])
    suggested_position_usd = recommended_size
    suggested_quantity = recommended_size / current_price if recommended_size > 0 else 0.0
    position_pct = (suggested_position_usd / portfolio.balance * 100) if portfolio.balance > 0 else 0

    return {
        "ticker": ticker,
        "direction": direction,
        "asset_type": asset_type,
        "sector": sector,
        "account_balance": round(portfolio.balance, 2),
        "loss_tolerance_pct": loss_tolerance,
        "max_loss_amount": round(max_loss_amount, 2),
        "current_price": round(current_price, 2),
        "suggested_stop_loss": round(suggested_stop, 2),
        "suggested_target": round(suggested_target, 2),
        "suggested_quantity": round(suggested_quantity, 4),
        "suggested_position_usd": round(suggested_position_usd, 2),
        "position_pct_of_balance": round(position_pct, 2),
        "risk_reward_ratio": risk_reward,
        "risk_decision": risk_decision,
    }


# --- Phase 8: Multi-User Auth ---

class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/register")
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user (only when AUTH_ENABLED=true)."""
    if not settings.AUTH_ENABLED:
        raise HTTPException(400, "Auth is not enabled. Set AUTH_ENABLED=true to use multi-user mode.")
    existing = await db.execute(select(User).where(User.username == req.username))
    if existing.scalars().first():
        raise HTTPException(409, "Username already taken")
    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        display_name=req.display_name or req.username,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    token = create_token(str(user.id), user.username)
    return {"token": token, "user_id": user.id, "username": user.username}


@app.post("/api/auth/login")
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login and get a JWT token."""
    if not settings.AUTH_ENABLED:
        raise HTTPException(400, "Auth is not enabled. Set AUTH_ENABLED=true to use multi-user mode.")
    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalars().first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    if not user.is_active:
        raise HTTPException(403, "Account deactivated")
    token = create_token(str(user.id), user.username)
    return {"token": token, "user_id": user.id, "username": user.username}


@app.get("/api/auth/status")
async def auth_status():
    """Return whether multi-user auth is enabled."""
    return {"auth_enabled": settings.AUTH_ENABLED}


# ---------------------------------------------------------------------------
# Guarded live-broker execution
#
# Live trading is disabled by default and stays unreachable until three
# independent conditions hold: LIVE_TRADING_ENABLED is true, a durable operator
# acknowledgement exists, and no kill switch is engaged. Every submission must
# echo the fingerprint of a preview it was approved from, and every gate failure
# is stored as a rejected order with a hash-chained audit trail.
# ---------------------------------------------------------------------------


class LiveOrderRequestBody(BaseModel):
    ticker: str
    side: str
    order_type: str
    quantity: float
    asset_type: str = "stock"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: str = live.DAY


class LiveOrderSubmitBody(LiveOrderRequestBody):
    idempotency_key: str
    approval_fingerprint: str
    approved_by: Optional[str] = None


class LiveAcknowledgeBody(BaseModel):
    phrase: str
    note: Optional[str] = None


class LiveDisableBody(BaseModel):
    reason: Optional[str] = None


class LiveCancelBody(BaseModel):
    reason: Optional[str] = None


def get_broker() -> Optional[BrokerAdapter]:
    """Resolve the configured broker adapter.

    Returns ``None`` when no adapter is configured; tests override this
    dependency with a fake adapter so no sandbox or production account is
    required for the guard logic.
    """
    if settings.LIVE_BROKER.strip().lower() != "alpaca":
        return None
    broker = AlpacaBroker(
        api_key=settings.ALPACA_API_KEY,
        api_secret=settings.ALPACA_SECRET_KEY,
        base_url=settings.LIVE_BROKER_BASE_URL,
    )
    return broker


def _live_user_key(request: Request) -> str:
    return get_current_user(request) or "default"


async def _live_control(db: AsyncSession) -> LiveTradingControl:
    control = (
        await db.execute(select(LiveTradingControl).order_by(LiveTradingControl.id).limit(1))
    ).scalar_one_or_none()
    if control is None:
        control = LiveTradingControl(acknowledged=False, trading_disabled=False)
        db.add(control)
        await db.flush()
    return control


async def _live_audit(
    db: AsyncSession,
    *,
    event_type: str,
    message: str,
    actor: str,
    record: dict[str, object],
    order: Optional[LiveOrder] = None,
) -> LiveExecutionAudit:
    """Append one hash-chained audit entry."""
    previous = (
        await db.execute(
            select(LiveExecutionAudit).order_by(desc(LiveExecutionAudit.id)).limit(1)
        )
    ).scalar_one_or_none()
    previous_hash = previous.entry_hash if previous else ""
    body = {
        "event_type": event_type,
        "actor": actor,
        "message": message,
        "order_id": order.id if order else None,
        "record": record,
    }
    entry = LiveExecutionAudit(
        order_id=order.id if order else None,
        event_type=event_type,
        actor=actor,
        message=message,
        record_json=json.dumps(body, sort_keys=True, default=str),
        previous_hash=previous_hash,
        entry_hash=live.audit_hash(previous_hash, body),
    )
    db.add(entry)
    await db.flush()
    return entry


def _audit_dict(entry: LiveExecutionAudit) -> dict[str, object]:
    return {
        "id": entry.id,
        "order_id": entry.order_id,
        "event_type": entry.event_type,
        "actor": entry.actor,
        "message": entry.message,
        "record": _json_field(entry.record_json),
        "previous_hash": entry.previous_hash,
        "entry_hash": entry.entry_hash,
        "created_at": entry.created_at,
    }


def _live_order_request(payload: LiveOrderRequestBody) -> live.OrderRequest:
    return live.OrderRequest(
        ticker=payload.ticker.strip().upper(),
        asset_type="crypto" if payload.asset_type.strip().lower() == "crypto" else "stock",
        side=payload.side.strip().upper(),
        order_type=payload.order_type.strip().lower(),
        quantity=payload.quantity,
        time_in_force=payload.time_in_force.strip().lower(),
        limit_price=payload.limit_price,
        stop_price=payload.stop_price,
    )


async def _live_held_quantity(db: AsyncSession, ticker: str) -> float:
    positions = (
        await db.execute(
            select(Trade).where(Trade.ticker == ticker, Trade.status == TradeStatus.OPEN)
        )
    ).scalars().all()
    return round(
        sum(
            position.remaining_quantity
            for position in positions
            if position.direction == SignalDirection.BUY
        ),
        8,
    )


async def _live_data_state(ticker: str) -> tuple[Optional[bool], Optional[str], Optional[float]]:
    """Data-quality eligibility, reason and price age in seconds."""
    report = await _get_json(f"{settings.DATA_INGESTION_URL}/api/data-quality/{ticker}")
    if "error" in report:
        return None, f"Market-data quality is unavailable: {report['error']}", None
    eligible = bool(report.get("is_eligible"))
    issues = report.get("issues")
    reason = "; ".join(str(issue) for issue in issues) if isinstance(issues, list) and issues else None
    age_hours = report.get("age_hours")
    try:
        age_seconds = float(age_hours) * 3600.0 if age_hours is not None else None
    except (TypeError, ValueError):
        age_seconds = None
    if not eligible and not reason:
        reason = f"Market data status is {report.get('status', 'unknown')}"
    return eligible, reason, age_seconds


async def _live_gate_context(
    db: AsyncSession,
    request: live.OrderRequest,
    broker: Optional[BrokerAdapter],
) -> tuple[live.GateContext, dict[str, object]]:
    """Collect every gate input. Unavailable inputs stay ``None`` so gates fail closed."""
    control = await _live_control(db)
    eligible, data_reason, price_age = await _live_data_state(request.ticker)
    closes = await _latest_closes([request.ticker])
    reference_price = closes.get(request.ticker)

    buying_power: Optional[float] = None
    trading_blocked = False
    account_error: Optional[str] = None
    tradable: Optional[bool] = None
    shortable: Optional[bool] = None
    halted: Optional[bool] = None
    asset_error: Optional[str] = None
    credentials_present = bool(broker and broker.configured())
    if broker is not None and credentials_present:
        try:
            account = await broker.get_account()
            buying_power = account.buying_power
            trading_blocked = account.trading_blocked
        except BrokerError as exc:
            account_error = str(exc)
        try:
            asset = await broker.get_asset(request.ticker)
            tradable = asset.tradable
            shortable = asset.shortable
            halted = asset.halted
        except BrokerError as exc:
            asset_error = str(exc)

    risk = await _portfolio_risk_status(db)
    breaker = risk.get("breaker") if isinstance(risk, dict) else None
    breaker_active: Optional[bool] = None
    breaker_reasons: list[str] = []
    if isinstance(breaker, dict):
        breaker_active = bool(breaker.get("active"))
        raw_reasons = breaker.get("reasons")
        if isinstance(raw_reasons, list):
            breaker_reasons = [str(reason) for reason in raw_reasons]
    disabled_reason = control.disabled_reason
    trading_disabled = bool(control.trading_disabled)
    if trading_blocked:
        trading_disabled = True
        disabled_reason = "The broker account itself has trading blocked"

    context = live.GateContext(
        now=datetime.datetime.now(datetime.timezone.utc),
        config_enabled=bool(settings.LIVE_TRADING_ENABLED),
        acknowledged=bool(control.acknowledged),
        trading_disabled=trading_disabled,
        disabled_reason=disabled_reason,
        broker=broker.name if broker else None,
        credentials_present=credentials_present,
        sandbox=bool(broker.sandbox) if broker else True,
        reference_price=reference_price,
        price_age_seconds=price_age,
        max_price_age_seconds=settings.LIVE_MAX_PRICE_AGE_SECONDS,
        data_eligible=eligible,
        data_reason=data_reason,
        halted=halted,
        tradable=tradable,
        shortable=shortable,
        held_quantity=await _live_held_quantity(db, request.ticker),
        buying_power=buying_power,
        breaker_active=breaker_active,
        breaker_reasons=breaker_reasons,
        max_order_notional=settings.LIVE_MAX_ORDER_NOTIONAL_USD,
    )
    diagnostics: dict[str, object] = {
        "account_error": account_error,
        "asset_error": asset_error,
        "risk_breaker": breaker,
    }
    return context, diagnostics


def _live_order_dict(
    order: LiveOrder,
    fills: Optional[list[LiveOrderFill]] = None,
    events: Optional[list[LiveExecutionAudit]] = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": order.id,
        "mode": live.LIVE,
        "idempotency_key": order.idempotency_key,
        "client_order_id": order.client_order_id,
        "broker": order.broker,
        "broker_order_id": order.broker_order_id,
        "broker_endpoint": order.broker_endpoint,
        "sandbox": order.sandbox,
        "ticker": order.ticker,
        "asset_type": order.asset_type,
        "side": order.side,
        "order_type": order.order_type,
        "quantity": order.quantity,
        "filled_quantity": order.filled_quantity,
        "limit_price": order.limit_price,
        "stop_price": order.stop_price,
        "time_in_force": order.time_in_force,
        "reference_price": order.reference_price,
        "estimated_notional": order.estimated_notional,
        "average_fill_price": order.average_fill_price,
        "status": order.status,
        "preflight": _json_field(order.preflight_json) or [],
        "request_fingerprint": order.request_fingerprint,
        "reject_reason": order.reject_reason,
        "cancel_reason": order.cancel_reason,
        "broker_status_raw": order.broker_status_raw,
        "submitted_at": order.submitted_at,
        "reconciled_at": order.reconciled_at,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
    }
    if fills is not None:
        payload["fills"] = [
            {
                "id": fill.id,
                "broker_fill_id": fill.broker_fill_id,
                "quantity": fill.quantity,
                "price": fill.price,
                "filled_at": fill.filled_at,
                "created_at": fill.created_at,
            }
            for fill in fills
        ]
    if events is not None:
        payload["audit"] = [_audit_dict(event) for event in events]
    return payload


async def _live_order_detail(db: AsyncSession, order: LiveOrder) -> dict[str, object]:
    fills = (
        await db.execute(
            select(LiveOrderFill).where(LiveOrderFill.order_id == order.id).order_by(LiveOrderFill.id)
        )
    ).scalars().all()
    events = (
        await db.execute(
            select(LiveExecutionAudit)
            .where(LiveExecutionAudit.order_id == order.id)
            .order_by(LiveExecutionAudit.id)
        )
    ).scalars().all()
    return _live_order_dict(order, fills=list(fills), events=list(events))


def _live_mode_payload(broker: Optional[BrokerAdapter], control: LiveTradingControl) -> dict[str, object]:
    armed = (
        bool(settings.LIVE_TRADING_ENABLED)
        and bool(control.acknowledged)
        and not control.trading_disabled
        and broker is not None
        and broker.configured()
    )
    return {
        "mode": live.LIVE,
        "armed": armed,
        "config_enabled": bool(settings.LIVE_TRADING_ENABLED),
        "acknowledged": bool(control.acknowledged),
        "acknowledged_by": control.acknowledged_by,
        "acknowledged_at": control.acknowledged_at,
        "trading_disabled": bool(control.trading_disabled),
        "disabled_reason": control.disabled_reason,
        "disabled_by": control.disabled_by,
        "disabled_at": control.disabled_at,
        "broker": broker.name if broker else None,
        "broker_configured": bool(broker and broker.configured()),
        "broker_endpoint": settings.LIVE_BROKER_BASE_URL,
        "sandbox": bool(broker.sandbox) if broker else True,
        "acknowledgement_phrase": settings.LIVE_ACK_PHRASE,
        "max_order_notional": settings.LIVE_MAX_ORDER_NOTIONAL_USD,
        "max_price_age_seconds": settings.LIVE_MAX_PRICE_AGE_SECONDS,
        "notice": (
            "Live orders reach a real broker account. Paper orders remain fully separate."
            if armed
            else "Live execution is not armed; no live order can be submitted."
        ),
    }


@app.get("/api/live-trading/status")
async def live_trading_status(
    db: AsyncSession = Depends(get_db),
    broker: Optional[BrokerAdapter] = Depends(get_broker),
):
    """Arming state of live execution. Safe to call with no broker configured."""
    control = await _live_control(db)
    await db.commit()
    return _live_mode_payload(broker, control)


@app.post("/api/live-trading/acknowledge")
async def acknowledge_live_trading(
    payload: LiveAcknowledgeBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    broker: Optional[BrokerAdapter] = Depends(get_broker),
):
    """Record the explicit operator acknowledgement required before live orders."""
    if not settings.LIVE_TRADING_ENABLED:
        raise HTTPException(
            403,
            "Live trading is disabled by configuration; set LIVE_TRADING_ENABLED=true first",
        )
    if payload.phrase.strip() != settings.LIVE_ACK_PHRASE:
        raise HTTPException(400, f'The acknowledgement phrase must be exactly "{settings.LIVE_ACK_PHRASE}"')
    actor = _live_user_key(request)
    control = await _live_control(db)
    control.acknowledged = True
    control.acknowledged_by = actor
    control.acknowledged_at = _utc_now()
    control.acknowledgement_note = payload.note
    await _live_audit(
        db,
        event_type="acknowledged",
        message="Operator acknowledged live trading",
        actor=actor,
        record={"note": payload.note},
    )
    await db.commit()
    return _live_mode_payload(broker, control)


@app.post("/api/live-trading/revoke")
async def revoke_live_trading(
    request: Request,
    db: AsyncSession = Depends(get_db),
    broker: Optional[BrokerAdapter] = Depends(get_broker),
):
    """Withdraw the acknowledgement so live submission is blocked again."""
    actor = _live_user_key(request)
    control = await _live_control(db)
    control.acknowledged = False
    control.acknowledged_by = None
    control.acknowledged_at = None
    await _live_audit(
        db,
        event_type="acknowledgement_revoked",
        message="Live-trading acknowledgement revoked",
        actor=actor,
        record={},
    )
    await db.commit()
    return _live_mode_payload(broker, control)


@app.post("/api/live-trading/disable")
async def disable_live_trading(
    payload: LiveDisableBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    broker: Optional[BrokerAdapter] = Depends(get_broker),
):
    """Engage the kill switch. Local-only, so it works while the broker is down."""
    actor = _live_user_key(request)
    control = await _live_control(db)
    control.trading_disabled = True
    control.disabled_reason = (payload.reason or "Disabled by operator").strip()
    control.disabled_by = actor
    control.disabled_at = _utc_now()
    await _live_audit(
        db,
        event_type="trading_disabled",
        message=control.disabled_reason,
        actor=actor,
        record={"reason": control.disabled_reason},
    )
    await db.commit()
    return _live_mode_payload(broker, control)


@app.post("/api/live-trading/enable")
async def enable_live_trading(
    request: Request,
    db: AsyncSession = Depends(get_db),
    broker: Optional[BrokerAdapter] = Depends(get_broker),
):
    """Clear the kill switch. Requires configuration and an acknowledgement."""
    if not settings.LIVE_TRADING_ENABLED:
        raise HTTPException(403, "Live trading is disabled by configuration")
    control = await _live_control(db)
    if not control.acknowledged:
        raise HTTPException(403, "Acknowledge live trading before clearing the kill switch")
    actor = _live_user_key(request)
    control.trading_disabled = False
    control.disabled_reason = None
    control.disabled_by = None
    control.disabled_at = None
    await _live_audit(
        db,
        event_type="trading_enabled",
        message="Kill switch cleared",
        actor=actor,
        record={},
    )
    await db.commit()
    return _live_mode_payload(broker, control)


@app.post("/api/live-orders/preview")
async def preview_live_order(
    payload: LiveOrderRequestBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    broker: Optional[BrokerAdapter] = Depends(get_broker),
):
    """Evaluate every gate without contacting the broker's order endpoint."""
    order_request = _live_order_request(payload)
    context, diagnostics = await _live_gate_context(db, order_request, broker)
    checks = live.preflight(order_request, context)
    fingerprint = live.fingerprint(order_request)
    control = await _live_control(db)
    await _live_audit(
        db,
        event_type="preview",
        message=(
            f"Previewed live {order_request.order_type} {order_request.side} "
            f"{order_request.quantity:g} {order_request.ticker}"
        ),
        actor=_live_user_key(request),
        record={
            "request": order_request.as_dict(),
            "fingerprint": fingerprint,
            "blockers": [check.name for check in live.blockers(checks)],
        },
    )
    await db.commit()
    return {
        **_live_mode_payload(broker, control),
        "request": order_request.as_dict(),
        "approval_fingerprint": fingerprint,
        "estimated_notional": live.estimated_notional(order_request, context.reference_price),
        "reference_price": context.reference_price,
        "buying_power": context.buying_power,
        "checks": [check.as_dict() for check in checks],
        "blockers": [check.as_dict() for check in live.blockers(checks)],
        "submittable": live.submittable(checks),
        "diagnostics": diagnostics,
    }


@app.post("/api/live-orders", status_code=201)
async def submit_live_order(
    payload: LiveOrderSubmitBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    broker: Optional[BrokerAdapter] = Depends(get_broker),
):
    """Submit a previewed, approved live order.

    Retrying the same ``idempotency_key`` returns the stored order without
    contacting the broker again, and the broker call itself carries the key as
    the client order id so a duplicate can never be created upstream.
    """
    key = payload.idempotency_key.strip()
    if not key:
        raise HTTPException(400, "An idempotency key is required so retries cannot duplicate orders")
    existing = (
        await db.execute(select(LiveOrder).where(LiveOrder.idempotency_key == key))
    ).scalar_one_or_none()
    if existing is not None:
        return await _live_order_detail(db, existing)

    actor = _live_user_key(request)
    order_request = _live_order_request(payload)
    fingerprint = live.fingerprint(order_request)
    if payload.approval_fingerprint.strip() != fingerprint:
        raise HTTPException(
            400,
            "The approval fingerprint does not match this order; preview and approve it again",
        )

    context, diagnostics = await _live_gate_context(db, order_request, broker)
    checks = live.preflight(order_request, context)
    notional = live.estimated_notional(order_request, context.reference_price)
    order = LiveOrder(
        idempotency_key=key,
        client_order_id=key,
        broker=(broker.name if broker else settings.LIVE_BROKER),
        broker_endpoint=settings.LIVE_BROKER_BASE_URL,
        sandbox=bool(broker.sandbox) if broker else True,
        user_key=actor,
        ticker=order_request.ticker,
        asset_type=order_request.asset_type,
        side=order_request.side,
        order_type=order_request.order_type,
        quantity=order_request.quantity,
        limit_price=order_request.limit_price,
        stop_price=order_request.stop_price,
        time_in_force=order_request.time_in_force,
        reference_price=context.reference_price,
        estimated_notional=notional,
        status=live.NEW,
        preflight_json=json.dumps([check.as_dict() for check in checks], default=str),
        request_fingerprint=fingerprint,
    )
    db.add(order)
    await db.flush()
    await _live_audit(
        db,
        event_type="approved",
        message=f"{actor} approved live {order_request.side} {order_request.quantity:g} {order_request.ticker}",
        actor=payload.approved_by or actor,
        record={"request": order_request.as_dict(), "fingerprint": fingerprint},
        order=order,
    )

    blocking = live.blockers(checks)
    if blocking:
        order.status = live.REJECTED
        order.reject_reason = "; ".join(check.detail for check in blocking)
        await _live_audit(
            db,
            event_type="blocked",
            message=order.reject_reason,
            actor=actor,
            record={"blockers": [check.as_dict() for check in blocking], "diagnostics": diagnostics},
            order=order,
        )
        await db.commit()
        return await _live_order_detail(db, order)

    await _live_audit(
        db,
        event_type="order_request",
        message=f"Submitting to {order.broker} at {order.broker_endpoint}",
        actor=actor,
        record={"request": order_request.as_dict(), "client_order_id": order.client_order_id},
        order=order,
    )
    assert broker is not None  # a missing broker is a blocking preflight failure
    try:
        response = await broker.submit_order(order_request, order.client_order_id)
    except BrokerError as exc:
        order.status = live.REJECTED
        order.reject_reason = f"Broker rejected the order: {exc}"
        await _live_audit(
            db,
            event_type="broker_error",
            message=order.reject_reason,
            actor=actor,
            record={"status_code": exc.status_code, "body": exc.body},
            order=order,
        )
        await db.commit()
        return await _live_order_detail(db, order)

    order.broker_order_id = response.broker_order_id
    order.status = response.status
    order.broker_status_raw = str(response.raw.get("status", "")) or None
    order.filled_quantity = response.filled_quantity
    order.average_fill_price = response.average_fill_price
    order.submitted_at = _utc_now()
    await _live_audit(
        db,
        event_type="broker_response",
        message=f"Broker accepted order {response.broker_order_id} as {response.status}",
        actor=actor,
        record={"broker_order": response.as_dict()},
        order=order,
    )
    await _record_live_fill(db, order, response, actor)
    await db.commit()
    return await _live_order_detail(db, order)


async def _record_live_fill(
    db: AsyncSession,
    order: LiveOrder,
    response,
    actor: str,
) -> Optional[LiveOrderFill]:
    """Store a fill row for a cumulative broker fill quantity, once."""
    if response.filled_quantity <= live.QUANTITY_EPSILON or response.average_fill_price is None:
        return None
    fill_id = f"{response.broker_order_id}:{response.filled_quantity:.8f}"
    existing = (
        await db.execute(
            select(LiveOrderFill).where(
                LiveOrderFill.order_id == order.id,
                LiveOrderFill.broker_fill_id == fill_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    fill = LiveOrderFill(
        order_id=order.id,
        broker_fill_id=fill_id,
        quantity=response.filled_quantity,
        price=response.average_fill_price,
        filled_at=_utc_now(),
    )
    db.add(fill)
    await db.flush()
    await _live_audit(
        db,
        event_type="fill",
        message=(
            f"Broker reported {response.filled_quantity:g} filled at "
            f"${response.average_fill_price:,.4f}"
        ),
        actor=actor,
        record={"broker_fill_id": fill_id, "broker_order": response.as_dict()},
        order=order,
    )
    return fill


@app.get("/api/live-orders")
async def list_live_orders(
    status: Optional[str] = None,
    ticker: Optional[str] = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    broker: Optional[BrokerAdapter] = Depends(get_broker),
):
    """Live orders only; paper orders are stored and served separately."""
    query = select(LiveOrder).order_by(desc(LiveOrder.id)).limit(max(1, min(limit, 500)))
    if status:
        query = query.where(LiveOrder.status == status.strip().lower())
    if ticker:
        query = query.where(LiveOrder.ticker == ticker.strip().upper())
    orders = (await db.execute(query)).scalars().all()
    control = await _live_control(db)
    await db.commit()
    return {
        **_live_mode_payload(broker, control),
        "orders": [_live_order_dict(order) for order in orders],
    }


@app.post("/api/live-orders/cancel-all")
async def cancel_all_live_orders(
    payload: LiveCancelBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    broker: Optional[BrokerAdapter] = Depends(get_broker),
):
    """Best-effort cancel of every open live order.

    Local state is always marked ``canceled`` for orders the broker confirms, and
    broker failures are returned per order instead of aborting the sweep, so a
    provider outage cannot prevent the operator from stopping trading.
    """
    actor = _live_user_key(request)
    reason = (payload.reason or "Cancel-all requested by operator").strip()
    orders = (
        await db.execute(
            select(LiveOrder).where(LiveOrder.status.in_(live.OPEN_STATUSES)).order_by(LiveOrder.id)
        )
    ).scalars().all()
    results: list[dict[str, object]] = []
    for order in orders:
        if broker is None or not order.broker_order_id:
            results.append({"order_id": order.id, "canceled": False, "error": "No broker order id"})
            continue
        try:
            await broker.cancel_order(order.broker_order_id)
        except BrokerError as exc:
            results.append({"order_id": order.id, "canceled": False, "error": str(exc)})
            await _live_audit(
                db,
                event_type="cancel_failed",
                message=f"Broker cancel failed: {exc}",
                actor=actor,
                record={"reason": reason, "error": str(exc)},
                order=order,
            )
            continue
        order.status = live.CANCELED
        order.cancel_reason = reason
        results.append({"order_id": order.id, "canceled": True, "error": None})
        await _live_audit(
            db,
            event_type="canceled",
            message=reason,
            actor=actor,
            record={"reason": reason},
            order=order,
        )
    control = await _live_control(db)
    await db.commit()
    failures = [row for row in results if not row["canceled"]]
    return {
        **_live_mode_payload(broker, control),
        "requested": len(orders),
        "canceled": len(results) - len(failures),
        "failed": len(failures),
        "results": results,
    }


@app.post("/api/live-orders/reconcile")
async def reconcile_live_orders(
    request: Request,
    db: AsyncSession = Depends(get_db),
    broker: Optional[BrokerAdapter] = Depends(get_broker),
):
    """Compare stored live orders with broker state and adopt the broker's truth."""
    actor = _live_user_key(request)
    orders = (
        await db.execute(
            select(LiveOrder).where(LiveOrder.status.notin_(live.TERMINAL_STATUSES)).order_by(LiveOrder.id)
        )
    ).scalars().all()
    rows: list[dict[str, object]] = []
    for order in orders:
        if broker is None:
            rows.append({"order_id": order.id, "error": "No broker adapter configured"})
            continue
        try:
            remote = (
                await broker.get_order(order.broker_order_id)
                if order.broker_order_id
                else await broker.get_order_by_client_id(order.client_order_id)
            )
        except BrokerError as exc:
            rows.append({"order_id": order.id, "error": str(exc)})
            continue
        if remote is None:
            rows.append({"order_id": order.id, "error": "The broker does not know this order"})
            continue
        diff = live.reconcile(
            {
                "id": order.id,
                "broker_order_id": order.broker_order_id,
                "status": order.status,
                "filled_quantity": order.filled_quantity,
            },
            {"status": remote.raw.get("status"), "filled_qty": remote.filled_quantity},
        )
        rows.append(diff)
        if not diff["in_sync"]:
            order.status = remote.status
            order.broker_status_raw = str(remote.raw.get("status", "")) or None
            order.filled_quantity = remote.filled_quantity
            order.average_fill_price = remote.average_fill_price
            order.broker_order_id = order.broker_order_id or remote.broker_order_id
            await _live_audit(
                db,
                event_type="reconciled",
                message=(
                    f"Adopted broker state {remote.status} with "
                    f"{remote.filled_quantity:g} filled"
                ),
                actor=actor,
                record=diff,
                order=order,
            )
            await _record_live_fill(db, order, remote, actor)
        order.reconciled_at = _utc_now()
    control = await _live_control(db)
    await db.commit()
    return {
        **_live_mode_payload(broker, control),
        "checked": len(orders),
        "out_of_sync": len([row for row in rows if row.get("in_sync") is False]),
        "errors": len([row for row in rows if row.get("error")]),
        "results": rows,
    }


@app.get("/api/live-orders/audit")
async def live_execution_audit(limit: int = 200, db: AsyncSession = Depends(get_db)):
    """The full append-only audit trail, newest last."""
    entries = (
        await db.execute(
            select(LiveExecutionAudit).order_by(LiveExecutionAudit.id).limit(max(1, min(limit, 1000)))
        )
    ).scalars().all()
    return {"entries": [_audit_dict(entry) for entry in entries]}


@app.get("/api/live-orders/audit/verify")
async def verify_live_audit_chain(db: AsyncSession = Depends(get_db)):
    """Recompute the audit hash chain to prove no record was edited or removed."""
    entries = (
        await db.execute(select(LiveExecutionAudit).order_by(LiveExecutionAudit.id))
    ).scalars().all()
    chain = [
        {
            "id": entry.id,
            "record": (_json_field(entry.record_json) or {}),
            "entry_hash": entry.entry_hash,
        }
        for entry in entries
    ]
    intact, broken_id = live.verify_chain(chain)
    return {"entries": len(chain), "intact": intact, "broken_entry_id": broken_id}


@app.get("/api/live-orders/{order_id}")
async def get_live_order(order_id: int, db: AsyncSession = Depends(get_db)):
    order = (
        await db.execute(select(LiveOrder).where(LiveOrder.id == order_id))
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(404, "Live order not found")
    return await _live_order_detail(db, order)


@app.get("/api/live-orders/{order_id}/audit")
async def live_order_audit(order_id: int, db: AsyncSession = Depends(get_db)):
    entries = (
        await db.execute(
            select(LiveExecutionAudit)
            .where(LiveExecutionAudit.order_id == order_id)
            .order_by(LiveExecutionAudit.id)
        )
    ).scalars().all()
    return {"order_id": order_id, "entries": [_audit_dict(entry) for entry in entries]}


@app.post("/api/live-orders/{order_id}/cancel")
async def cancel_live_order(
    order_id: int,
    payload: LiveCancelBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    broker: Optional[BrokerAdapter] = Depends(get_broker),
):
    actor = _live_user_key(request)
    order = (
        await db.execute(select(LiveOrder).where(LiveOrder.id == order_id))
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(404, "Live order not found")
    if order.status in live.TERMINAL_STATUSES:
        raise HTTPException(400, f"Order {order_id} is already {order.status}")
    if broker is None or not order.broker_order_id:
        raise HTTPException(409, "This order has no broker order id to cancel")
    reason = (payload.reason or "Canceled by operator").strip()
    try:
        await broker.cancel_order(order.broker_order_id)
    except BrokerError as exc:
        await _live_audit(
            db,
            event_type="cancel_failed",
            message=f"Broker cancel failed: {exc}",
            actor=actor,
            record={"reason": reason, "error": str(exc)},
            order=order,
        )
        await db.commit()
        raise HTTPException(502, f"The broker did not cancel order {order_id}: {exc}")
    order.status = live.CANCELED
    order.cancel_reason = reason
    await _live_audit(
        db,
        event_type="canceled",
        message=reason,
        actor=actor,
        record={"reason": reason},
        order=order,
    )
    await db.commit()
    return await _live_order_detail(db, order)
