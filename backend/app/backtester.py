"""Backtesting engine: simulate strategy on historical data and report metrics."""

import logging
from dataclasses import dataclass, field

import pandas as pd
import numpy as np

from app.quant_engine import add_indicators
from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    direction: str
    entry_price: float
    exit_price: float
    stop_loss: float
    target_price: float
    entry_idx: int
    exit_idx: int
    pnl: float = 0.0
    pnl_pct: float = 0.0


@dataclass
class BacktestResult:
    ticker: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    initial_balance: float = 100_000.0
    final_balance: float = 100_000.0
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)


def run_backtest(df: pd.DataFrame, ticker: str, initial_balance: float = 100_000.0) -> BacktestResult:
    """Run a full backtest of the strategy on historical data."""
    if len(df) < 201:
        return BacktestResult(ticker=ticker, initial_balance=initial_balance, final_balance=initial_balance)

    df = add_indicators(df)
    result = BacktestResult(ticker=ticker, initial_balance=initial_balance, final_balance=initial_balance)

    balance = initial_balance
    peak = initial_balance
    max_dd = 0.0
    position = None  # Current open position
    equity_curve = [initial_balance]

    for i in range(201, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        price = row["close"]

        # Check if we need to close position
        if position is not None:
            should_close = False
            exit_price = price

            if position["direction"] == "BUY":
                # Trailing stop update
                if price >= position["entry_price"] * (1 + settings.TRAILING_STOP_PCT):
                    new_ts = price * (1 - settings.TRAILING_STOP_PCT)
                    if position["trailing_stop"] is None or new_ts > position["trailing_stop"]:
                        position["trailing_stop"] = new_ts

                if price <= position["stop_loss"]:
                    should_close = True
                    exit_price = position["stop_loss"]
                elif position["trailing_stop"] and price <= position["trailing_stop"]:
                    should_close = True
                    exit_price = position["trailing_stop"]
                elif price >= position["target_price"]:
                    should_close = True
                    exit_price = position["target_price"]

            if should_close:
                pnl = (exit_price - position["entry_price"]) * position["quantity"]
                pnl_pct = (pnl / (position["entry_price"] * position["quantity"])) * 100
                balance += (position["entry_price"] * position["quantity"]) + pnl

                trade = BacktestTrade(
                    direction=position["direction"],
                    entry_price=position["entry_price"],
                    exit_price=exit_price,
                    stop_loss=position["stop_loss"],
                    target_price=position["target_price"],
                    entry_idx=position["entry_idx"],
                    exit_idx=i,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                )
                result.trades.append(trade)
                if pnl > 0:
                    result.wins += 1
                else:
                    result.losses += 1
                position = None

        # Check for new signals (only if no open position)
        if position is None:
            ema_20 = row["ema_20"]
            ema_50 = row["ema_50"]
            ema_200 = row["ema_200"]
            rsi = row["rsi"]
            atr = row["atr"]
            atr_avg_30 = row["atr_avg_30"]
            ema_20_prev = row["ema_20_prev"]
            ema_50_prev = row["ema_50_prev"]

            if pd.isna(atr) or pd.isna(rsi) or pd.isna(ema_200) or pd.isna(atr_avg_30):
                equity_curve.append(balance)
                continue

            # Volatility filter
            volatile = atr > settings.ATR_VOLATILITY_THRESHOLD * atr_avg_30

            # BUY signal
            golden_cross = ema_20_prev <= ema_50_prev and ema_20 > ema_50
            above_200 = price > ema_200
            rsi_buy_zone = 45 <= rsi <= 55

            if golden_cross and above_200 and rsi_buy_zone and not volatile:
                stop_loss = price - (settings.ATR_STOP_MULTIPLIER * atr)
                risk = price - stop_loss
                reward = risk * settings.RISK_REWARD_RATIO
                target_price = price + reward

                # Position sizing: 2% risk
                max_risk = balance * 0.02
                quantity = max_risk / risk if risk > 0 else 0
                cost = quantity * price

                if cost <= balance and quantity > 0:
                    balance -= cost
                    position = {
                        "direction": "BUY",
                        "entry_price": price,
                        "stop_loss": stop_loss,
                        "target_price": target_price,
                        "quantity": quantity,
                        "trailing_stop": None,
                        "entry_idx": i,
                    }

        # Track equity
        current_equity = balance
        if position:
            current_equity += position["quantity"] * price
        equity_curve.append(current_equity)

        if current_equity > peak:
            peak = current_equity
        dd = (peak - current_equity) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Close any remaining position at last price
    if position is not None:
        last_price = df.iloc[-1]["close"]
        pnl = (last_price - position["entry_price"]) * position["quantity"]
        balance += (position["entry_price"] * position["quantity"]) + pnl
        trade = BacktestTrade(
            direction="BUY",
            entry_price=position["entry_price"],
            exit_price=last_price,
            stop_loss=position["stop_loss"],
            target_price=position["target_price"],
            entry_idx=position["entry_idx"],
            exit_idx=len(df) - 1,
            pnl=pnl,
            pnl_pct=(pnl / (position["entry_price"] * position["quantity"])) * 100,
        )
        result.trades.append(trade)
        if pnl > 0:
            result.wins += 1
        else:
            result.losses += 1

    result.total_trades = result.wins + result.losses
    result.win_rate = (result.wins / result.total_trades * 100) if result.total_trades > 0 else 0
    result.total_pnl = sum(t.pnl for t in result.trades)
    result.max_drawdown = max_dd * 100
    result.final_balance = balance
    result.equity_curve = equity_curve

    # Profit factor
    gross_profit = sum(t.pnl for t in result.trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in result.trades if t.pnl < 0))
    result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    return result
