"""Triple-channel notification system: Discord Webhook + Telegram Bot + API Webhooks."""

import hashlib
import hmac
import json
import logging
import time
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


def build_signal_payload(
    ticker: str,
    direction: SignalDirection,
    trigger_price: float,
    reason: str,
    stop_loss: float,
    target_price: float,
    suppressed: bool = False,
) -> dict:
    """Build a structured JSON payload for API webhook delivery."""
    return {
        "event": "trading_signal",
        "timestamp": int(time.time()),
        "signal": {
            "ticker": ticker,
            "direction": direction.value,
            "trigger_price": trigger_price,
            "stop_loss": stop_loss,
            "target_price": target_price,
            "reason": reason,
            "suppressed": suppressed,
        },
    }


def compute_webhook_signature(payload: dict, secret: str) -> str:
    """Compute HMAC-SHA256 signature for webhook payload verification."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


async def send_api_webhook(
    url: str, payload: dict, secret: Optional[str] = None
) -> tuple[bool, Optional[int], Optional[str]]:
    """POST a JSON payload to an API webhook URL.

    Returns (success, status_code, error_message).
    """
    headers = {"Content-Type": "application/json"}
    if secret:
        sig = compute_webhook_signature(payload, secret)
        headers["X-Signature-SHA256"] = sig

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers, timeout=10)
            resp.raise_for_status()
        logger.info(f"API webhook delivered to {url}")
        return True, resp.status_code, None
    except httpx.HTTPStatusError as e:
        msg = f"HTTP {e.response.status_code}"
        logger.error(f"API webhook to {url} failed: {msg}")
        return False, e.response.status_code, msg
    except Exception as e:
        logger.error(f"API webhook to {url} failed: {e}")
        return False, None, str(e)


async def send_all_api_webhooks(
    payload: dict, webhooks: list[dict],
) -> list[dict]:
    """Send payload to all configured API webhook endpoints.

    webhooks: list of {"id": int, "name": str, "url": str, "secret": str|None}
    Returns list of result dicts per webhook.
    """
    results = []
    for wh in webhooks:
        ok, status, error = await send_api_webhook(
            wh["url"], payload, wh.get("secret")
        )
        results.append({
            "webhook_id": wh["id"],
            "name": wh["name"],
            "url": wh["url"],
            "success": ok,
            "status_code": status,
            "error": error,
        })
    return results


async def send_signal_notification(
    ticker: str,
    direction: SignalDirection,
    trigger_price: float,
    reason: str,
    stop_loss: float,
    target_price: float,
    suppressed: bool = False,
    webhooks: Optional[list[dict]] = None,
):
    """Send signal notification to Telegram, Discord, and API webhooks."""
    message = format_signal_message(
        ticker, direction, trigger_price, reason, stop_loss, target_price, suppressed
    )
    telegram_ok = await send_telegram(message)
    discord_ok = await send_discord(message)

    # Send to API webhooks
    webhook_results = []
    if webhooks:
        payload = build_signal_payload(
            ticker, direction, trigger_price, reason, stop_loss, target_price, suppressed
        )
        webhook_results = await send_all_api_webhooks(payload, webhooks)

    any_webhook_ok = any(r["success"] for r in webhook_results) if webhook_results else False
    if not telegram_ok and not discord_ok and not any_webhook_ok:
        logger.warning("No notification channels configured or all failed")

    return webhook_results
