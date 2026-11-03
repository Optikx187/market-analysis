"""Asset-class scanner schedule definitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import exchange_calendars
import pandas as pd

EQUITY_SCANNER_KEY = "market-opportunity-scan:equity"
CRYPTO_SCANNER_KEY = "market-opportunity-scan:crypto"
UTC_ZONE = ZoneInfo("UTC")


@dataclass(frozen=True)
class ScheduleDecision:
    eligible: bool
    next_eligible_at: datetime | None
    calendar_name: str
    session_label: str


@dataclass(frozen=True)
class AssetSchedule:
    name: str
    scanner_key: str
    asset_types: frozenset[str]
    calendar_name: str | None


def schedule_definitions(
    equity_calendar_name: str,
) -> tuple[AssetSchedule, AssetSchedule]:
    return (
        AssetSchedule(
            name="equity",
            scanner_key=EQUITY_SCANNER_KEY,
            asset_types=frozenset({"stock"}),
            calendar_name=equity_calendar_name,
        ),
        AssetSchedule(
            name="crypto",
            scanner_key=CRYPTO_SCANNER_KEY,
            asset_types=frozenset({"crypto"}),
            calendar_name=None,
        ),
    )


def schedule_decision(
    now: datetime,
    calendar_name: str | None,
) -> ScheduleDecision:
    current = (
        now.replace(tzinfo=UTC_ZONE)
        if now.tzinfo is None
        else now.astimezone(UTC_ZONE)
    )
    if calendar_name is None:
        return ScheduleDecision(
            eligible=True,
            next_eligible_at=None,
            calendar_name="24/7",
            session_label="24/7 UTC",
        )
    calendar = exchange_calendars.get_calendar(calendar_name)
    minute = pd.Timestamp(current).floor("min")
    if calendar.is_open_on_minute(minute):
        return ScheduleDecision(
            eligible=True,
            next_eligible_at=None,
            calendar_name=calendar.name,
            session_label=f"{calendar.name} open session",
        )
    next_open = calendar.next_open(minute).to_pydatetime()
    return ScheduleDecision(
        eligible=False,
        next_eligible_at=next_open,
        calendar_name=calendar.name,
        session_label=f"{calendar.name} next open",
    )
