"""Pure paper-order semantics: validation, state machine and candle-driven fills.

Nothing in this module touches the database, HTTP or the clock, so every fill
rule (limit/stop crossing, trailing ratchet, spread, slippage and the liquidity
participation cap) stays directly testable against fixed OHLCV candles. Paper
orders never reach a broker: the simulation below is the only execution path.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

MARKET = "market"
LIMIT = "limit"
STOP = "stop"
STOP_LIMIT = "stop_limit"
BRACKET = "bracket"
TRAILING_STOP = "trailing_stop"

ORDER_TYPES = (MARKET, LIMIT, STOP, STOP_LIMIT, BRACKET, TRAILING_STOP)

PENDING = "pending"
SUBMITTED = "submitted"
PARTIALLY_FILLED = "partially_filled"
FILLED = "filled"
CANCELED = "canceled"
REJECTED = "rejected"
EXPIRED = "expired"

ORDER_STATUSES = (
    PENDING,
    SUBMITTED,
    PARTIALLY_FILLED,
    FILLED,
    CANCELED,
    REJECTED,
    EXPIRED,
)

TERMINAL_STATUSES = (FILLED, CANCELED, REJECTED, EXPIRED)
FILLABLE_STATUSES = (SUBMITTED, PARTIALLY_FILLED)

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    PENDING: frozenset({SUBMITTED, CANCELED, REJECTED, EXPIRED}),
    SUBMITTED: frozenset({PARTIALLY_FILLED, FILLED, CANCELED, EXPIRED}),
    PARTIALLY_FILLED: frozenset({PARTIALLY_FILLED, FILLED, CANCELED, EXPIRED}),
    FILLED: frozenset(),
    CANCELED: frozenset(),
    REJECTED: frozenset(),
    EXPIRED: frozenset(),
}

BUY = "BUY"
SELL = "SELL"

ENTRY = "entry"
TAKE_PROFIT = "take_profit"
STOP_LOSS = "stop_loss"
STANDALONE = "standalone"

GTC = "gtc"
DAY = "day"
TIME_IN_FORCE = (GTC, DAY)

QUANTITY_EPSILON = 1e-9
QUANTITY_PRECISION = 8
PRICE_PRECISION = 4
PRICE_QUANTUM = Decimal(1).scaleb(-PRICE_PRECISION)


def transition_allowed(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def round_quantity(value: float) -> float:
    return round(value, QUANTITY_PRECISION)


def round_price(value: float) -> float:
    """Quantize a price to four decimals with financial ROUND_HALF_UP.

    Every persisted or reported price passes through here, so half-ticks such as
    101.07575 resolve to 101.0758 instead of following binary float or banker's
    rounding. Execution semantics are unaffected: prices are computed at full
    precision first and priced orders are clamped to their limit afterwards.
    """
    return float(Decimal(str(float(value))).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP))


def day_session_end(timestamp: datetime) -> datetime:
    """Deterministic close of the DAY session containing ``timestamp``.

    Candles are normalized to naive UTC, so a session spans one UTC calendar
    day: that matches crypto's continuous 24h session and, for stocks, keeps a
    DAY order alive for the whole session of the candle that opened it without
    ever consulting the wall clock.
    """
    return datetime.combine(timestamp.date(), datetime.min.time()) + timedelta(days=1)


@dataclass(frozen=True)
class Candle:
    """A single true OHLCV bar. Intraday bars are never interpolated."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class FillConfig:
    """Execution frictions applied to every simulated fill."""

    spread_pct: float = 0.0
    slippage_pct: float = 0.0
    participation_pct: float = 1.0
    fee_pct: float = 0.0

    @property
    def cost_pct(self) -> float:
        return (self.spread_pct / 2.0) + self.slippage_pct


@dataclass(frozen=True)
class OrderState:
    """The subset of a persisted order the fill simulation needs."""

    side: str
    order_type: str
    quantity: float
    filled_quantity: float = 0.0
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    trail_percent: Optional[float] = None
    trail_amount: Optional[float] = None
    trail_reference_price: Optional[float] = None
    triggered: bool = False
    role: str = STANDALONE

    @property
    def remaining_quantity(self) -> float:
        return round_quantity(self.quantity - self.filled_quantity)

    @property
    def effective_type(self) -> str:
        """Bracket parents execute their entry as a limit when a limit is set."""
        if self.order_type == BRACKET:
            return LIMIT if self.limit_price is not None else MARKET
        return self.order_type


@dataclass(frozen=True)
class CandleOutcome:
    """What a single candle did to an order."""

    reason: str
    newly_triggered: bool = False
    trail_reference_price: Optional[float] = None
    effective_stop_price: Optional[float] = None
    fill_quantity: float = 0.0
    fill_price: Optional[float] = None
    fees: float = 0.0
    slippage: float = 0.0

    @property
    def filled(self) -> bool:
        return self.fill_quantity > QUANTITY_EPSILON and self.fill_price is not None

    @property
    def notional(self) -> float:
        if self.fill_price is None:
            return 0.0
        return round(self.fill_quantity * self.fill_price, 2)


def parse_candle(raw: dict[str, object]) -> Candle:
    """Validate one candle dictionary, rejecting anything not a true OHLCV bar."""
    missing = [key for key in ("timestamp", "open", "high", "low", "close") if raw.get(key) is None]
    if missing:
        raise ValueError(f"Candle is missing required fields: {', '.join(missing)}")
    timestamp = raw["timestamp"]
    if isinstance(timestamp, str):
        text = timestamp.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"Candle timestamp '{timestamp}' is not ISO-8601") from exc
    elif isinstance(timestamp, datetime):
        parsed = timestamp
    else:
        raise ValueError("Candle timestamp must be an ISO-8601 string or datetime")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    try:
        candle = Candle(
            timestamp=parsed,
            open=float(raw["open"]),
            high=float(raw["high"]),
            low=float(raw["low"]),
            close=float(raw["close"]),
            volume=float(raw.get("volume") or 0.0),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Candle OHLCV values must be numeric") from exc
    if min(candle.open, candle.high, candle.low, candle.close) <= 0:
        raise ValueError("Candle OHLC values must be positive")
    if candle.high < max(candle.open, candle.close) or candle.low > min(candle.open, candle.close):
        raise ValueError(
            f"Candle at {candle.timestamp.isoformat()} has an inconsistent high/low range"
        )
    if candle.volume < 0:
        raise ValueError("Candle volume cannot be negative")
    return candle


def parse_candles(raw: list[dict[str, object]]) -> list[Candle]:
    """Parse and order candles oldest-first so processing stays deterministic."""
    return sorted((parse_candle(row) for row in raw), key=lambda candle: candle.timestamp)


def trailing_reference(side: str, previous: Optional[float], candle: Candle) -> float:
    """Ratchet the trail high/low-water mark in the favorable direction only."""
    if side == SELL:
        return round_price(max(previous, candle.high) if previous is not None else candle.high)
    return round_price(min(previous, candle.low) if previous is not None else candle.low)


def trailing_stop_price(
    side: str,
    reference_price: float,
    trail_percent: Optional[float],
    trail_amount: Optional[float],
) -> float:
    """Effective stop implied by the trail reference; percent takes precedence."""
    if trail_percent:
        distance = reference_price * (trail_percent / 100.0)
    else:
        distance = trail_amount or 0.0
    if side == SELL:
        return round_price(max(0.0, reference_price - distance))
    return round_price(reference_price + distance)


def validate_order(
    side: str,
    order_type: str,
    quantity: float,
    limit_price: Optional[float],
    stop_price: Optional[float],
    trail_percent: Optional[float],
    trail_amount: Optional[float],
    take_profit_price: Optional[float],
    stop_loss_price: Optional[float],
    time_in_force: str,
) -> list[str]:
    """Collect every actionable validation problem so the UI can show them all."""
    errors: list[str] = []
    if side not in (BUY, SELL):
        errors.append(f"Side must be BUY or SELL, got '{side}'")
    if order_type not in ORDER_TYPES:
        errors.append(f"Order type must be one of {', '.join(ORDER_TYPES)}, got '{order_type}'")
    if quantity is None or quantity <= 0:
        errors.append("Quantity must be greater than zero")
    if time_in_force not in TIME_IN_FORCE:
        errors.append(f"Time in force must be one of {', '.join(TIME_IN_FORCE)}")
    for label, price in (("Limit price", limit_price), ("Stop price", stop_price)):
        if price is not None and price <= 0:
            errors.append(f"{label} must be greater than zero")
    if order_type in (LIMIT, STOP_LIMIT) and limit_price is None:
        errors.append(f"A {order_type} order requires a limit price")
    if order_type in (STOP, STOP_LIMIT) and stop_price is None:
        errors.append(f"A {order_type} order requires a stop price")
    if order_type == TRAILING_STOP:
        if not trail_percent and not trail_amount:
            errors.append("A trailing_stop order requires a trail percent or a trail amount")
        if trail_percent is not None and not 0 < trail_percent < 100:
            errors.append("Trail percent must be between 0 and 100")
        if trail_amount is not None and trail_amount <= 0:
            errors.append("Trail amount must be greater than zero")
    if order_type == BRACKET:
        if take_profit_price is None or stop_loss_price is None:
            errors.append("A bracket order requires both a take profit and a stop loss price")
        elif side == BUY and not stop_loss_price < take_profit_price:
            errors.append("A long bracket needs its stop loss below its take profit")
        elif side == SELL and not stop_loss_price > take_profit_price:
            errors.append("A short bracket needs its stop loss above its take profit")
    elif take_profit_price is not None or stop_loss_price is not None:
        errors.append("Take profit and stop loss prices are only valid on bracket orders")
    return errors


def reference_price_for_reservation(
    order_type: str,
    limit_price: Optional[float],
    stop_price: Optional[float],
    reference_price: Optional[float],
) -> Optional[float]:
    """Price used to reserve buying power before any candle is processed.

    Priced order types reserve against their own worst acceptable price so the
    reservation never depends on an external quote.
    """
    if order_type == LIMIT and limit_price:
        return limit_price
    if order_type == STOP_LIMIT and (limit_price or stop_price):
        return max(price for price in (limit_price, stop_price) if price)
    if order_type == STOP and stop_price:
        return stop_price
    if order_type == BRACKET and limit_price:
        return limit_price
    candidates = [price for price in (reference_price, limit_price, stop_price) if price]
    return candidates[0] if candidates else None


def simulate_candle(state: OrderState, candle: Candle, config: FillConfig) -> CandleOutcome:
    """Apply one candle to an order and report the trigger/fill it produces."""
    remaining = state.remaining_quantity
    order_type = state.effective_type
    trail_reference_value: Optional[float] = state.trail_reference_price
    effective_stop: Optional[float] = state.stop_price
    triggered = state.triggered
    newly_triggered = False

    if order_type == TRAILING_STOP:
        trail_reference_value = trailing_reference(state.side, state.trail_reference_price, candle)
        effective_stop = trailing_stop_price(
            state.side, trail_reference_value, state.trail_percent, state.trail_amount
        )

    def outcome(reason: str, **kwargs: object) -> CandleOutcome:
        return CandleOutcome(
            reason=reason,
            newly_triggered=newly_triggered,
            trail_reference_price=trail_reference_value,
            effective_stop_price=effective_stop,
            **kwargs,  # type: ignore[arg-type]
        )

    if remaining <= QUANTITY_EPSILON:
        return outcome("nothing_remaining")

    trigger_price: Optional[float] = None
    if order_type in (STOP, STOP_LIMIT, TRAILING_STOP):
        trigger_price = effective_stop
        if trigger_price is None:
            return outcome("missing_trigger_price")
        if not triggered:
            crossed = candle.high >= trigger_price if state.side == BUY else candle.low <= trigger_price
            if not crossed:
                return outcome("stop_not_crossed")
            triggered = True
            newly_triggered = True

    base_price = candle.open
    if trigger_price is not None:
        base_price = max(base_price, trigger_price) if state.side == BUY else min(base_price, trigger_price)

    limit_price = state.limit_price if order_type in (LIMIT, STOP_LIMIT) else None
    if limit_price is not None:
        marketable = candle.low <= limit_price if state.side == BUY else candle.high >= limit_price
        if not marketable:
            return outcome("limit_not_marketable")

    if state.side == BUY:
        price = base_price * (1.0 + config.cost_pct)
        if limit_price is not None:
            price = min(price, limit_price)
    else:
        price = base_price * (1.0 - config.cost_pct)
        if limit_price is not None:
            price = max(price, limit_price)

    participation_cap = round_quantity(candle.volume * config.participation_pct)
    if participation_cap <= QUANTITY_EPSILON:
        return outcome("no_liquidity")
    fill_quantity = round_quantity(min(remaining, participation_cap))
    fill_price = round_price(price)
    if limit_price is not None:
        # Rounding may never push a fill past its limit.
        fill_price = min(fill_price, limit_price) if state.side == BUY else max(fill_price, limit_price)
    adverse = fill_price - base_price if state.side == BUY else base_price - fill_price
    slippage = max(0.0, adverse) * fill_quantity
    return outcome(
        "filled" if fill_quantity >= remaining - QUANTITY_EPSILON else "partially_filled",
        fill_quantity=fill_quantity,
        fill_price=fill_price,
        fees=round(fill_quantity * fill_price * config.fee_pct, 6),
        slippage=round(slippage, 6),
    )


def weighted_average_price(fills: list[tuple[float, float]]) -> Optional[float]:
    """Weighted average price of ``(quantity, price)`` fills."""
    quantity = round_quantity(sum(fill[0] for fill in fills))
    if quantity <= QUANTITY_EPSILON:
        return None
    return round_price(sum(fill[0] * fill[1] for fill in fills) / quantity)
