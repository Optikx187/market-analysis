"""Telegram bot listener — parses /bought and /sold commands and forwards
them to the portfolio-engine as manual trades.

Usage from Telegram:
    /bought BTC 65000 0.1         → BUY 0.1 BTC @ $65,000
    /sold   ETH 3500  1           → SELL 1 ETH @ $3,500
    /sold   ETH 3500  1 3400 3600 → SELL with explicit stop/target
    /trades                        → list recent open trades
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

logger = logging.getLogger(__name__)

PORTFOLIO_ENGINE_URL = os.environ.get("PORTFOLIO_ENGINE_URL", "http://portfolio-engine:8002")

_authorized_chat_id: Optional[str] = None

# In-memory log of trades submitted via Telegram
_reply_trade_log: list[dict] = []


def _record_reply_trade(
    user: str,
    channel: str,
    ticker: str,
    direction: str,
    entry_price: float,
    quantity: float,
    result: dict,
) -> dict:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user": user,
        "channel": channel,
        "ticker": ticker,
        "direction": direction,
        "entry_price": entry_price,
        "quantity": quantity,
        "result": result,
    }
    _reply_trade_log.append(entry)
    if len(_reply_trade_log) > 200:
        _reply_trade_log.pop(0)
    return entry


async def _submit_trade(
    ticker: str,
    direction: str,
    entry_price: float,
    quantity: float,
    stop_loss: Optional[float] = None,
    target_price: Optional[float] = None,
) -> dict:
    payload: dict = {
        "ticker": ticker,
        "direction": direction,
        "entry_price": entry_price,
        "quantity": quantity,
    }
    if stop_loss is not None:
        payload["stop_loss"] = stop_loss
    if target_price is not None:
        payload["target_price"] = target_price

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{PORTFOLIO_ENGINE_URL}/api/trades/manual",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


def _parse_trade_args(args: list[str]) -> tuple[str, float, float, Optional[float], Optional[float]]:
    """Parse: TICKER PRICE QTY [STOP_LOSS] [TARGET_PRICE]"""
    if len(args) < 3:
        raise ValueError(
            "Usage: /bought TICKER PRICE QTY [STOP_LOSS TARGET_PRICE]\n"
            "Example: /bought BTC 65000 0.1"
        )
    ticker = args[0].upper()
    try:
        price = float(args[1])
        qty = float(args[2])
    except ValueError:
        raise ValueError("Price and quantity must be numbers.")
    stop = float(args[3]) if len(args) > 3 else None
    target = float(args[4]) if len(args) > 4 else None
    return ticker, price, qty, stop, target


def _check_authorized(update: Update) -> bool:
    if _authorized_chat_id is None:
        return True
    return str(update.effective_chat.id) == _authorized_chat_id


async def _handle_bought(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /bought TICKER PRICE QTY [STOP TARGET]"""
    if not _check_authorized(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: /bought TICKER PRICE QTY [STOP_LOSS TARGET_PRICE]\n"
            "Example: /bought BTC 65000 0.1"
        )
        return
    try:
        ticker, price, qty, stop, target = _parse_trade_args(context.args)
        result = await _submit_trade(ticker, "BUY", price, qty, stop, target)
        user = update.effective_user.username or str(update.effective_user.id)
        _record_reply_trade(user, "telegram", ticker, "BUY", price, qty, result)
        await update.message.reply_text(
            f"Trade logged: BUY {qty} {ticker} @ ${price:,.2f}\n"
            f"Stop: ${result.get('stop_loss', 0):,.2f} | "
            f"Target: ${result.get('target_price', 0):,.2f}"
        )
    except ValueError as e:
        await update.message.reply_text(str(e))
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e))
        await update.message.reply_text(f"Trade rejected: {detail}")
    except Exception as e:
        logger.error(f"Bot /bought error: {e}")
        await update.message.reply_text(f"Error: {e}")


async def _handle_sold(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sold TICKER PRICE QTY [STOP TARGET]"""
    if not _check_authorized(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: /sold TICKER PRICE QTY [STOP_LOSS TARGET_PRICE]\n"
            "Example: /sold ETH 3500 1"
        )
        return
    try:
        ticker, price, qty, stop, target = _parse_trade_args(context.args)
        result = await _submit_trade(ticker, "SELL", price, qty, stop, target)
        user = update.effective_user.username or str(update.effective_user.id)
        _record_reply_trade(user, "telegram", ticker, "SELL", price, qty, result)
        await update.message.reply_text(
            f"Trade logged: SELL {qty} {ticker} @ ${price:,.2f}\n"
            f"Stop: ${result.get('stop_loss', 0):,.2f} | "
            f"Target: ${result.get('target_price', 0):,.2f}"
        )
    except ValueError as e:
        await update.message.reply_text(str(e))
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e))
        await update.message.reply_text(f"Trade rejected: {detail}")
    except Exception as e:
        logger.error(f"Bot /sold error: {e}")
        await update.message.reply_text(f"Error: {e}")


async def _handle_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /trades — list recent open trades from portfolio-engine."""
    if not _check_authorized(update):
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{PORTFOLIO_ENGINE_URL}/api/trades")
            resp.raise_for_status()
            trades = resp.json()
        open_trades = [t for t in trades if t.get("status") == "OPEN"]
        if not open_trades:
            await update.message.reply_text("No open trades.")
            return
        lines = ["Open trades:"]
        for t in open_trades[-10:]:
            lines.append(
                f"  {t['direction']} {t['quantity']} {t['ticker']} "
                f"@ ${t['entry_price']:,.2f} | {t['status']}"
            )
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        logger.error(f"Bot /trades error: {e}")
        await update.message.reply_text(f"Error fetching trades: {e}")


def get_reply_trade_log() -> list[dict]:
    return list(_reply_trade_log)


async def start_telegram_bot(token: str, chat_id: str) -> None:
    """Start the Telegram bot polling loop (runs forever)."""
    global _authorized_chat_id
    _authorized_chat_id = chat_id
    logger.info("Starting Telegram bot listener for trade replies (chat_id=%s)...", chat_id)
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("bought", _handle_bought))
    app.add_handler(CommandHandler("sold", _handle_sold))
    app.add_handler(CommandHandler("trades", _handle_trades))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        allowed_updates=["message"],
        drop_pending_updates=True,
    )
    logger.info("Telegram bot listener started — listening for /bought, /sold, /trades")

    # Keep running until cancelled
    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        logger.info("Telegram bot shutting down...")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
