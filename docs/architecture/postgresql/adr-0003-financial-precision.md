# ADR 0003: Financial precision and rounding

- Status: Accepted
- Date: 2026-10-12

## Decision

Durable financial values use PostgreSQL `NUMERIC`; binary `real`/`double
precision` and SQLAlchemy `Float` are prohibited in the PostgreSQL persistence
model. Python service boundaries parse source values from strings into
`Decimal`, never through a binary float.

| Domain | PostgreSQL declaration | Unit | Typical fields |
| --- | --- | --- | --- |
| Price | `NUMERIC(30,12)` | quote currency per unit | OHLC, limits, stops, fills, alert thresholds |
| Quantity/volume | `NUMERIC(38,18)` | asset units | order/trade/fill quantities and candle volume |
| Money/notional | `NUMERIC(38,12)` | declared currency | balances, equity, fees, slippage, P&L, reserved cash |
| Ratio/return | `NUMERIC(24,12)` | fraction; `1 = 100%` | returns, drawdown, Kelly fraction, trailing percent |
| Score | `NUMERIC(18,8)` | percentage points `0..100` | signal confidence and timeframe agreement |
| Indicator | `NUMERIC(30,12)` | indicator-defined | durable ATR/EMA/RSI or future factor values |

Precision is deliberately larger than current UI precision. Venue tick size,
lot size, and currency minor units are business constraints layered on exact
storage, not reasons to discard provider or broker evidence.

The machine-readable inventory in
[`financial-fields.json`](../../../contracts/postgresql/financial-fields.json)
maps every currently persistent SQLAlchemy `Float` field to one of these
domains. Its unit test fails if a new durable float appears without a reviewed
mapping.

## Rounding

Default decimal rounding is `ROUND_HALF_UP`, matching the existing paper-order
contract and PostgreSQL `round(numeric, scale)` tie behavior. Domain-specific
rules override the default:

| Boundary | Rule |
| --- | --- |
| Provider input normalization | Parse exact decimal text; reject non-finite values; `ROUND_HALF_UP` only when input exceeds the domain scale |
| Buy/order quantity against a lot step | Round toward zero/down so sizing cannot exceed the approved quantity |
| Sell/close quantity | Round toward zero and cap at the owned/open quantity |
| Risk loss, required reserve, or debit authorization | Round up to the applicable tick/minor unit so risk or required cash is not understated |
| Broker fill | Persist the broker's exact decimal within domain scale; never recompute the broker's reported price/quantity from display values |
| Cash ledger posting | `ROUND_HALF_UP` to the currency minor unit; preserve high-precision source components and the posted amount |
| Aggregation | Sum stored decimals without intermediate rounding; round only when a posting/export contract requires it |
| Display | Format a copy; never write display-rounded values back to durable source fields |

The currency code is mandatory anywhere a money value can vary by currency.
ISO 4217 minor-unit metadata (for example USD `2`, JPY `0`) controls posting and
display quantization. Crypto settlement precision comes from the venue/asset
rule. Unknown precision fails closed rather than assuming cents.

## Existing percentage conversion

Current fields ending in `_pct` and `kelly_pct` persist percentage points.
PostgreSQL ratio fields store fractions:

```text
legacy 12.5 percent points -> 0.125000000000
legacy -3.25 percent points -> -0.032500000000
```

`timeframe_agreement` and `signal_confidence` remain score values from `0` to
`100`, because they are ranking/confidence scores rather than cash-return
fractions.

## Exact examples

The executable fixture verifies these values:

```text
price        123456789012.123456789012
quantity     987654321098765432.123456789012345678
money        -999999999999999999.123456789012
ratio        -1.234567890123
score        99.12345678
```

It also verifies `ROUND_HALF_UP`:

```text
2.345 -> 2.35
-2.345 -> -2.35
1.0049 -> 1.00 at two decimals
1.0050 -> 1.01 at two decimals
```

Decimal values cross JSON/API boundaries as strings where exact round-trip is
required. Numeric JSON may be emitted only for explicitly presentation-only
responses where loss of decimal representation cannot be persisted or used for
execution.

## Calculation policy

- Execution, accounting, and risk authorization use `Decimal` end to end.
- Technical analysis may use binary floating point in memory when the algorithm
  is approximate, but any durable result uses the indicator/score domain and
  cannot be fed into cash accounting without explicit decimal conversion.
- Derived notional and P&L calculations use unrounded decimal operands and are
  cast to the destination domain at the persistence boundary.
- Database constraints enforce non-negative prices/quantities where the domain
  requires it; signed money and return domains allow losses and reversals.

## Consequences

- Service conversion tickets must replace persistence model floats and update
  API serialization tests.
- SQLite imports must parse textual decimal representations and compare exact
  quantized values in reconciliation reports.
- PostgreSQL values may contain more precision than the current UI displays.
- No calculation may infer currency or tick/lot precision from the database
  column scale alone.
