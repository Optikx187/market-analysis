"""Deterministic Action Required aggregation.

Every candidate carries a stable ``source_key`` so a still-active source updates the
same durable row instead of creating a duplicate. The module is side-effect free: the
API layer fetches service payloads and persists the candidates returned here.
"""

import dataclasses
import datetime
import hashlib
import json
from typing import Optional

DEFAULT_USER_KEY = "default"

STATUS_OPEN = "open"
STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_SNOOZED = "snoozed"
STATUS_RESOLVED = "resolved"
STATUSES = (STATUS_OPEN, STATUS_ACKNOWLEDGED, STATUS_SNOOZED, STATUS_RESOLVED)

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"
SEVERITIES = (SEVERITY_CRITICAL, SEVERITY_WARNING, SEVERITY_INFO)
SEVERITY_ORDER = {SEVERITY_CRITICAL: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}

CATEGORY_OPPORTUNITY = "opportunity"
CATEGORY_RISK = "risk"
CATEGORY_DATA = "data"
CATEGORY_EVENT = "event"
CATEGORY_EXECUTION = "execution"
CATEGORY_OPERATIONS = "operations"
CATEGORIES = (
    CATEGORY_OPPORTUNITY,
    CATEGORY_RISK,
    CATEGORY_DATA,
    CATEGORY_EVENT,
    CATEGORY_EXECUTION,
    CATEGORY_OPERATIONS,
)

# Risk-breaker and blocked-data problems stay visible through ordinary filters.
MANDATORY_CATEGORIES = (CATEGORY_RISK, CATEGORY_DATA)

ORDER_REVIEW_STATUSES = ("rejected", "expired")

SOURCE_TYPES = (
    "opportunity",
    "stop_proximity",
    "data_quality",
    "earnings",
    "risk_breaker",
    "order_review",
    "operational",
)

WIDGET_IDS = (
    "pnl",
    "cash",
    "exposure",
    "heat",
    "drawdown",
    "regime",
    "provider_health",
    "top_opportunities",
)
MODES = ("compact", "detailed")


@dataclasses.dataclass(frozen=True)
class ActionCandidate:
    source_key: str
    source_type: str
    category: str
    severity: str
    title: str
    message: str
    deep_link_tab: str
    deep_link: dict[str, object] = dataclasses.field(default_factory=dict)
    ticker: Optional[str] = None
    trade_id: Optional[int] = None
    order_id: Optional[int] = None
    context_id: Optional[str] = None
    payload: dict[str, object] = dataclasses.field(default_factory=dict)

    @property
    def is_mandatory(self) -> bool:
        return self.severity == SEVERITY_CRITICAL and self.category in MANDATORY_CATEGORIES

    @property
    def payload_hash(self) -> str:
        canonical = json.dumps(
            {
                "severity": self.severity,
                "title": self.title,
                "message": self.message,
                "deep_link": self.deep_link,
                "payload": self.payload,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


def _number(value: object) -> Optional[float]:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def build_opportunity_candidates(scanner_status: dict[str, object]) -> list[ActionCandidate]:
    """Eligible ranked opportunities the user has not already decided on."""
    scan = scanner_status.get("last_scan_result")
    if not isinstance(scan, dict):
        return []
    signals = scan.get("signals")
    if not isinstance(signals, list):
        return []
    candidates: list[ActionCandidate] = []
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        opportunity = signal.get("opportunity")
        if not isinstance(opportunity, dict) or not bool(opportunity.get("eligible")):
            continue
        if str(opportunity.get("user_decision", "pending")) not in ("pending", "edited"):
            continue
        opportunity_id = str(opportunity.get("id") or "")
        ticker = str(opportunity.get("ticker") or signal.get("ticker") or "")
        if not opportunity_id or not ticker:
            continue
        score = _number(opportunity.get("score")) or 0.0
        direction = str(opportunity.get("direction") or "NONE")
        plan = opportunity.get("trade_plan") if isinstance(opportunity.get("trade_plan"), dict) else {}
        candidates.append(ActionCandidate(
            source_key=f"opportunity:{opportunity_id}",
            source_type="opportunity",
            category=CATEGORY_OPPORTUNITY,
            severity=SEVERITY_INFO,
            title=f"{ticker} {direction} opportunity scored {score:.1f}",
            message=str(signal.get("reason") or opportunity.get("signal_reason") or "Eligible ranked opportunity awaiting a decision"),
            deep_link_tab="scanner",
            deep_link={"tab": "scanner", "ticker": ticker, "opportunity_id": opportunity_id},
            ticker=ticker,
            context_id=opportunity_id,
            payload={
                "score": score,
                "direction": direction,
                "position_size_usd": _number(plan.get("position_size_usd")) or 0.0,
                "stop_loss": _number(plan.get("stop_loss")),
                "evaluated_at": opportunity.get("evaluated_at"),
            },
        ))
    return candidates


def build_stop_proximity_candidates(
    open_trades: list[dict[str, object]],
    latest_closes: dict[str, Optional[float]],
    threshold_pct: float,
) -> list[ActionCandidate]:
    """Flag open trades whose latest stored close consumed most of the planned stop distance.

    ``remaining_pct`` is the share of the original entry-to-stop distance still left.
    An item is raised when ``remaining_pct <= threshold_pct``; a stop already breached
    (``remaining_pct <= 0``) is critical, otherwise it is a warning.
    """
    candidates: list[ActionCandidate] = []
    for trade in open_trades:
        trade_id = trade.get("id")
        ticker = str(trade.get("ticker") or "")
        entry_price = _number(trade.get("entry_price"))
        stop_loss = _number(trade.get("stop_loss"))
        close = latest_closes.get(ticker)
        if trade_id is None or not ticker or not entry_price or not stop_loss or close is None:
            continue
        planned_distance = abs(entry_price - stop_loss)
        if planned_distance <= 0:
            continue
        direction = str(trade.get("direction") or "BUY").upper()
        remaining = (close - stop_loss) if direction == "BUY" else (stop_loss - close)
        remaining_pct = round(remaining / planned_distance, 6)
        if remaining_pct > threshold_pct:
            continue
        breached = remaining_pct <= 0
        candidates.append(ActionCandidate(
            source_key=f"stop_proximity:trade:{trade_id}",
            source_type="stop_proximity",
            category=CATEGORY_RISK,
            severity=SEVERITY_CRITICAL if breached else SEVERITY_WARNING,
            title=(
                f"{ticker} stop breached at {close:g}"
                if breached
                else f"{ticker} is within {round(remaining_pct * 100, 2)}% of its stop"
            ),
            message=(
                f"Latest stored close {close:g} is beyond the {stop_loss:g} stop for this open {direction} trade."
                if breached
                else f"Latest stored close {close:g} leaves {round(remaining_pct * 100, 2)}% of the "
                f"{entry_price:g}\u2192{stop_loss:g} stop distance."
            ),
            deep_link_tab="trades",
            deep_link={"tab": "trades", "ticker": ticker, "trade_id": int(trade_id)},
            ticker=ticker,
            trade_id=int(trade_id),
            payload={
                "latest_close": close,
                "stop_loss": stop_loss,
                "entry_price": entry_price,
                "remaining_pct": remaining_pct,
                "threshold_pct": threshold_pct,
                "breached": breached,
            },
        ))
    return candidates


def build_data_quality_candidates(reports: dict[str, object]) -> list[ActionCandidate]:
    """Blocked or stale watchlist data from the data-ingestion quality report."""
    candidates: list[ActionCandidate] = []
    for ticker, raw in sorted(reports.items()):
        if not isinstance(raw, dict):
            continue
        eligible = bool(raw.get("is_eligible", True))
        stale = bool(raw.get("stale", False))
        status = str(raw.get("status") or "unknown")
        if eligible and not stale and status != "warning":
            continue
        raw_issues = raw.get("issues")
        issues = [str(issue) for issue in raw_issues] if isinstance(raw_issues, list) else []
        candidates.append(ActionCandidate(
            source_key=f"data_quality:{ticker}",
            source_type="data_quality",
            category=CATEGORY_DATA,
            severity=SEVERITY_CRITICAL if not eligible else SEVERITY_WARNING,
            title=(
                f"{ticker} data is blocked for trading"
                if not eligible
                else f"{ticker} data is stale or degraded"
            ),
            message="; ".join(issues) or f"Data quality status is {status}.",
            deep_link_tab="settings",
            deep_link={"tab": "settings", "ticker": ticker, "section": "data-quality"},
            ticker=str(ticker),
            context_id=str(ticker),
            payload={
                "status": status,
                "is_eligible": eligible,
                "stale": stale,
                "age_hours": _number(raw.get("age_hours")),
                "candle_count": raw.get("candle_count"),
                "issues": issues,
            },
        ))
    return candidates


def build_earnings_candidates(
    payload: dict[str, object],
    window_days: int,
) -> list[ActionCandidate]:
    """Watchlist earnings inside the configured near-term window."""
    upcoming = payload.get("upcoming")
    if not isinstance(upcoming, list):
        return []
    candidates: list[ActionCandidate] = []
    for entry in upcoming:
        if not isinstance(entry, dict):
            continue
        ticker = str(entry.get("ticker") or "")
        days_until = _number(entry.get("days_until"))
        if not ticker or days_until is None or days_until > window_days:
            continue
        candidates.append(ActionCandidate(
            source_key=f"earnings:{ticker}",
            source_type="earnings",
            category=CATEGORY_EVENT,
            severity=SEVERITY_WARNING,
            title=f"{ticker} reports earnings in {int(days_until)} day(s)",
            message=(
                f"Earnings on {entry.get('earnings_date')} are inside the {window_days}-day "
                "window; review or reduce event risk before adding exposure."
            ),
            deep_link_tab="scanner",
            deep_link={"tab": "scanner", "ticker": ticker},
            ticker=ticker,
            context_id=ticker,
            payload={
                "earnings_date": entry.get("earnings_date"),
                "days_until": int(days_until),
                "window_days": window_days,
            },
        ))
    return candidates


def build_breaker_candidates(breaker: dict[str, object]) -> list[ActionCandidate]:
    """One mandatory item per active portfolio breaker flag."""
    if not bool(breaker.get("active")):
        return []
    reasons = [str(reason) for reason in breaker.get("reasons", [])] if isinstance(breaker.get("reasons"), list) else []
    flags = (
        ("daily_loss", "daily_loss_active", "Daily loss limit breached"),
        ("weekly_loss", "weekly_loss_active", "Weekly loss limit breached"),
        ("drawdown", "drawdown_active", "Maximum drawdown breached"),
    )
    candidates: list[ActionCandidate] = []
    for key, flag, title in flags:
        if not bool(breaker.get(flag)):
            continue
        candidates.append(ActionCandidate(
            source_key=f"risk_breaker:{key}",
            source_type="risk_breaker",
            category=CATEGORY_RISK,
            severity=SEVERITY_CRITICAL,
            title=title,
            message="; ".join(reasons) or "New risk is blocked until the breach clears.",
            deep_link_tab="settings",
            deep_link={"tab": "settings", "section": "risk-limits"},
            context_id=key,
            payload={
                "reasons": reasons,
                "daily_loss_pct": _number(breaker.get("daily_loss_pct")),
                "weekly_loss_pct": _number(breaker.get("weekly_loss_pct")),
                "current_drawdown_pct": _number(breaker.get("current_drawdown_pct")),
                "allows_position_reduction": bool(breaker.get("allows_position_reduction", True)),
            },
        ))
    return candidates


def build_order_review_candidates(orders: list[dict[str, object]]) -> list[ActionCandidate]:
    """Rejected, failed, or expired paper orders that never reached the market."""
    candidates: list[ActionCandidate] = []
    for order in orders:
        order_id = order.get("id")
        status = str(order.get("status") or "")
        if order_id is None or status not in ORDER_REVIEW_STATUSES:
            continue
        ticker = str(order.get("ticker") or "")
        reason = str(order.get("reject_reason") or order.get("cancel_reason") or "")
        candidates.append(ActionCandidate(
            source_key=f"order_review:{order_id}",
            source_type="order_review",
            category=CATEGORY_EXECUTION,
            severity=SEVERITY_WARNING,
            title=f"{ticker} paper order {order_id} {status}",
            message=reason or f"Simulated order ended {status} and needs review before resubmitting.",
            deep_link_tab="orders",
            deep_link={"tab": "orders", "ticker": ticker, "order_id": int(order_id)},
            ticker=ticker or None,
            order_id=int(order_id),
            payload={
                "status": status,
                "side": order.get("side"),
                "order_type": order.get("order_type"),
                "quantity": _number(order.get("quantity")),
                "reason": reason,
            },
        ))
    return candidates


def operational_candidate(source: str, message: str) -> ActionCandidate:
    """A provider/service failure surfaced as an item instead of a failed endpoint."""
    return ActionCandidate(
        source_key=f"operational:{source}",
        source_type="operational",
        category=CATEGORY_OPERATIONS,
        severity=SEVERITY_WARNING,
        title=f"{source} is unavailable",
        message=message,
        deep_link_tab="settings",
        deep_link={"tab": "settings", "section": "system-health"},
        context_id=source,
        payload={"source": source, "error": message},
    )


def sort_key(item: dict[str, object]) -> tuple[int, int, str]:
    """Mandatory first, then severity, then newest activity."""
    severity = str(item.get("severity", SEVERITY_INFO))
    return (
        0 if item.get("is_mandatory") else 1,
        SEVERITY_ORDER.get(severity, len(SEVERITY_ORDER)),
        str(item.get("last_seen_at") or ""),
    )


def default_widgets() -> list[dict[str, object]]:
    return [{"id": widget_id, "enabled": True} for widget_id in WIDGET_IDS]


def normalize_widgets(raw: object) -> list[dict[str, object]]:
    """Keep the stored order, drop unknown ids, and append missing widgets as enabled."""
    widgets: list[dict[str, object]] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                widget_id = str(entry.get("id") or "")
                enabled = bool(entry.get("enabled", True))
            else:
                widget_id = str(entry)
                enabled = True
            if widget_id not in WIDGET_IDS or widget_id in seen:
                continue
            seen.add(widget_id)
            widgets.append({"id": widget_id, "enabled": enabled})
    for widget_id in WIDGET_IDS:
        if widget_id not in seen:
            widgets.append({"id": widget_id, "enabled": True})
    return widgets


def normalize_mode(raw: object) -> str:
    mode = str(raw or "detailed")
    return mode if mode in MODES else "detailed"


def expired_snooze(status: str, snoozed_until: Optional[datetime.datetime], now: datetime.datetime) -> bool:
    return status == STATUS_SNOOZED and snoozed_until is not None and snoozed_until <= now
