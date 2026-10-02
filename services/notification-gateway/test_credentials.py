from fastapi.testclient import TestClient

import main


PROVIDER_VALUES = {
    "telegram": {"TELEGRAM_BOT_TOKEN": "fake-token", "TELEGRAM_CHAT_ID": "fake-chat"},
    "discord": {"DISCORD_WEBHOOK_URL": "https://example.invalid/discord"},
    "slack": {"SLACK_WEBHOOK_URL": "https://example.invalid/slack"},
    "email": {
        "SMTP_HOST": "smtp.example.invalid",
        "SMTP_USER": "fake-user",
        "SMTP_PASSWORD": "fake-password",
        "EMAIL_TO": "recipient@example.invalid",
    },
    "sms": {
        "TWILIO_ACCOUNT_SID": "fake-account",
        "TWILIO_AUTH_TOKEN": "fake-auth-token",
        "SMS_TO_NUMBER": "+15555550100",
    },
}


def test_notification_credentials_come_from_portfolio_engine(
    monkeypatch,
) -> None:
    async def fake_credentials(provider: str) -> dict[str, str]:
        return PROVIDER_VALUES[provider]

    monkeypatch.setattr(main, "_credentials", fake_credentials)
    client = TestClient(main.app)

    credential_status = client.get("/api/settings/credentials")
    channel_status = client.get("/api/notify/channels")

    assert credential_status.status_code == 200
    assert credential_status.json() == {
        "telegram": True,
        "discord": True,
        "slack": True,
        "email": True,
        "sms": True,
    }
    assert channel_status.status_code == 200
    assert all(value["configured"] for value in channel_status.json().values())
    for key in (
        "SLACK_WEBHOOK_URL",
        "SMTP_HOST",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "EMAIL_TO",
        "EMAIL_FROM",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_FROM_NUMBER",
        "SMS_TO_NUMBER",
    ):
        assert not hasattr(main.settings, key)
