# ADR 0002: Identifiers, tenancy, and time

- Status: Accepted
- Date: 2026-10-12

## Internal identifiers

New durable entities use application-generated UUIDv7 values stored in native
PostgreSQL `uuid` columns. UUIDv7 provides globally unique, time-ordered
identifiers without a database extension or a central sequence. PostgreSQL and
Aurora receive the identifier as a parameter; tables do not define
`gen_random_uuid()` defaults.

Exceptions require a stable natural identity:

- a candle is identified by
  `(provider_id, instrument_id, interval, bucket_start)`;
- an organization/user membership is identified by
  `(tenant_id, user_id)`;
- a user analysis selection is identified by
  `(tenant_id, user_id, analysis_definition_id)`.

UUIDs are immutable and never reused. Imported SQLite integer IDs are retained
in import mapping/audit data, not promoted to public PostgreSQL IDs.

## External identifiers

Provider, broker, OAuth, and notification-channel identifiers are opaque text.
They are unique only inside the authority that issued them:

```text
(provider_id, provider_symbol)
(broker_code, broker_order_id)
(destination_type, external_destination_id)
```

Internal UUIDs remain the primary and foreign keys. External identifiers are
retained after terminal status for reconciliation. Reuse by an external system
creates a conflict requiring operator review; it does not silently relink an
existing record.

Client-generated idempotency keys are scoped by tenant, operation, and actor:

```text
(tenant_id, operation, actor_id, idempotency_key)
```

Consumer deduplication uses `(consumer_name, event_id)`. Request fingerprints
are stored with the first result; a reused key with a different fingerprint is
rejected.

## Canonical instruments and symbols

Ticker strings are display/provider notation, not instrument identity.

- `market_data.instruments.id` is the immutable UUID referenced by research,
  portfolio, alert, and notification records.
- `instrument_key` is a stable canonical string such as `stock:XNAS:AAPL` or
  `crypto:BTC/USD`; it is unique and immutable.
- `canonical_symbol`, `base_asset`, `quote_asset`, `asset_class`, and optional
  `venue_id` are descriptive/query fields.
- `market_data.instrument_symbols` maps one provider's symbol to an instrument
  over a validity interval.
- An active provider symbol is unique per provider. A provider/instrument may
  retain prior aliases as closed validity intervals.
- Delisting, symbol changes, stock splits, and provider aliases never change
  historical instrument foreign keys.

Normalization trims surrounding whitespace and uses the provider's documented
case before lookup. It must not guess that equal text from two venues represents
the same instrument.

## Tenancy

`tenant_id` means `identity.organizations.id`. It is non-null on durable
research selections/runs, portfolios, orders, trades, notification routes, and
tenant audit records. `user_id` is additionally non-null where a human owns or
initiated the record.

Global reference data (`market_data.providers`, `venues`, `instruments`, and
`candles`) has no `tenant_id`. Access to global data is controlled at the
service/API layer; adding a tenant column would duplicate identical market
observations and undermine cache sharing.

Tenant uniqueness always includes `tenant_id`, for example:

```text
(tenant_id, user_id, portfolio_name)
(tenant_id, user_id, destination_name)
(tenant_id, source_type, source_key)
```

Application queries must include tenant scope even when the primary UUID is
known. Future row-level security may add defense in depth, but RLS is not a
substitute for scoped repository methods and service authorization.

The SQLite import creates an explicit default tenant and maps legacy
`user_key` values unchanged unless the operator supplies a JSON user mapping.
Application-generated UUIDv7 target IDs are retained in
`operations.import_mappings`; valid public research UUIDs are preserved for
API compatibility. Collisions, changed source rows, orphaned references, and
unmapped tables block reconciliation rather than selecting an arbitrary owner.

## Time

All instants use `timestamp with time zone` (`timestamptz`) and are transmitted
as RFC 3339 UTC values ending in `Z`. Application inputs without an offset are
rejected at service boundaries. Database sessions set `TimeZone = 'UTC'`.

Names describe semantics:

- `created_at` / `updated_at`: durable record lifecycle;
- `occurred_at`: domain or audit event time;
- `observed_at`: provider timestamp;
- `ingested_at`: accepted by data-ingestion;
- `bucket_start`: inclusive candle open instant;
- `expires_at`: exclusive expiry instant;
- `filled_at`, `submitted_at`, `reconciled_at`: execution lifecycle.

Ordering uses `(timestamp, id)` when timestamps can collide. Business schedules
store the IANA zone and local schedule separately; they are converted to UTC
instants before persistence. A database `date` may represent a civil reporting
day, but it must include the associated market/calendar zone in the contract.

## Candle uniqueness

The durable uniqueness boundary is:

```text
(provider_id, instrument_id, interval, bucket_start)
```

`interval` uses a controlled value (`1m`, `5m`, `15m`, `1h`, `4h`, `1d`,
`1w`). `bucket_start` is the UTC start of that provider's bar. Upserts may
replace an incomplete observation from the same provider, but must retain
lineage and update `provider_observed_at`, `ingested_at`, quality flags, and a
content checksum. A record from another provider is a separate observation and
must not overwrite the first.

## Consequences

- No PostgreSQL extension is required for UUID generation or case-insensitive
  identity.
- Public API identifiers can remain stable across import, failover, and worker
  scaling.
- Existing ticker/user string lookups need explicit mapping during conversion.
- Naive SQLite timestamps require a documented UTC assumption and validation
  during import; ambiguous values fail reconciliation.
