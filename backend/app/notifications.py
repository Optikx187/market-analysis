"""Dual-channel notification system: Discord Webhook + Telegram Bot."""

import logging
from typing import Optional

import httpx

from app.config import settings
from app.models import SignalDirection

logger = logging.getLogger(__name__)


def format_signal_message(
    ticker: str,
    direction: SignalDirection,
    trigger_price: float,
    reason: str,
    stop_loss: float,
    target_price: float,
    suppressed: bool = False,
) -> str:
    emoji = "🟢" if direction == SignalDirection.BUY else "🔴"
    suppressed_note = "\n⚠️ SUPPRESSED by volatility filter — high ATR detected." if suppressed else ""
    return (
        f"🚨 [SIGNAL] {ticker}\n"
        f"Direction: {emoji} {direction.value}\n"
        f"Trigger Price: ${trigger_price:,.6f}\n"
        f"Reason: {reason}\n"
        f"Strict Stop-Loss: ${stop_loss:,.6f} (Exit immediately if hit)\n"
        f"Target Profit: ${target_price:,.6f}\n"
        f"[Paper Trading] Executed simulated position for virtual portfolio."
        f"{suppressed_note}"
    )


async def send_telegram(message: str) -> bool:
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.debug("Telegram not configured, skipping")
        return False

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        logger.info("Telegram notification sent")
        return True
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


async def send_discord(message: str) -> bool:
    if not settings.DISCORD_WEBHOOK_URL:
        logger.debug("Discord webhook not configured, skipping")
        return False

    payload = {"content": message}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(settings.DISCORD_WEBHOOK_URL, json=payload, timeout=10)
            resp.raise_for_status()
        logger.info("Discord notification sent")
        return True
    except Exception as e:
        logger.error(f"Discord send failed: {e}")
        return False


async def send_signal_notification(
    ticker: str,
    direction: SignalDirection,
    trigger_price: float,
    reason: str,
    stop_loss: float,
    target_price: float,
    suppressed: bool = False,
):
    """Send signal notification to both Telegram and Discord."""
    message = format_signal_message(
        ticker, direction, trigger_price, reason, stop_loss, target_price, suppressed
    )
    telegram_ok = await send_telegram(message)
    discord_ok = await send_discord(message)
    if not telegram_ok and not discord_ok:
        logger.warning("No notification channels configured or both failed")
