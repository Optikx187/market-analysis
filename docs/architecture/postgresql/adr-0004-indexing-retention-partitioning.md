# ADR 0004: Indexing, retention, and partitioning

- Status: Accepted
- Date: 2026-10-12

## Query and uniqueness contract

The initial PostgreSQL conversion must create only indexes tied to known access
paths. Index names in the prototype are stable review fixtures, not a license
to duplicate them in every service.

| Access path | Constraint/index |
| --- | --- |
| Canonical instrument lookup | unique `instrument_key`; secondary `(asset_class, canonical_symbol)` |
| Active provider symbol lookup | partial unique `(provider_id, provider_symbol)` where `valid_to IS NULL` |
| Candle upsert | unique `(provider_id, instrument_id, interval, bucket_start)` |
| Latest/range candles | `(instrument_id, interval, bucket_start DESC)` including OHLCV/source fields |
| Provider ingestion audit | `(provider_id, ingested_at DESC)` |
| Tenant research history | `(tenant_id, analysis_definition_id, created_at DESC)` |
| Tenant backtest history | `(tenant_id, user_key, ticker, created_at DESC, run_id DESC)` |
| User portfolio lookup | unique `(tenant_id, user_id, name)` |
| Open paper/live orders | partial `(tenant_id, user_id, created_at DESC)` for active statuses |
| Broker reconciliation | unique `(broker_code, broker_order_id)` when broker ID exists |
| Notification delivery | unique `(tenant_id, notification_id, destination_id)` plus partial due-job index |
| Consumer idempotency | unique `(consumer_name, event_id)` |
| Tenant audit | `(tenant_id, occurred_at DESC, id DESC)` |

Tenant-owned uniqueness includes `tenant_id`. A globally unique UUID does not
remove the requirement for tenant predicates in application queries.

The verification fixture inserts deterministic data, runs `ANALYZE`, disables
sequential scans only for plan inspection, and checks that representative
latest-candle, open-order, due-delivery, and audit queries are eligible to use
their intended indexes. This proves index shape, not production latency or
cardinality estimates. Pre-production load testing must review
`EXPLAIN (ANALYZE, BUFFERS)` with realistic volumes.

## Retention defaults

These are product defaults, not a claim of legal or regulatory compliance.
Operator policy may retain data longer; legal/privacy requirements supersede
the defaults. Deletion jobs must be dry-run/reportable and backup-aware.

| Data | Default online retention | Archive/delete behavior |
| --- | --- | --- |
| `1m`, `5m`, `15m` candles | 400 days | Archive before batch deletion |
| `1h`, `4h` candles | 7 years | Archive before batch deletion |
| `1d`, `1w` candles | Indefinite while instrument is supported | No automatic delete |
| Analysis/backtest runs used for a recommendation | 7 years | Preserve request, result, module version, and lineage |
| Non-actionable ad-hoc research runs | 2 years | Delete in tenant-scoped batches |
| Trades, orders, fills, journals, live audit | Minimum 7 years | Immutable archive; corrections remain linked |
| Equity snapshots | Daily aggregates 7 years; intraday 2 years | Downsample before deletion |
| Notification payloads | 90 days | Remove message bodies; retain delivery metadata 2 years |
| Notification attempts/dead letters | 1 year | Retain final status and hashes 2 years |
| Command idempotency records | 30 days after terminal result unless domain requires longer | Expire only after retry window |
| Consumer receipts | 90 days or broker replay horizon, whichever is longer | Expire by consumer policy |
| Operator/security audit | 7 years | Append-only archive |
| Identity | Account lifetime plus approved deletion/tombstone policy | Preserve non-personal referential tombstone |
| Billing | Deferred | Defined with billing implementation |

Retention runs by UTC time bounds and records row counts, oldest/newest
timestamps, policy version, and operator/job identity in `operations.audit_events`.

## Partitioning

Start unpartitioned for the first PostgreSQL/Aurora conversion. Native
PostgreSQL range partitioning is approved when a table reaches any reviewed
threshold:

- approximately 50 million rows;
- approximately 100 GiB including indexes;
- retention deletes or vacuum routinely exceed the maintenance window;
- measured query/maintenance evidence shows partition pruning is beneficial.

Candidates and future keys:

| Table | Candidate key/window |
| --- | --- |
| `market_data.candles` | monthly `RANGE (bucket_start)` |
| `portfolio.equity_snapshots` | quarterly `RANGE (captured_at)` |
| `notifications.delivery_attempts` | monthly `RANGE (attempted_at)` |
| `operations.audit_events` | monthly `RANGE (occurred_at)` |

The candle natural unique key already includes `bucket_start`, so it remains
valid when partitioned. Other tables must include the partition key in
partitioned primary/unique constraints or retain an unpartitioned identity
registry; migrations may not weaken event/order uniqueness to enable
partitioning.

Use PostgreSQL declarative `RANGE` partitions only. Do not depend on TimescaleDB,
pg_partman, or another extension in the base contract. Each partition receives
the required local indexes. Partition creation, attach/detach, and retention
must be explicit migration/maintenance operations with backup and restore
coverage.

## Aurora compatibility

The contract uses PostgreSQL 16 native `uuid`, `numeric`, `jsonb`,
`timestamptz`, generated identity columns for small internal catalogs, partial
indexes, covering indexes, expression indexes, and optional BRIN indexes. It
requires no extension. Aurora parameter groups must set UTC and collect enough
statistics for the documented query shapes.

Aurora read replicas may serve stale-tolerant research/history reads only.
Order submission, idempotency, portfolio/risk authorization, notification job
claims, and read-after-write reconciliation use the writer endpoint.

## Operational review

Before adding or removing an index:

1. capture the query and representative parameters;
2. record `EXPLAIN (ANALYZE, BUFFERS)` on production-like data;
3. assess write amplification and storage;
4. use concurrent index creation where supported by the migration boundary;
5. verify Aurora writer and replica behavior;
6. update the executable plan fixture when the access-path contract changes.
