"""Notification Gateway — dual-broadcasts alerts to Discord + Telegram.

Listens for approved signals from Service C and formats/sends notifications.
"""

import logging
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

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

app = FastAPI(title="Notification Gateway", version="2.0.0")
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
    robinhood_buying_power: Optional[float] = None
    paper_trade_executed: bool = False


def format_alert(p: NotificationPayload) -> str:
    cash_line = (
        f"[Robinhood Balance Check] Buying Power Available: ${p.robinhood_buying_power:,.2f}"
        if p.robinhood_buying_power is not None
        else "[Robinhood Balance Check] Not connected"
    )
    paper_line = (
        "[Paper Wallet] Virtual position logged."
        if p.paper_trade_executed
        else "[Paper Wallet] No position taken."
    )
    return (
        f"\U0001f6a8 [MICROSERVICE ALERT] {p.ticker}\n"
        f"Action: {p.direction} | Status: {p.status}\n"
        f"Trigger Price: ${p.trigger_price:,.2f} | "
        f"Target Profit: ${p.target_price:,.2f} | "
        f"Stop-Loss: ${p.stop_loss:,.2f}\n"
        f"Optimal Sizing: ${p.optimal_size_usd:,.2f} ({p.kelly_pct}% allocation)\n"
        f"---\n"
        f"{cash_line}\n"
        f"{paper_line}"
    )


async def send_telegram(message: str) -> bool:
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.debug("Telegram not configured")
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


async def send_discord(message: str) -> bool:
    if not settings.DISCORD_WEBHOOK_URL:
        logger.debug("Discord not configured")
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
