# PostgreSQL durable-state contract

Issue [#90](https://github.com/Optikx187/market-analysis/issues/90)
defines the relational boundary that later service-specific PostgreSQL
conversions must implement. It does not migrate production data or change the
current SQLite runtime.

## Decisions

- [ADR 0001: Logical schemas and ownership](adr-0001-logical-schemas.md)
- [ADR 0002: Identifiers, tenancy, and time](adr-0002-identifiers-tenancy-time.md)
- [ADR 0003: Financial precision and rounding](adr-0003-financial-precision.md)
- [ADR 0004: Indexing, retention, and partitioning](adr-0004-indexing-retention-partitioning.md)

## Executable contract

- [`contracts/postgresql/schema.sql`](../../../contracts/postgresql/schema.sql)
  is a non-runtime PostgreSQL 16 DDL prototype.
- [`contracts/postgresql/verify.sql`](../../../contracts/postgresql/verify.sql)
  checks exact decimals, UTC timestamps, uniqueness, tenancy keys, and
  representative index eligibility.
- [`contracts/postgresql/financial-fields.json`](../../../contracts/postgresql/financial-fields.json)
  maps every currently persisted SQLAlchemy `Float` field to a durable decimal
  domain and conversion rule.

Run the prototype against an isolated PostgreSQL 16 container:

```bash
python scripts/verify_postgresql_contract.py
```

The verifier does not publish a host port, does not use application
credentials, and always removes its container. It is a contract check, not a
production migration.

## Boundary for later tickets

Issues #91 through #94 implement service conversion and read-only,
checkpointed import/reconciliation. Issue #95 owns PostgreSQL maintenance,
backup, and restore policy. They must preserve these decisions or supersede
them with a reviewed ADR. In particular:

- application-generated UUIDv7 identifiers must not be replaced by local
  sequence identifiers;
- durable financial values must not be persisted as binary floating point;
- market-data identity must use canonical instruments plus provider mappings,
  not ticker strings alone;
- tenant-owned rows must carry `tenant_id` and the relevant owner key;
- candle writes must preserve the declared natural uniqueness boundary;
- live-order authorization must remain synchronous and fail closed rather than
  depend on the future event broker.

The import implementation is `tools/postgresql-import/`, exposed through
`manage import-postgresql`. Dry-run mode writes no domain records. Apply mode
requires a verified backup, records stable mappings and table checkpoints in
`operations`, preserves unsupported alert rows explicitly, and never changes
runtime storage flags. Cutover requires a reconciled report and remains an
operator-controlled step.

The operations implementation is `tools/postgresql-operations/`, exposed
through the `manage postgres-*` commands. It provides extension-free logical
backup manifests, isolated restore drills, growth and active-query reporting,
allowlisted `VACUUM (ANALYZE)`, and dry-run-first bounded candle retention.
Retention apply requires a fresh backup whose revisions and exact table counts
still match the active database. Trade, order, fill, journal, live-execution,
and import evidence is not in a deletion path. Aurora backup/PITR remains an
additional recovery layer rather than a substitute for logical restore and
application reconciliation.
