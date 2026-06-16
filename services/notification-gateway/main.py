"""Notification Gateway — dual-broadcasts alerts to Discord + Telegram.

Listens for approved signals from Service C and formats/sends notifications.
Also runs a Telegram bot listener for trade replies (/bought, /sold commands).
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pydantic_settings import BaseSettings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    DISCORD_WEBHOOK_URL: Optional[str] = None
    NOTIFY_TELEGRAM_ENABLED: bool = True
    NOTIFY_DISCORD_ENABLED: bool = True

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

_bot_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot_task
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        from bot import start_telegram_bot
        _bot_task = asyncio.create_task(
            start_telegram_bot(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)
        )
        logger.info("Telegram bot listener scheduled")
    yield
    if _bot_task and not _bot_task.done():
        _bot_task.cancel()
        try:
            await _bot_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Notification Gateway", version="2.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


class NotificationPayload(BaseModel):
    ticker: str
    direction: str
    status: str
    trigger_price: float
    target_price: float
    stop_loss: float
    optimal_size_usd: float
    kelly_pct: float
    paper_trade_executed: bool = False


class TestNotificationPayload(BaseModel):
    message: str = "This is a test notification from Market Analysis platform."


def format_alert(p: NotificationPayload) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    paper_line = (
        "[Paper Wallet] Virtual position logged."
        if p.paper_trade_executed
        else "[Paper Wallet] No position taken."
    )
    return (
        f"\U0001f6a8 [MARKET ALERT] {p.ticker}\n"
        f"Generated: {now}\n"
        f"Action: {p.direction} | Status: {p.status}\n"
        f"Trigger Price: ${p.trigger_price:,.2f}\n"
        f"Target Profit: ${p.target_price:,.2f} | Stop-Loss: ${p.stop_loss:,.2f}\n"
        f"Position Size: ${p.optimal_size_usd:,.2f} ({p.kelly_pct}% of balance)\n"
        f"---\n"
        f"{paper_line}"
    )


async def send_telegram(message: str, force: bool = False) -> bool:
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.debug("Telegram not configured")
        return False
    if not force and not settings.NOTIFY_TELEGRAM_ENABLED:
        logger.info("Telegram disabled via NOTIFY_TELEGRAM_ENABLED")
        return False
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": settings.TELEGRAM_CHAT_ID, "text": message}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        logger.info("Telegram notification sent")
        return True
    except Exception as e:
        logger.error(f"Telegram failed: {e}")
        return False


async def send_discord(message: str, force: bool = False) -> bool:
    if not settings.DISCORD_WEBHOOK_URL:
        logger.debug("Discord not configured")
        return False
    if not force and not settings.NOTIFY_DISCORD_ENABLED:
        logger.info("Discord disabled via NOTIFY_DISCORD_ENABLED")
        return False
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                settings.DISCORD_WEBHOOK_URL, json={"content": message}, timeout=10,
            )
            resp.raise_for_status()
        logger.info("Discord notification sent")
        return True
    except Exception as e:
        logger.error(f"Discord failed: {e}")
        return False


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "notification-gateway"}


@app.get("/api/settings/credentials")
async def credential_status():
    """Return which credential groups are configured (without exposing values)."""
    return {
        "telegram": bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID),
        "discord": bool(settings.DISCORD_WEBHOOK_URL),
    }


@app.post("/api/notify")
async def notify(payload: NotificationPayload):
    """Dual-broadcast a formatted alert to Discord and Telegram simultaneously."""
    message = format_alert(payload)
    tg_ok = await send_telegram(message)
    dc_ok = await send_discord(message)
    return {
        "message_preview": message[:200],
        "telegram_sent": tg_ok,
        "discord_sent": dc_ok,
    }


@app.post("/api/notify/test")
async def test_notification(payload: TestNotificationPayload = TestNotificationPayload()):
    """Send a test notification to verify Discord and Telegram are configured correctly."""
    message = f"\U0001f527 [TEST] {payload.message}"
    tg_ok = await send_telegram(message, force=True)
    dc_ok = await send_discord(message, force=True)
    results = {
        "telegram": {
            "configured": bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID),
            "sent": tg_ok,
        },
        "discord": {
            "configured": bool(settings.DISCORD_WEBHOOK_URL),
            "sent": dc_ok,
        },
    }
    any_configured = results["telegram"]["configured"] or results["discord"]["configured"]
    all_ok = any_configured and \
             (results["telegram"]["sent"] or not results["telegram"]["configured"]) and \
             (results["discord"]["sent"] or not results["discord"]["configured"])
    return {
        "success": all_ok,
        "results": results,
        "message": "Test notifications sent successfully." if all_ok else "One or more notification channels failed.",
    }


class ChannelToggle(BaseModel):
    channel: str
    enabled: bool


@app.get("/api/notify/channels")
async def get_channel_status():
    """Return notification channel configuration and toggle status."""
    return {
        "telegram": {
            "configured": bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID),
            "enabled": settings.NOTIFY_TELEGRAM_ENABLED,
        },
        "discord": {
            "configured": bool(settings.DISCORD_WEBHOOK_URL),
            "enabled": settings.NOTIFY_DISCORD_ENABLED,
        },
    }


@app.post("/api/notify/channels/toggle")
async def toggle_channel(payload: ChannelToggle):
    """Enable or disable a notification channel at runtime."""
    channel = payload.channel.lower()
    if channel == "telegram":
        settings.NOTIFY_TELEGRAM_ENABLED = payload.enabled
    elif channel == "discord":
        settings.NOTIFY_DISCORD_ENABLED = payload.enabled
    else:
        from fastapi import HTTPException
        raise HTTPException(400, f"Unknown channel: {channel}")
    return {
        "channel": channel,
        "enabled": payload.enabled,
        "message": f"{channel.title()} notifications {'enabled' if payload.enabled else 'disabled'}.",
    }


@app.get("/api/notify/reply-trades")
async def get_reply_trades():
    """Return trades logged via Telegram bot replies."""
    from bot import get_reply_trade_log
    return {
        "trades": get_reply_trade_log(),
        "bot_active": _bot_task is not None and not _bot_task.done(),
    }
