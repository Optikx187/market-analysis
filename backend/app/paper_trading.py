"""Paper trading simulator: virtual portfolio management and trade execution."""

import datetime
import logging
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Trade,
    TradeStatus,
    SignalDirection,
    Portfolio,
    EquitySnapshot,
    SystemLog,
)

logger = logging.getLogger(__name__)

POSITION_SIZE_PCT = 0.02  # Risk 2% of equity per trade


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
    db: AsyncSession,
    ticker: str,
    direction: SignalDirection,
    entry_price: float,
    stop_loss: float,
    target_price: float,
) -> Optional[Trade]:
    """Execute a simulated paper trade."""
    portfolio = await get_or_create_portfolio(db)

    # Position sizing: risk 2% of equity
    risk_per_share = abs(entry_price - stop_loss)
    if risk_per_share <= 0:
        return None

    max_risk = portfolio.equity * POSITION_SIZE_PCT
    quantity = max_risk / risk_per_share
    cost = quantity * entry_price

    if cost > portfolio.balance:
        quantity = portfolio.balance / entry_price
        cost = quantity * entry_price

    if quantity <= 0 or cost <= 0:
        logger.warning(f"Insufficient balance for {ticker} trade")
        return None

    trade = Trade(
        ticker=ticker,
        direction=direction,
        entry_price=entry_price,
        quantity=quantity,
        stop_loss=stop_loss,
        target_price=target_price,
        trailing_stop=None,
        status=TradeStatus.OPEN,
    )
    db.add(trade)

    portfolio.balance -= cost
    log = SystemLog(level="INFO", message=f"Paper {direction.value} {quantity:.4f} {ticker} @ {entry_price:.2f}")
    db.add(log)
    await db.commit()
    await db.refresh(trade)
    return trade


async def check_and_close_trades(db: AsyncSession, ticker: str, current_price: float):
    """Check open trades for stop-loss, target, or trailing stop hits."""
    result = await db.execute(
        select(Trade).where(
            Trade.ticker == ticker,
            Trade.status == TradeStatus.OPEN,
        )
    )
    trades = result.scalars().all()
    portfolio = await get_or_create_portfolio(db)

    for trade in trades:
        should_close = False
        exit_price = current_price

        if trade.direction == SignalDirection.BUY:
            # Update trailing stop
            if current_price >= trade.entry_price * (1 + settings.TRAILING_STOP_PCT):
                new_trailing = current_price * (1 - settings.TRAILING_STOP_PCT)
                if trade.trailing_stop is None or new_trailing > trade.trailing_stop:
                    trade.trailing_stop = new_trailing

            # Check stop-loss
            if current_price <= trade.stop_loss:
                should_close = True
                exit_price = trade.stop_loss
            # Check trailing stop
            elif trade.trailing_stop and current_price <= trade.trailing_stop:
                should_close = True
                exit_price = trade.trailing_stop
            # Check target
            elif current_price >= trade.target_price:
                should_close = True
                exit_price = trade.target_price

        elif trade.direction == SignalDirection.SELL:
            if current_price >= trade.stop_loss:
                should_close = True
                exit_price = trade.stop_loss
            elif trade.trailing_stop and current_price >= trade.trailing_stop:
                should_close = True
                exit_price = trade.trailing_stop
            elif current_price <= trade.target_price:
                should_close = True
                exit_price = trade.target_price

        if should_close:
            await close_trade(db, trade, exit_price, portfolio)

    await db.commit()


async def close_trade(db: AsyncSession, trade: Trade, exit_price: float, portfolio: Portfolio):
    """Close a trade and update portfolio metrics."""
    trade.exit_price = exit_price
    trade.status = TradeStatus.CLOSED
    trade.closed_at = datetime.datetime.utcnow()

    if trade.direction == SignalDirection.BUY:
        trade.pnl = (exit_price - trade.entry_price) * trade.quantity
    else:
        trade.pnl = (trade.entry_price - exit_price) * trade.quantity

    trade.pnl_pct = (trade.pnl / (trade.entry_price * trade.quantity)) * 100

    # Return capital + PnL to balance
    portfolio.balance += (trade.entry_price * trade.quantity) + trade.pnl
    portfolio.total_pnl += trade.pnl

    if trade.pnl > 0:
        portfolio.win_count += 1
    else:
        portfolio.loss_count += 1

    # Update equity and drawdown
    portfolio.equity = portfolio.balance + await get_floating_pnl(db, portfolio)
    if portfolio.equity > portfolio.peak_equity:
        portfolio.peak_equity = portfolio.equity

    drawdown = (portfolio.peak_equity - portfolio.equity) / portfolio.peak_equity
    if drawdown > portfolio.max_drawdown:
        portfolio.max_drawdown = drawdown

    log = SystemLog(
        level="INFO",
        message=f"Closed {trade.direction.value} {trade.ticker}: PnL ${trade.pnl:.2f} ({trade.pnl_pct:.1f}%)",
    )
    db.add(log)


async def get_floating_pnl(db: AsyncSession, portfolio: Portfolio) -> float:
    """Calculate unrealized PnL for open trades (simplified—uses entry as proxy)."""
    result = await db.execute(
        select(Trade).where(Trade.status == TradeStatus.OPEN)
    )
    trades = result.scalars().all()
    return sum(t.entry_price * t.quantity for t in trades)


async def snapshot_equity(db: AsyncSession):
    """Take a snapshot of current equity for the equity curve."""
    portfolio = await get_or_create_portfolio(db)
    snap = EquitySnapshot(equity=portfolio.equity, balance=portfolio.balance)
    db.add(snap)
    await db.commit()


async def get_portfolio_metrics(db: AsyncSession) -> dict:
    """Get portfolio performance metrics."""
    portfolio = await get_or_create_portfolio(db)
    total_trades = portfolio.win_count + portfolio.loss_count
    win_rate = (portfolio.win_count / total_trades * 100) if total_trades > 0 else 0

    # Profit factor
    result_wins = await db.execute(
        select(func.sum(Trade.pnl)).where(Trade.status == TradeStatus.CLOSED, Trade.pnl > 0)
    )
    result_losses = await db.execute(
        select(func.sum(func.abs(Trade.pnl))).where(Trade.status == TradeStatus.CLOSED, Trade.pnl < 0)
    )
    total_wins = result_wins.scalar() or 0
    total_losses = result_losses.scalar() or 1
    profit_factor = total_wins / total_losses if total_losses > 0 else float("inf")

    # Equity curve
    result = await db.execute(
        select(EquitySnapshot).order_by(EquitySnapshot.timestamp)
    )
    snapshots = result.scalars().all()
    equity_curve = [{"timestamp": s.timestamp.isoformat(), "equity": s.equity} for s in snapshots]

    return {
        "balance": round(portfolio.balance, 2),
        "equity": round(portfolio.equity, 2),
        "total_pnl": round(portfolio.total_pnl, 2),
        "win_count": portfolio.win_count,
        "loss_count": portfolio.loss_count,
        "win_rate": round(win_rate, 1),
        "max_drawdown": round(portfolio.max_drawdown * 100, 2),
        "profit_factor": round(profit_factor, 2),
        "peak_equity": round(portfolio.peak_equity, 2),
        "equity_curve": equity_curve,
    }
