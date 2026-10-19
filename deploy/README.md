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
until the separate import proves exact candle, research-run/lineage, cash,
equity, order, fill, reservation, and P&L reconciliation.

Each service has independent migration state in the shared database:
`alembic_version`, `alembic_version_research`, and
`alembic_version_portfolio`. Run every service migration command shown above;
one service reaching its head does not migrate the other schemas.

If a fresh target migration fails, stop the overlay, drop the incomplete
service-owned schema (`market_data`, `research`, or `portfolio`), rerun the
service migrations, and replay deterministic tests. Do not clean or restore
over any source SQLite volume. Historical import/cutover remains issue #94.
