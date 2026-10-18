# ADR 0001: Logical schemas and ownership

- Status: Accepted
- Date: 2026-10-12
- Decision owners: data, platform, and service architecture

## Context

The current services own independent SQLite files and sometimes identify the
same concept with unrelated ticker or user strings. Shared PostgreSQL must not
become a schema in which every service can read and write every table. The
target is one PostgreSQL 16/Aurora PostgreSQL cluster with explicit logical
ownership and stable cross-schema references.

## Decision

Use seven PostgreSQL schemas. A service role receives write access only to its
owned schema and explicit read access to referenced records. Cross-service
updates occur through APIs or versioned events; direct writes across ownership
boundaries are forbidden.

| Schema | Owner | Durable responsibilities | Initial lifecycle |
| --- | --- | --- | --- |
| `identity` | identity/auth boundary, initially portfolio-engine | Organizations, users, memberships, roles, API subjects | Created before tenant-owned data; records are disabled/tombstoned rather than silently reassigned |
| `market_data` | data-ingestion | Providers, venues, canonical instruments, provider symbols, candles, quality/source lineage | Instruments are durable; high-volume observations follow interval retention policy |
| `research` | quant-engine | Analysis definitions, user selections, analysis runs, backtest requests/results | Definitions are versioned; runs are immutable after completion except status/error finalization |
| `portfolio` | portfolio-engine | Portfolios, trades, journals, paper orders/fills, live orders/fills, risk and equity history | Financial records are append-only where practical; corrections use compensating records |
| `notifications` | notification-gateway | Destinations, routes, normalized notification records, delivery jobs/attempts | Delivery attempts are append-only; secrets stay in the credential boundary, not payloads |
| `operations` | platform/operations boundary | Idempotency receipts, import runs, service audit records, contract helper domains/functions | Operational records have explicit expiry; security and execution audits use longer retention |
| `billing` | deferred commercial boundary | Reserved for plans, entitlements, usage, invoices, and provider references | Schema exists to reserve ownership; no billing table is approved in this ticket |

The prototype DDL contains the minimum tables needed to prove these boundaries.
It is intentionally not an Alembic migration and is not imported by a running
service.

## Current SQLite model mapping

Every current durable table has an approved logical destination. A later
conversion may split a row across destinations, but it may not leave ownership
implicit.

| Current table/store | PostgreSQL destination |
| --- | --- |
| `assets` | Canonical identity in `market_data.instruments`; user tracking state in future `research.watchlists` / `watchlist_items` |
| `candles` | `market_data.candles` |
| `price_alerts` | Future `notifications.alert_rules`, tenant/user scoped and keyed by instrument UUID |
| `users` | `identity.users`; tenant role in `identity.memberships` |
| `credential_secrets` | Future `operations.credential_bindings` containing an opaque secret-manager reference, never plaintext |
| `portfolio` | `portfolio.portfolios` |
| `trades` | `portfolio.trades` |
| `trade_executions` | `portfolio.trade_executions` |
| `trade_journals` | `portfolio.trade_journals` |
| `paper_orders` | `portfolio.paper_orders` |
| `paper_order_fills` | `portfolio.paper_order_fills` |
| `paper_order_events` | Future `portfolio.paper_order_events` append-only lifecycle records |
| `equity_snapshots` | `portfolio.equity_snapshots` |
| `risk_snapshots` | `portfolio.risk_snapshots` |
| `alert_logs` | Future `portfolio.trade_alerts`, retaining risk decision and research lineage |
| `action_items` | Future `portfolio.action_items`, tenant/user scoped |
| `dashboard_preferences` | Future `portfolio.dashboard_preferences`, tenant/user scoped |
| `live_trading_control` | Future `portfolio.live_trading_controls`, tenant/portfolio scoped and fail closed |
| `live_orders` | `portfolio.live_orders` |
| `live_order_fills` | `portfolio.live_order_fills` |
| `live_execution_audit` | Future `portfolio.live_execution_audit` plus security events in `operations.audit_events` |
| `backtest_runs` SQLite store | `research.backtest_runs` |

The future-table labels are part of this contract even where the executable
prototype omits low-volume detail tables. Issues #91 through #93 must add them
to their service-owned migrations before importing the corresponding SQLite
records.

## Relationships

- `identity.organizations.id` is the `tenant_id` root.
- `identity.users.id` is the durable human/service subject.
- Tenant-owned research, portfolio, notification, and operations records carry
  `tenant_id`; user-owned records also carry `user_id`.
- `market_data.instruments.id` is global reference data. Tenant tables refer to
  it by UUID rather than duplicating symbols.
- Provider and broker identifiers are scoped by their provider/broker and never
  replace internal identifiers.
- Research output may create normalized events and portfolio proposals, but it
  cannot submit a live order.
- Notification delivery consumes normalized events and cannot mutate research
  results or portfolio execution state.
- Live execution authorization, kill switches, broker preflight, and broker
  submission remain synchronous portfolio responsibilities. Broker availability
  or an event-bus failure must never make execution fail open.

## Table boundary summary

### Identity

`organizations`, `users`, and `memberships` establish tenancy. Future
authentication providers map their immutable subject to `users.subject`.
Credentials and secret material are not stored in identity profile rows.

### Market data

`providers`, `venues`, `instruments`, `instrument_symbols`, and `candles`
separate canonical identity from provider notation. `candles` records source,
observation, ingestion, quality, and freshness metadata. Tenant watchlists and
alerts do not belong in this global schema.

### Research

`analysis_definitions` identifies a module and version.
`analysis_selections` records tenant/user choice and configuration.
`analysis_runs` stores normalized request/result snapshots, while
`backtest_runs` preserves the existing dedicated research-history boundary.
Completed runs are immutable evidence.

### Portfolio

`portfolios`, `trades`, `trade_executions`, `trade_journals`,
`paper_orders`, `paper_order_fills`, `live_orders`, `live_order_fills`, and
`equity_snapshots` preserve the current distinction between simulated and
real-money execution. Paper and live orders remain separate tables so they
cannot be aggregated or submitted through the wrong code path by accident.

### Notifications

`destinations` stores owned endpoints by opaque secret reference.
`routes` selects analysis/event types and destinations.
`notifications` stores a normalized user-facing payload.
`delivery_jobs` and `delivery_attempts` provide durable, idempotent delivery
without coupling the producer to Discord, Telegram, or a future channel.

### Operations

`idempotency_keys` scopes retried commands, `consumer_receipts` deduplicates
future event consumption, `import_runs` supports issue #94 reconciliation, and
`audit_events` records operator/security actions. Each producer will eventually
own a transactional outbox in the same transaction as its domain write; a
single cross-service shared outbox is explicitly rejected.

### Billing

The namespace is reserved, but plans, subscriptions, entitlements, usage, and
invoices are deferred to issues #121 and #122. Research or notification code
must not infer billing status from missing rows or hard-coded tiers.

## Consequences

- Service roles and grants become part of future migrations.
- Cross-schema foreign keys are allowed for stable roots such as tenant, user,
  and instrument IDs, but services must not use them as permission to perform
  cross-owner writes.
- SQLite remains the supported local sandbox store until the service conversion
  tickets land.
- Consolidating into one Aurora cluster does not imply one application role or
  one schema owner.
