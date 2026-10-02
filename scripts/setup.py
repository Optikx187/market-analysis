#!/usr/bin/env python3
"""Interactive setup script for Market Analysis platform.

Provider credentials are staged in .env for first-start migration into the
encrypted database store. The portfolio service removes those plaintext values
after migration; encryption and service-authentication keys remain external.
"""

import base64
import os
import secrets
import sys
from getpass import getpass
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"

CREDENTIAL_GROUPS = [
    {
        "name": "Binance (Crypto Market Data)",
        "description": "Used for real-time crypto price streaming (BTC, ETH, etc).",
        "keys": [
            ("BINANCE_API_KEY", "Binance API key", True),
            ("BINANCE_API_SECRET", "Binance API secret", True),
        ],
    },
    {
        "name": "Alpaca (Stock Market Data)",
        "description": "Used for real-time stock price data (SPY, AAPL, etc).",
        "keys": [
            ("ALPACA_API_KEY", "Alpaca API key", True),
            ("ALPACA_API_SECRET", "Alpaca API secret", True),
        ],
    },
    {
        "name": "Telegram (Notifications)",
        "description": "Send trading signal alerts to a Telegram chat.",
        "keys": [
            ("TELEGRAM_BOT_TOKEN", "Telegram bot token", True),
            ("TELEGRAM_CHAT_ID", "Telegram chat ID", False),
        ],
    },
    {
        "name": "Discord (Notifications)",
        "description": "Send trading signal alerts to a Discord channel.",
        "keys": [
            ("DISCORD_WEBHOOK_URL", "Discord webhook URL", True),
        ],
    },
    {
        "name": "Slack (Notifications)",
        "description": "Send trading signal alerts to a Slack channel.",
        "keys": [("SLACK_WEBHOOK_URL", "Slack webhook URL", True)],
    },
    {
        "name": "Email (Notifications)",
        "description": "Send alerts through an SMTP account.",
        "keys": [
            ("SMTP_HOST", "SMTP host", False),
            ("SMTP_USER", "SMTP username", False),
            ("SMTP_PASSWORD", "SMTP password", True),
            ("EMAIL_TO", "Recipient address", False),
            ("EMAIL_FROM", "Sender address", False),
        ],
    },
    {
        "name": "Twilio SMS (Notifications)",
        "description": "Send alerts through Twilio SMS.",
        "keys": [
            ("TWILIO_ACCOUNT_SID", "Twilio account SID", False),
            ("TWILIO_AUTH_TOKEN", "Twilio auth token", True),
            ("TWILIO_FROM_NUMBER", "Twilio sender number", False),
            ("SMS_TO_NUMBER", "Recipient phone number", False),
        ],
    },
]

GENERATED_SECRETS = {
    "CREDENTIAL_ENCRYPTION_KEYS": base64.urlsafe_b64encode(os.urandom(32)).decode(),
    "INTERNAL_SERVICE_TOKEN": secrets.token_urlsafe(32),
}

DEFAULTS = {
    "RISK_REWARD_RATIO": "3.0",
    "ATR_STOP_MULTIPLIER": "1.5",
    "ATR_VOLATILITY_THRESHOLD": "2.0",
    "TRAILING_STOP_PCT": "0.02",
    "INITIAL_BALANCE": "10000.0",
    "LOSS_TOLERANCE_PCT": "0.02",
}


def load_existing_env() -> dict[str, str]:
    """Load existing .env values if file exists."""
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip()
    return values


def mask_value(value: str) -> str:
    """Mask a credential value for display."""
    if not value:
        return "(not set)"
    if len(value) <= 6:
        return "***"
    return value[:3] + "***" + value[-3:]


def write_env(values: dict[str, str]) -> None:
    """Write values to .env file, preserving comments from .env.example."""
    lines = []
    written_keys: set[str] = set()
    if ENV_EXAMPLE.exists():
        for line in ENV_EXAMPLE.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                val = values.get(key, "")
                lines.append(f"{key}={val}")
                written_keys.add(key)
            else:
                lines.append(line)
    else:
        for key, val in values.items():
            lines.append(f"{key}={val}")
            written_keys.add(key)

    for key, val in values.items():
        if key not in written_keys:
            lines.append(f"{key}={val}")

    ENV_FILE.write_text("\n".join(lines) + "\n")
    os.chmod(ENV_FILE, 0o600)


def main() -> None:
    print("=" * 60)
    print("  Market Analysis — Credential Setup")
    print("=" * 60)
    print()
    print(f"This script stages credentials in: {ENV_FILE}")
    print("On first start they migrate to encrypted storage and are removed from this file.")
    print()

    existing = load_existing_env()
    values = {**GENERATED_SECRETS, **DEFAULTS, **existing}

    for group in CREDENTIAL_GROUPS:
        print(f"\n{'─' * 50}")
        print(f"  {group['name']}")
        print(f"  {group['description']}")
        print(f"{'─' * 50}")

        has_existing = any(existing.get(k) for k, _, _ in group["keys"])
        if has_existing:
            print("  Current values:")
            for key, label, _ in group["keys"]:
                print(f"    {key}: {mask_value(existing.get(key, ''))}")
            print()

        skip = input(f"  Configure {group['name']}? [Y/n/skip]: ").strip().lower()
        if skip in ("n", "no", "skip", "s"):
            print("  → Skipped")
            continue

        for key, label, is_secret in group["keys"]:
            current = existing.get(key, "")
            prompt = f"  {label}"
            if current:
                prompt += f" [{mask_value(current)}]"
            prompt += ": "

            if is_secret:
                val = getpass(prompt)
            else:
                val = input(prompt)

            if val.strip():
                values[key] = val.strip()
            elif current:
                values[key] = current

    print(f"\n{'─' * 50}")
    print("  Risk & Portfolio Parameters")
    print(f"{'─' * 50}")
    skip = input("  Adjust risk parameters? [y/N]: ").strip().lower()
    if skip in ("y", "yes"):
        for key, default in DEFAULTS.items():
            current = values.get(key, default)
            val = input(f"  {key} [{current}]: ").strip()
            if val:
                values[key] = val
    else:
        print("  → Using defaults")

    write_env(values)

    print()
    print("=" * 60)
    print(f"  Credentials staged in {ENV_FILE}")
    print("  File permissions set to 600; first start removes provider plaintext")
    print()
    print("  Next steps:")
    print("    docker-compose up --build")
    print("    Open http://localhost:3000")
    print("=" * 60)


if __name__ == "__main__":
    main()
