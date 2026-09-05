"""Pure guard logic for live-broker execution.

Live execution is the only path in this service that can reach real money, so
every gate is expressed here as a deterministic, side-effect-free function that
can be tested without a broker, a clock or a database. ``preflight`` returns the
full check list — passing and failing — so the UI can show the operator exactly
what was evaluated, and ``blockers`` decides submission. A request is only
submittable when *no* check blocks it: the checks fail closed, so a missing
input (no price, no account snapshot, unknown halt state) blocks rather than
being assumed safe.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import datetime, time, timezone
from typing import Optional, Sequence

PAPER = "paper"
LIVE = "live"

BUY = "BUY"
SELL = "SELL"
SIDES = (BUY, SELL)

MARKET = "market"
LIMIT = "limit"
STOP = "stop"
STOP_LIMIT = "stop_limit"
ORDER_TYPES = (MARKET, LIMIT, STOP, STOP_LIMIT)

DAY = "day"
GTC = "gtc"
TIME_IN_FORCE = (DAY, GTC)

NEW = "new"
SUBMITTED = "submitted"
ACCEPTED = "accepted"
PARTIALLY_FILLED = "partially_filled"
FILLED = "filled"
CANCELED = "canceled"
REJECTED = "rejected"
EXPIRED = "expired"
UNKNOWN = "unknown"

ORDER_STATUSES = (
    NEW,
    SUBMITTED,
    ACCEPTED,
    PARTIALLY_FILLED,
    FILLED,
    CANCELED,
    REJECTED,
    EXPIRED,
    UNKNOWN,
)

OPEN_STATUSES = (NEW, SUBMITTED, ACCEPTED, PARTIALLY_FILLED)
TERMINAL_STATUSES = (FILLED, CANCELED, REJECTED, EXPIRED)

# Alpaca (and most brokers) report a superset of states; anything unmapped stays
# ``unknown`` so reconciliation surfaces it instead of silently treating a live
# order as closed.
BROKER_STATUS_MAP = {
    "new": NEW,
    "accepted": ACCEPTED,
    "pending_new": SUBMITTED,
    "accepted_for_bidding": ACCEPTED,
    "partially_filled": PARTIALLY_FILLED,
    "filled": FILLED,
    "done_for_day": EXPIRED,
    "canceled": CANCELED,
    "pending_cancel": CANCELED,
    "expired": EXPIRED,
    "replaced": CANCELED,
    "rejected": REJECTED,
    "suspended": REJECTED,
    "stopped": CANCELED,
    "calculated": ACCEPTED,
}

US_EQUITY_OPEN_UTC = time(13, 30)
US_EQUITY_CLOSE_UTC = time(20, 0)

QUANTITY_EPSILON = 1e-9


def normalize_status(raw: object) -> str:
    """Map a broker status string onto this service's order lifecycle."""
    return BROKER_STATUS_MAP.get(str(raw or "").strip().lower(), UNKNOWN)


def market_open(now: datetime, asset_type: str) -> bool:
    """Whether ``asset_type`` trades at ``now`` (UTC).

    Crypto is continuous. US equities use the regular 09:30-16:00 ET session,
    expressed in UTC; extended hours are deliberately excluded because the
    liquidity and slippage assumptions elsewhere in the app assume RTH.
    """
    if asset_type == "crypto":
        return True
    moment = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    if moment.weekday() >= 5:
        return False
    return US_EQUITY_OPEN_UTC <= moment.timetz().replace(tzinfo=None) < US_EQUITY_CLOSE_UTC


@dataclasses.dataclass(frozen=True)
class Check:
    """One preflight gate. ``blocking`` is only meaningful when not ``passed``."""

    name: str
    passed: bool
    detail: str
    blocking: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "passed": self.passed,
            "blocking": self.blocking and not self.passed,
            "detail": self.detail,
        }


@dataclasses.dataclass(frozen=True)
class OrderRequest:
    """A normalized live-order intent, independent of broker payload shapes."""

    ticker: str
    asset_type: str
    side: str
    order_type: str
    quantity: float
    time_in_force: str = DAY
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class GateContext:
    """Everything the gates need, gathered by the caller before preflight."""

    now: datetime
    config_enabled: bool
    acknowledged: bool
    trading_disabled: bool
    disabled_reason: Optional[str]
    broker: Optional[str]
    credentials_present: bool
    sandbox: bool
    reference_price: Optional[float]
    price_age_seconds: Optional[float]
    max_price_age_seconds: float
    data_eligible: Optional[bool]
    data_reason: Optional[str]
    halted: Optional[bool]
    tradable: Optional[bool]
    shortable: Optional[bool]
    held_quantity: float
    buying_power: Optional[float]
    breaker_active: Optional[bool]
    breaker_reasons: Sequence[str]
    max_order_notional: float


def validate_request(request: OrderRequest) -> list[str]:
    """Structural problems that make an order meaningless regardless of state."""
    errors: list[str] = []
    if not request.ticker:
        errors.append("Ticker is required")
    if request.side not in SIDES:
        errors.append(f"Side must be one of {', '.join(SIDES)}")
    if request.order_type not in ORDER_TYPES:
        errors.append(f"Order type must be one of {', '.join(ORDER_TYPES)}")
    if request.time_in_force not in TIME_IN_FORCE:
        errors.append(f"Time in force must be one of {', '.join(TIME_IN_FORCE)}")
    if not request.quantity or request.quantity <= QUANTITY_EPSILON:
        errors.append("Quantity must be greater than zero")
    if request.order_type in (LIMIT, STOP_LIMIT) and not request.limit_price:
        errors.append(f"A {request.order_type} order needs a limit price")
    if request.order_type in (STOP, STOP_LIMIT) and not request.stop_price:
        errors.append(f"A {request.order_type} order needs a stop price")
    for label, value in (("limit price", request.limit_price), ("stop price", request.stop_price)):
        if value is not None and value <= 0:
            errors.append(f"The {label} must be greater than zero")
    return errors


def estimated_notional(request: OrderRequest, reference_price: Optional[float]) -> Optional[float]:
    """Worst-case cash impact used for buying-power and notional-cap checks."""
    candidates = [
        price
        for price in (request.limit_price, request.stop_price, reference_price)
        if price is not None and price > 0
    ]
    if not candidates:
        return None
    return round(request.quantity * max(candidates), 2)


def preflight(request: OrderRequest, context: GateContext) -> list[Check]:
    """Every live-execution gate, in the order an operator would reason about them."""
    checks: list[Check] = []
    errors = validate_request(request)
    checks.append(
        Check(
            "order_valid",
            not errors,
            "Order parameters are well formed" if not errors else "; ".join(errors),
        )
    )
    checks.append(
        Check(
            "live_trading_configured",
            context.config_enabled,
            "Live trading is enabled in configuration"
            if context.config_enabled
            else "Live trading is disabled by configuration (LIVE_TRADING_ENABLED is false)",
        )
    )
    checks.append(
        Check(
            "operator_acknowledged",
            context.acknowledged,
            "A live-trading acknowledgement is on record"
            if context.acknowledged
            else "No operator acknowledgement is on record for live trading",
        )
    )
    checks.append(
        Check(
            "kill_switch_clear",
            not context.trading_disabled,
            "No kill switch is engaged"
            if not context.trading_disabled
            else f"Trading is disabled: {context.disabled_reason or 'kill switch engaged'}",
        )
    )
    checks.append(
        Check(
            "broker_configured",
            bool(context.broker) and context.credentials_present,
            f"Broker {context.broker} has credentials"
            if context.broker and context.credentials_present
            else "No broker adapter with credentials is configured",
        )
    )
    checks.append(
        Check(
            "broker_sandbox",
            context.sandbox,
            "Broker endpoint is a sandbox/paper endpoint"
            if context.sandbox
            else "Broker endpoint is a production endpoint",
            blocking=False,
        )
    )
    fresh = (
        context.reference_price is not None
        and context.price_age_seconds is not None
        and context.price_age_seconds <= context.max_price_age_seconds
    )
    if context.reference_price is None:
        price_detail = "No reference price is available for this ticker"
    elif context.price_age_seconds is None:
        price_detail = "The reference price has no timestamp, so staleness cannot be proven"
    elif not fresh:
        price_detail = (
            f"The reference price is {context.price_age_seconds:,.0f}s old, above the "
            f"{context.max_price_age_seconds:,.0f}s limit"
        )
    else:
        price_detail = (
            f"Reference price ${context.reference_price:,.4f} is "
            f"{context.price_age_seconds:,.0f}s old"
        )
    checks.append(Check("price_fresh", fresh, price_detail))
    checks.append(
        Check(
            "data_quality",
            context.data_eligible is True,
            "Market data passes the quality gate"
            if context.data_eligible is True
            else (context.data_reason or "Market-data quality could not be confirmed"),
        )
    )
    is_open = market_open(context.now, request.asset_type)
    checks.append(
        Check(
            "market_open",
            is_open,
            "The venue session is open"
            if is_open
            else f"The {request.asset_type} venue is closed at {context.now.isoformat()}",
        )
    )
    checks.append(
        Check(
            "not_halted",
            context.halted is False and context.tradable is not False,
            "The symbol is tradable and not halted"
            if context.halted is False and context.tradable is not False
            else "The symbol is halted or not tradable at the broker",
        )
    )
    if request.side == SELL:
        covered = context.held_quantity + QUANTITY_EPSILON >= request.quantity
        if covered:
            short_detail = f"{context.held_quantity:g} units are held, covering the sale"
        elif context.shortable is True:
            short_detail = "The remainder is short-sellable at the broker"
        else:
            short_detail = (
                f"Only {context.held_quantity:g} units are held and the symbol is not "
                "confirmed short-sellable"
            )
        checks.append(Check("short_availability", covered or context.shortable is True, short_detail))
    notional = estimated_notional(request, context.reference_price)
    if request.side == BUY:
        affordable = (
            notional is not None
            and context.buying_power is not None
            and notional <= context.buying_power + 1e-9
        )
        if notional is None or context.buying_power is None:
            buying_detail = "Buying power or order notional could not be determined"
        elif not affordable:
            buying_detail = (
                f"Order needs ${notional:,.2f} but only ${context.buying_power:,.2f} "
                "of buying power is available"
            )
        else:
            buying_detail = f"${notional:,.2f} of ${context.buying_power:,.2f} buying power"
        checks.append(Check("buying_power", affordable, buying_detail))
    within_cap = notional is not None and notional <= context.max_order_notional + 1e-9
    checks.append(
        Check(
            "notional_cap",
            within_cap,
            f"${notional:,.2f} is within the ${context.max_order_notional:,.2f} per-order cap"
            if within_cap
            else (
                f"${notional:,.2f} exceeds the ${context.max_order_notional:,.2f} per-order cap"
                if notional is not None
                else "Order notional could not be determined"
            ),
        )
    )
    breaker_clear = context.breaker_active is False
    checks.append(
        Check(
            "risk_breakers_clear",
            breaker_clear,
            "No portfolio risk breaker is active"
            if breaker_clear
            else "; ".join(context.breaker_reasons) or "Portfolio risk state could not be confirmed",
        )
    )
    return checks


def blockers(checks: Sequence[Check]) -> list[Check]:
    return [check for check in checks if not check.passed and check.blocking]


def submittable(checks: Sequence[Check]) -> bool:
    return not blockers(checks)


def fingerprint(request: OrderRequest) -> str:
    """Stable hash of the previewed intent.

    Submission requires the operator to echo the fingerprint returned by
    ``preview``, so an approval can never be replayed against a different
    ticker, side, size or price than the one that was reviewed.
    """
    canonical = json.dumps(request.as_dict(), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def audit_hash(previous_hash: str, record: dict[str, object]) -> str:
    """Chain hash making the audit log tamper-evident.

    Each entry commits to the previous entry's hash, so removing or editing any
    record breaks every hash after it.
    """
    canonical = json.dumps(record, sort_keys=True, default=str)
    return hashlib.sha256(f"{previous_hash}|{canonical}".encode()).hexdigest()


def verify_chain(entries: Sequence[dict[str, object]]) -> tuple[bool, Optional[int]]:
    """Recompute the chain, returning ``(intact, first_broken_entry_id)``."""
    previous = ""
    for entry in entries:
        expected = audit_hash(previous, entry["record"])
        if expected != entry["entry_hash"]:
            return False, int(entry["id"])
        previous = str(entry["entry_hash"])
    return True, None


def reconcile(local: dict[str, object], remote: dict[str, object]) -> dict[str, object]:
    """Difference between our stored order state and the broker's."""
    remote_status = normalize_status(remote.get("status"))
    remote_filled = float(remote.get("filled_qty") or 0.0)
    local_filled = float(local.get("filled_quantity") or 0.0)
    status_matches = str(local.get("status")) == remote_status
    quantity_matches = abs(local_filled - remote_filled) <= QUANTITY_EPSILON
    return {
        "order_id": local.get("id"),
        "broker_order_id": local.get("broker_order_id"),
        "local_status": local.get("status"),
        "remote_status": remote_status,
        "local_filled_quantity": local_filled,
        "remote_filled_quantity": remote_filled,
        "status_matches": status_matches,
        "quantity_matches": quantity_matches,
        "in_sync": status_matches and quantity_matches,
    }
