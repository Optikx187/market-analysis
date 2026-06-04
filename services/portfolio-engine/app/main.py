"""Service C — Portfolio & Integration Engine."""

import datetime
import logging
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, init_db, async_session
from app.models import (
    Trade, TradeStatus, SignalDirection, Portfolio, EquitySnapshot, AlertLog,
)
from app.robinhood import check_capital_overspend, get_buying_power

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
    robinhood_buying_power: Optional[float]
    capital_overspend: bool
    reason: str
    paper_trade_executed: bool


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
    robinhood_buying_power: Optional[float]
    capital_overspend: bool
    message: Optional[str]
    created_at: Optional[datetime.datetime]
    model_config = {"from_attributes": True}


POSITION_SIZE_PCT = 0.02


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


async def execute_paper_trade(
    db: AsyncSession, ticker: str, direction: SignalDirection,
    entry_price: float, stop_loss: float, target_price: float,
    quantity_override: Optional[float] = None,
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
    trade = Trade(
        ticker=ticker, direction=direction, entry_price=entry_price,
        quantity=quantity, stop_loss=stop_loss, target_price=target_price,
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
    """Receive a signal from quant-engine, validate against guardrails, execute paper trade."""
    rh_check = check_capital_overspend(signal.optimal_size_usd)
    approved = not signal.suppressed and not rh_check["overspend"]

    paper_trade_executed = False
    if approved and signal.direction in ("BUY", "SELL"):
        trade = await execute_paper_trade(
            db, signal.ticker,
            SignalDirection(signal.direction),
            signal.trigger_price, signal.stop_loss, signal.target_price,
        )
        paper_trade_executed = trade is not None

    reason_parts = [signal.reason]
    if signal.suppressed:
        reason_parts.append(f"Signal suppressed ({signal.status})")
    if rh_check["overspend"]:
        reason_parts.append(rh_check["reason"])

    alert = AlertLog(
        ticker=signal.ticker,
        direction=signal.direction or "NONE",
        status=signal.status,
        trigger_price=signal.trigger_price,
        stop_loss=signal.stop_loss,
        target_price=signal.target_price,
        optimal_size_usd=signal.optimal_size_usd,
        kelly_pct=signal.kelly_pct,
        robinhood_buying_power=rh_check.get("buying_power"),
        capital_overspend=rh_check["overspend"],
        message=" | ".join(reason_parts),
    )
    db.add(alert)
    await db.commit()

    return SignalDecision(
        ticker=signal.ticker,
        direction=signal.direction or "NONE",
        status=signal.status,
        approved=approved,
        trigger_price=signal.trigger_price,
        stop_loss=signal.stop_loss,
        target_price=signal.target_price,
        optimal_size_usd=signal.optimal_size_usd,
        kelly_pct=signal.kelly_pct,
        robinhood_buying_power=rh_check.get("buying_power"),
        capital_overspend=rh_check["overspend"],
        reason=" | ".join(reason_parts),
        paper_trade_executed=paper_trade_executed,
    )


@app.get("/api/portfolio", response_model=PortfolioResponse)
async def get_portfolio(db: AsyncSession = Depends(get_db)):
    portfolio = await get_or_create_portfolio(db)
    total_trades = portfolio.win_count + portfolio.loss_count
    win_rate = (portfolio.win_count / total_trades * 100) if total_trades > 0 else 0

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
        equity_curve=equity_curve,
    )


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


@app.get("/api/alerts", response_model=list[AlertLogResponse])
async def list_alerts(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlertLog).order_by(desc(AlertLog.created_at)).limit(limit),
    )
    return result.scalars().all()


@app.get("/api/robinhood/balance")
async def robinhood_balance():
    bp = get_buying_power()
    return {"buying_power": bp, "connected": bp is not None}
