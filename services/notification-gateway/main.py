"""Notification Gateway — dual-broadcasts alerts to Discord + Telegram.

Listens for approved signals from Service C and formats/sends notifications.
Also runs a Telegram bot listener for trade replies (/bought, /sold commands).
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings

from credentials import get_provider_credentials

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    PORTFOLIO_ENGINE_URL: str = "http://portfolio-engine:8002"
    INTERNAL_SERVICE_TOKEN: str = ""
    SMTP_PORT: int = 587
    NOTIFY_TELEGRAM_ENABLED: bool = True
    NOTIFY_DISCORD_ENABLED: bool = True
    NOTIFY_SLACK_ENABLED: bool = True
    NOTIFY_EMAIL_ENABLED: bool = True
    NOTIFY_SMS_ENABLED: bool = True
    DEPLOYMENT_PROFILE: str = "single-node"
    PUBLIC_BASE_URL: str = "http://localhost:3000"
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"
    NOTIFICATION_MODE: str = "live"
    TELEGRAM_API_BASE_URL: str = "https://api.telegram.org"
    TWILIO_API_BASE_URL: str = "https://api.twilio.com"

    @property
    def allowed_origins(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.ALLOWED_ORIGINS.split(",")
            if origin.strip()
        ]

    @model_validator(mode="after")
    def validate_deployment_profile(self) -> "Settings":
        profile = self.DEPLOYMENT_PROFILE.strip().lower()
        mode = self.NOTIFICATION_MODE.strip().lower()
        if mode not in {"live", "fake"}:
            raise ValueError("NOTIFICATION_MODE must be 'live' or 'fake'")
        if profile == "sandbox":
            if mode != "fake":
                raise ValueError("sandbox requires NOTIFICATION_MODE=fake")
            endpoint_hosts = {
                urlparse(self.TELEGRAM_API_BASE_URL).hostname,
                urlparse(self.TWILIO_API_BASE_URL).hostname,
            }
            if any(not host or not host.endswith(".invalid") for host in endpoint_hosts):
                raise ValueError("sandbox notification endpoints must use reserved .invalid hosts")
        return self

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

_bot_task: Optional[asyncio.Task] = None
_fake_deliveries: list[dict[str, str]] = []

_FAKE_CREDENTIALS = {
    "telegram": {
        "TELEGRAM_BOT_TOKEN": "sandbox-token",
        "TELEGRAM_CHAT_ID": "sandbox-chat",
    },
    "discord": {"DISCORD_WEBHOOK_URL": "https://discord.invalid/webhook"},
    "slack": {"SLACK_WEBHOOK_URL": "https://slack.invalid/webhook"},
    "email": {
        "SMTP_HOST": "smtp.invalid",
        "EMAIL_TO": "sandbox@example.invalid",
    },
    "sms": {
        "TWILIO_ACCOUNT_SID": "sandbox-account",
        "TWILIO_AUTH_TOKEN": "sandbox-token",
        "SMS_TO_NUMBER": "+15555550100",
    },
}


async def _credentials(provider: str) -> dict[str, str]:
    if settings.NOTIFICATION_MODE.strip().lower() == "fake":
        return dict(_FAKE_CREDENTIALS[provider])
    return await get_provider_credentials(
        provider,
        portfolio_engine_url=settings.PORTFOLIO_ENGINE_URL,
        internal_service_token=settings.INTERNAL_SERVICE_TOKEN,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot_task
    if settings.NOTIFICATION_MODE.strip().lower() == "fake":
        yield
        return
    telegram = await _credentials("telegram")
    bot_token = telegram.get("TELEGRAM_BOT_TOKEN")
    chat_id = telegram.get("TELEGRAM_CHAT_ID")
    if bot_token and chat_id:
        from bot import start_telegram_bot
        _bot_task = asyncio.create_task(start_telegram_bot(bot_token, chat_id))
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
    allow_origins=settings.allowed_origins, allow_credentials=True,
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


def _record_fake_delivery(channel: str, message: str, enabled: bool) -> bool:
    if not enabled:
        return False
    _fake_deliveries.append(
        {
            "channel": channel,
            "message": message,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    if len(_fake_deliveries) > 500:
        _fake_deliveries.pop(0)
    return True


async def send_telegram(message: str, force: bool = False) -> bool:
    if settings.NOTIFICATION_MODE.strip().lower() == "fake":
        return _record_fake_delivery(
            "telegram", message, force or settings.NOTIFY_TELEGRAM_ENABLED
        )
    credentials = await _credentials("telegram")
    bot_token = credentials.get("TELEGRAM_BOT_TOKEN")
    chat_id = credentials.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        logger.debug("Telegram not configured")
        return False
    if not force and not settings.NOTIFY_TELEGRAM_ENABLED:
        logger.info("Telegram disabled via NOTIFY_TELEGRAM_ENABLED")
        return False
    url = f"{settings.TELEGRAM_API_BASE_URL.rstrip('/')}/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        logger.info("Telegram notification sent")
        return True
    except Exception as exc:
        logger.error("Telegram notification failed: %s", type(exc).__name__)
        return False


async def send_discord(message: str, force: bool = False) -> bool:
    if settings.NOTIFICATION_MODE.strip().lower() == "fake":
        return _record_fake_delivery(
            "discord", message, force or settings.NOTIFY_DISCORD_ENABLED
        )
    credentials = await _credentials("discord")
    webhook_url = credentials.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.debug("Discord not configured")
        return False
    if not force and not settings.NOTIFY_DISCORD_ENABLED:
        logger.info("Discord disabled via NOTIFY_DISCORD_ENABLED")
        return False
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(webhook_url, json={"content": message}, timeout=10)
            resp.raise_for_status()
        logger.info("Discord notification sent")
        return True
    except Exception as exc:
        logger.error("Discord notification failed: %s", type(exc).__name__)
        return False


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "notification-gateway",
        "deployment_profile": settings.DEPLOYMENT_PROFILE,
        "notification_mode": settings.NOTIFICATION_MODE,
    }


@app.get("/api/notify/sandbox-deliveries")
async def sandbox_deliveries():
    if settings.NOTIFICATION_MODE.strip().lower() != "fake":
        raise HTTPException(404, "Sandbox deliveries are unavailable")
    return {"deliveries": list(_fake_deliveries)}


@app.get("/api/settings/credentials")
async def credential_status():
    """Return which credential groups are configured (without exposing values)."""
    telegram, discord, slack, email, sms = await asyncio.gather(
        *(_credentials(provider) for provider in ("telegram", "discord", "slack", "email", "sms"))
    )
    return {
        "telegram": bool(telegram.get("TELEGRAM_BOT_TOKEN") and telegram.get("TELEGRAM_CHAT_ID")),
        "discord": bool(discord.get("DISCORD_WEBHOOK_URL")),
        "slack": bool(slack.get("SLACK_WEBHOOK_URL")),
        "email": bool(email.get("SMTP_HOST") and email.get("EMAIL_TO")),
        "sms": bool(sms.get("TWILIO_ACCOUNT_SID") and sms.get("TWILIO_AUTH_TOKEN") and sms.get("SMS_TO_NUMBER")),
    }


@app.post("/api/notify")
async def notify(payload: NotificationPayload):
    """Broadcast a formatted alert to all configured channels."""
    message = format_alert(payload)
    tg_ok = await send_telegram(message)
    dc_ok = await send_discord(message)
    sl_ok = await send_slack(message)
    em_ok = await send_email(f"Market Alert: {payload.ticker} {payload.direction}", message)
    sm_ok = await send_sms(message)
    return {
        "message_preview": message[:200],
        "telegram_sent": tg_ok,
        "discord_sent": dc_ok,
        "slack_sent": sl_ok,
        "email_sent": em_ok,
        "sms_sent": sm_ok,
    }


@app.post("/api/notify/test")
async def test_notification(payload: TestNotificationPayload = TestNotificationPayload()):
    """Send a test notification to verify all configured channels."""
    message = f"\U0001f527 [TEST] {payload.message}"
    tg_ok = await send_telegram(message, force=True)
    dc_ok = await send_discord(message, force=True)
    sl_ok = await send_slack(message, force=True)
    em_ok = await send_email("[TEST] Market Analysis", message, force=True)
    sm_ok = await send_sms(message, force=True)
    telegram, discord, slack, email, sms = await asyncio.gather(
        *(_credentials(provider) for provider in ("telegram", "discord", "slack", "email", "sms"))
    )
    results = {
        "telegram": {
            "configured": bool(telegram.get("TELEGRAM_BOT_TOKEN") and telegram.get("TELEGRAM_CHAT_ID")),
            "sent": tg_ok,
        },
        "discord": {
            "configured": bool(discord.get("DISCORD_WEBHOOK_URL")),
            "sent": dc_ok,
        },
        "slack": {
            "configured": bool(slack.get("SLACK_WEBHOOK_URL")),
            "sent": sl_ok,
        },
        "email": {
            "configured": bool(email.get("SMTP_HOST") and email.get("EMAIL_TO")),
            "sent": em_ok,
        },
        "sms": {
            "configured": bool(sms.get("TWILIO_ACCOUNT_SID") and sms.get("TWILIO_AUTH_TOKEN") and sms.get("SMS_TO_NUMBER")),
            "sent": sm_ok,
        },
    }
    configured_channels = [ch for ch, info in results.items() if info["configured"]]
    any_configured = len(configured_channels) > 0
    all_ok = any_configured and all(
        results[ch]["sent"] or not results[ch]["configured"]
        for ch in results
    )
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
    telegram, discord, slack, email, sms = await asyncio.gather(
        *(_credentials(provider) for provider in ("telegram", "discord", "slack", "email", "sms"))
    )
    return {
        "telegram": {
            "configured": bool(telegram.get("TELEGRAM_BOT_TOKEN") and telegram.get("TELEGRAM_CHAT_ID")),
            "enabled": settings.NOTIFY_TELEGRAM_ENABLED,
        },
        "discord": {
            "configured": bool(discord.get("DISCORD_WEBHOOK_URL")),
            "enabled": settings.NOTIFY_DISCORD_ENABLED,
        },
        "slack": {
            "configured": bool(slack.get("SLACK_WEBHOOK_URL")),
            "enabled": settings.NOTIFY_SLACK_ENABLED,
        },
        "email": {
            "configured": bool(email.get("SMTP_HOST") and email.get("EMAIL_TO")),
            "enabled": settings.NOTIFY_EMAIL_ENABLED,
        },
        "sms": {
            "configured": bool(sms.get("TWILIO_ACCOUNT_SID") and sms.get("TWILIO_AUTH_TOKEN") and sms.get("SMS_TO_NUMBER")),
            "enabled": settings.NOTIFY_SMS_ENABLED,
        },
    }


@app.post("/api/notify/channels/toggle")
async def toggle_channel(payload: ChannelToggle):
    """Enable or disable a notification channel at runtime."""
    channel = payload.channel.lower()
    channel_map = {
        "telegram": "NOTIFY_TELEGRAM_ENABLED",
        "discord": "NOTIFY_DISCORD_ENABLED",
        "slack": "NOTIFY_SLACK_ENABLED",
        "email": "NOTIFY_EMAIL_ENABLED",
        "sms": "NOTIFY_SMS_ENABLED",
    }
    attr = channel_map.get(channel)
    if not attr:
        from fastapi import HTTPException
        raise HTTPException(400, f"Unknown channel: {channel}")
    setattr(settings, attr, payload.enabled)
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


# --- Phase 9: Additional Notification Channels ---

async def send_slack(message: str, force: bool = False) -> bool:
    if settings.NOTIFICATION_MODE.strip().lower() == "fake":
        return _record_fake_delivery(
            "slack", message, force or settings.NOTIFY_SLACK_ENABLED
        )
    credentials = await _credentials("slack")
    webhook_url = credentials.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return False
    if not force and not settings.NOTIFY_SLACK_ENABLED:
        return False
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(webhook_url, json={"text": message}, timeout=10)
            resp.raise_for_status()
        logger.info("Slack notification sent")
        return True
    except Exception as exc:
        logger.error("Slack notification failed: %s", type(exc).__name__)
        return False


async def send_email(subject: str, body: str, force: bool = False) -> bool:
    if settings.NOTIFICATION_MODE.strip().lower() == "fake":
        return _record_fake_delivery(
            "email",
            f"{subject}\n{body}",
            force or settings.NOTIFY_EMAIL_ENABLED,
        )
    credentials = await _credentials("email")
    smtp_host = credentials.get("SMTP_HOST")
    email_to = credentials.get("EMAIL_TO")
    if not smtp_host or not email_to:
        return False
    if not force and not settings.NOTIFY_EMAIL_ENABLED:
        return False
    try:
        import smtplib
        from email.mime.text import MIMEText
        smtp_user = credentials.get("SMTP_USER")
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = credentials.get("EMAIL_FROM") or smtp_user or "market-analysis@localhost"
        msg["To"] = email_to
        with smtplib.SMTP(smtp_host, settings.SMTP_PORT) as server:
            server.starttls()
            if smtp_user and credentials.get("SMTP_PASSWORD"):
                server.login(smtp_user, credentials["SMTP_PASSWORD"])
            server.send_message(msg)
        logger.info("Email notification sent")
        return True
    except Exception as exc:
        logger.error("Email notification failed: %s", type(exc).__name__)
        return False


async def send_sms(message: str, force: bool = False) -> bool:
    if settings.NOTIFICATION_MODE.strip().lower() == "fake":
        return _record_fake_delivery(
            "sms", message, force or settings.NOTIFY_SMS_ENABLED
        )
    credentials = await _credentials("sms")
    account_sid = credentials.get("TWILIO_ACCOUNT_SID")
    auth_token = credentials.get("TWILIO_AUTH_TOKEN")
    to_number = credentials.get("SMS_TO_NUMBER")
    if not account_sid or not auth_token or not to_number:
        return False
    if not force and not settings.NOTIFY_SMS_ENABLED:
        return False
    try:
        url = (
            f"{settings.TWILIO_API_BASE_URL.rstrip('/')}/2010-04-01/"
            f"Accounts/{account_sid}/Messages.json"
        )
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                data={
                    "To": to_number,
                    "From": credentials.get("TWILIO_FROM_NUMBER", ""),
                    "Body": message[:1600],
                },
                auth=(account_sid, auth_token),
                timeout=15,
            )
            resp.raise_for_status()
        logger.info("SMS notification sent")
        return True
    except Exception as exc:
        logger.error("SMS notification failed: %s", type(exc).__name__)
        return False


@app.post("/api/notify/signal")
async def notify_signal(payload: NotificationPayload):
    """Broadcast a signal alert to ALL configured channels."""
    message = format_alert(payload)
    tg_ok = await send_telegram(message)
    dc_ok = await send_discord(message)
    sl_ok = await send_slack(message)
    em_ok = await send_email(f"Market Alert: {payload.ticker} {payload.direction}", message)
    sm_ok = await send_sms(message)
    return {
        "telegram_sent": tg_ok,
        "discord_sent": dc_ok,
        "slack_sent": sl_ok,
        "email_sent": em_ok,
        "sms_sent": sm_ok,
    }


class PriceAlertNotification(BaseModel):
    ticker: str
    condition: str
    threshold: float
    current_price: float


@app.post("/api/notify/price-alert")
async def notify_price_alert(payload: PriceAlertNotification):
    """Send a price alert notification to all channels."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    message = (
        f"\U0001f4b0 [PRICE ALERT] {payload.ticker}\n"
        f"{now}\n"
        f"Price crossed {payload.condition} ${payload.threshold:,.2f}\n"
        f"Current Price: ${payload.current_price:,.2f}"
    )
    tg_ok = await send_telegram(message)
    dc_ok = await send_discord(message)
    sl_ok = await send_slack(message)
    em_ok = await send_email(f"Price Alert: {payload.ticker} {payload.condition} ${payload.threshold}", message)
    sm_ok = await send_sms(message)
    return {
        "telegram_sent": tg_ok,
        "discord_sent": dc_ok,
        "slack_sent": sl_ok,
        "email_sent": em_ok,
        "sms_sent": sm_ok,
    }
