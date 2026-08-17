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
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, init_db, async_session
from app.models import (
    Trade, TradeStatus, SignalDirection, Portfolio, EquitySnapshot, AlertLog, CredentialSecret, User,
    TradeExecution, TradeJournal, ExecutionKind,
)
from app import attribution as attribution_math
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


@app.get("/api/dashboard-summary")
async def dashboard_summary(db: AsyncSession = Depends(get_db)):
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

    remaining = trade.quantity - (trade.realized_quantity or 0.0)
    if payload.quantity is not None:
        if payload.quantity <= 0:
            raise HTTPException(400, "Exit quantity must be positive")
        if payload.quantity > remaining + 1e-9:
            raise HTTPException(400, f"Exit quantity exceeds the remaining {remaining:g} units")
        close_quantity = min(payload.quantity, remaining)
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
        exit_fees=payload.fees,
        exit_slippage=payload.slippage,
    )
    gross_pnl, net_pnl = attribution_math.net_exit_pnl(
        direction,
        trade.entry_price,
        payload.exit_price,
        close_quantity,
        costs,
    )

    trade.exit_price = payload.exit_price
    trade.realized_quantity = (trade.realized_quantity or 0.0) + close_quantity
    trade.entry_costs_allocated = (trade.entry_costs_allocated or 0.0) + costs.entry_costs_allocated
    trade.exit_fees_total = (trade.exit_fees_total or 0.0) + payload.fees
    trade.exit_slippage_total = (trade.exit_slippage_total or 0.0) + payload.slippage
    trade.costs_total = (trade.costs_total or 0.0) + costs.total
    trade.gross_pnl = (trade.gross_pnl or 0.0) + gross_pnl
    trade.pnl = (trade.pnl or 0.0) + net_pnl
    trade.pnl_pct = round((trade.pnl / (trade.entry_price * trade.quantity)) * 100, 2)
    db.add(TradeExecution(
        trade_id=trade.id,
        kind=ExecutionKind.EXIT,
        price=payload.exit_price,
        quantity=close_quantity,
        fees=payload.fees,
        slippage=payload.slippage,
        entry_costs_allocated=costs.entry_costs_allocated,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        note=payload.note,
    ))
    if is_final_exit:
        trade.status = TradeStatus.CLOSED
        trade.closed_at = datetime.datetime.now(datetime.timezone.utc)

    portfolio = await get_or_create_portfolio(db)
    released_capital = trade.entry_price * close_quantity
    portfolio.balance += released_capital + net_pnl
    portfolio.total_pnl += net_pnl
    remaining_result = await db.execute(select(Trade).where(Trade.status == TradeStatus.OPEN))
    remaining_locked = sum(
        position.entry_price * (position.quantity - (position.realized_quantity or 0.0))
        for position in remaining_result.scalars().all()
        if position.id != trade.id
    )
    if not is_final_exit:
        remaining_locked += trade.entry_price * (trade.quantity - trade.realized_quantity)
    portfolio.equity = portfolio.balance + remaining_locked
    if is_final_exit:
        if trade.pnl >= 0:
            portfolio.win_count += 1
        else:
            portfolio.loss_count += 1
    if portfolio.equity > portfolio.peak_equity:
        portfolio.peak_equity = portfolio.equity
    drawdown = ((portfolio.peak_equity - portfolio.equity) / portfolio.peak_equity * 100) if portfolio.peak_equity > 0 else 0
    if drawdown > portfolio.max_drawdown:
        portfolio.max_drawdown = round(drawdown, 2)
    db.add(EquitySnapshot(equity=portfolio.equity, balance=portfolio.balance))

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
        .where(Trade.status == TradeStatus.CLOSED)
        .order_by(desc(Trade.closed_at))
    )
    closed = list(result.scalars().all())
    executions = await _trade_executions(db, [trade.id for trade in closed])
    all_trades = [
        _trade_attribution_dict(trade, executions.get(trade.id, []))
        for trade in closed
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
