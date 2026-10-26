# Compose deployment profiles

Always combine one overlay with the root `docker-compose.yml`.

| Overlay | Purpose |
| --- | --- |
| `compose.single-node.yml` | Explicit form of the supported default deployment |
| `compose.sandbox.yml` | Offline-safe deterministic market, paper-broker, and notification boundaries |
| `compose.multi-worker.yml` | Remote-lab topology with private stateless quant workers |
| `compose.postgresql.yml` | PostgreSQL 16 market-data, research, and portfolio adapters |

Use a stable, unique Compose project name (`-p`) for each concurrent
deployment. This isolates containers, networks, and volumes.

Only frontend/nginx publishes a host port. All Python services use `expose`
and remain reachable only through the Compose network.

The sandbox fails startup if market or notification modes are not fake, if
provider endpoints do not use reserved `.invalid` hosts, if live execution is
enabled, or if its broker endpoint is external. Its data volumes use
`sandbox-*` logical names and never reuse the single-node databases.

The multi-worker profile keeps scheduled scans on the singleton
`quant-engine`. Its additional `quant-worker` replicas are stateless, use an
ephemeral backtest database, and do not publish host ports. Durable distributed
scheduling is intentionally deferred until broker, lease, and idempotency
support is implemented.

For a fresh deployment, initialize `.env`, start PostgreSQL, build the service
images, and apply every service-owned migration before starting the PostgreSQL
overlay:

```bash
./manage install --no-start
docker compose \
  -f docker-compose.yml \
  -f deploy/compose.single-node.yml \
  -f deploy/compose.postgresql.yml \
  up -d postgresql
docker compose \
  -f docker-compose.yml \
  -f deploy/compose.single-node.yml \
  -f deploy/compose.postgresql.yml \
  build
docker compose \
  -f docker-compose.yml \
  -f deploy/compose.single-node.yml \
  -f deploy/compose.postgresql.yml \
  run --rm --no-deps data-ingestion python -m alembic upgrade head
docker compose \
  -f docker-compose.yml \
  -f deploy/compose.single-node.yml \
  -f deploy/compose.postgresql.yml \
  run --rm --no-deps quant-engine python -m alembic upgrade head
docker compose \
  -f docker-compose.yml \
  -f deploy/compose.single-node.yml \
  -f deploy/compose.postgresql.yml \
  run --rm --no-deps portfolio-engine python -m alembic upgrade head
docker compose \
  -f docker-compose.yml \
  -f deploy/compose.single-node.yml \
  -f deploy/compose.postgresql.yml \
  up -d
```

For the remote-lab topology, combine `compose.multi-worker.yml` and
`compose.postgresql.yml`. The root configuration deliberately declares
all three storage backends as `sqlite-transition`; that explicit compatibility
mode preserves the existing `data-ingestion-db`, `quant-engine-db`, and
`portfolio-db` volumes until the separate import and reconciliation stage
completes. The PostgreSQL overlay switches all three durable services to
PostgreSQL, mounts every SQLite source read-only, stores oversized research
artifacts in a separate writable volume, and does not import, delete, or
rewrite source data. Existing deployments must stay on `sqlite-transition`
until `./manage import-postgresql` (or
`.\manage.ps1 import-postgresql`) completes a clean dry run and an
operator-approved `--apply`. The migration runner records stable UUID
mappings and resumable checkpoints under `operations`, copies verified
research artifacts, and reconciles exact candle, research-run/lineage, cash,
equity, order, fill, reservation, and P&L values. Existing price-alert rows
require `--accept-deferred market.price_alerts` and are preserved in the
import ledger pending notification-owned storage.

Apply mode requires a verified backup and never edits runtime backend flags:

```bash
./manage import-postgresql --apply \
  --accept-deferred market.price_alerts
```

Resume an interrupted run with `--apply --resume RUN_ID`; repeated imports are
idempotent. Cutover remains a separate operator action and is blocked by any
count, key, checksum, artifact, ownership, or financial mismatch. Imported
live-execution controls are forced disabled.

Each service has independent migration state in the shared database:
`alembic_version`, `alembic_version_research`, and
`alembic_version_portfolio`. Run every service migration command shown above;
one service reaching its head does not migrate the other schemas.

If a fresh target migration fails, stop the overlay, drop the incomplete
service-owned schema (`market_data`, `research`, or `portfolio`), rerun the
service migrations, and replay deterministic tests. Do not clean or restore
over any source SQLite volume. Before cutover, rollback is the unchanged
SQLite profile. For a disposable failed target, drop the four new schemas and
all three Alembic version tables, rerun migrations, and replay the import.

## PostgreSQL operations

Use the cross-platform management commands rather than raw volume copies:

```text
postgres-backup [DIRECTORY]
postgres-restore-drill DIRECTORY
postgres-status
postgres-maintain [--table SCHEMA.TABLE]...
postgres-retention --as-of TIMESTAMP
```

`postgres-backup` uses a consistent custom-format `pg_dump` and writes a
checksum/count/revision manifest. `postgres-restore-drill` restores into a
temporary database, reconciles it, and drops it without touching the active
database. Retention defaults to dry-run and apply requires the verified backup
directory mounted read-only with revisions and exact table counts still
matching the active database. Run backups daily, restore drills monthly, and
`postgres-status` often enough to alert on growth, dead tuples, long-running
queries, or the documented 50-million-row/100-GiB partition thresholds.

Back up the separate `research-artifacts` volume with the same recovery set;
logical PostgreSQL dumps do not include artifact files. For Aurora, use
automated backups plus continuous point-in-time recovery, cross-account copy
or export where required, and scheduled restore/failover rehearsals. Do not
treat an Aurora snapshot as a substitute for tested logical export or
application-level reconciliation.
