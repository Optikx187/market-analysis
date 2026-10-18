from __future__ import annotations

import time
import uuid
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP, Decimal, localcontext

from sqlalchemy import Float, Numeric
from sqlalchemy.types import TypeDecorator

from app.config import settings


POSTGRESQL_STORAGE = (
    settings.PORTFOLIO_STORAGE_BACKEND.strip().lower() == "postgresql"
)

PRICE_PRECISION = 30
PRICE_SCALE = 12
QUANTITY_PRECISION = 38
QUANTITY_SCALE = 18
MONEY_PRECISION = 38
MONEY_SCALE = 12
RATIO_PRECISION = 24
RATIO_SCALE = 12
SCORE_PRECISION = 18
SCORE_SCALE = 8


def decimal_value(
    value: object,
    scale: int,
    *,
    rounding: str = ROUND_HALF_UP,
) -> Decimal:
    with localcontext() as context:
        context.prec = 60
        candidate = value if isinstance(value, Decimal) else Decimal(str(value))
        if not candidate.is_finite():
            raise ValueError("financial values must be finite")
        return candidate.quantize(Decimal(1).scaleb(-scale), rounding=rounding)


def price_decimal(value: object) -> Decimal:
    return decimal_value(value, PRICE_SCALE)


def quantity_decimal(value: object, *, reduce_only: bool = False) -> Decimal:
    return decimal_value(
        value,
        QUANTITY_SCALE,
        rounding=ROUND_DOWN if reduce_only else ROUND_HALF_UP,
    )


def money_decimal(value: object, *, authorize_debit: bool = False) -> Decimal:
    return decimal_value(
        value,
        MONEY_SCALE,
        rounding=ROUND_CEILING if authorize_debit else ROUND_HALF_UP,
    )


def ratio_decimal(value: object) -> Decimal:
    return decimal_value(value, RATIO_SCALE)


def score_decimal(value: object) -> Decimal:
    return decimal_value(value, SCORE_SCALE)


def money_add(*values: object) -> Decimal | float:
    result = money_decimal(
        sum((Decimal(str(value)) for value in values), Decimal(0))
    )
    return result if POSTGRESQL_STORAGE else float(result)


def money_subtract(left: object, right: object) -> Decimal | float:
    result = money_decimal(Decimal(str(left)) - Decimal(str(right)))
    return result if POSTGRESQL_STORAGE else float(result)


def money_product(
    quantity: object,
    price: object,
    *,
    authorize_debit: bool = False,
) -> Decimal | float:
    result = quantity_decimal(quantity) * price_decimal(price)
    posted = money_decimal(result, authorize_debit=authorize_debit)
    return posted if POSTGRESQL_STORAGE else float(posted)


def uuid7() -> uuid.UUID:
    timestamp_ms = int(time.time_ns() // 1_000_000)
    random_bits = int.from_bytes(uuid.uuid4().bytes, "big")
    value = (
        (timestamp_ms & ((1 << 48) - 1)) << 80
        | 0x7 << 76
        | ((random_bits >> 64) & 0xFFF) << 64
        | 0x2 << 62
        | (random_bits & ((1 << 62) - 1))
    )
    return uuid.UUID(int=value)


class ExactNumeric(TypeDecorator):
    cache_ok = True
    impl = Numeric

    def __init__(self, precision: int, scale: int):
        self.precision = precision
        self.scale = scale
        super().__init__()

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(
                Numeric(self.precision, self.scale, asdecimal=True)
            )
        return dialect.type_descriptor(Float())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return decimal_value(value, self.scale)
        return float(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return Decimal(value)
        return float(value)


PriceValue = lambda: ExactNumeric(PRICE_PRECISION, PRICE_SCALE)
QuantityValue = lambda: ExactNumeric(QUANTITY_PRECISION, QUANTITY_SCALE)
MoneyValue = lambda: ExactNumeric(MONEY_PRECISION, MONEY_SCALE)
RatioValue = lambda: ExactNumeric(RATIO_PRECISION, RATIO_SCALE)
ScoreValue = lambda: ExactNumeric(SCORE_PRECISION, SCORE_SCALE)
