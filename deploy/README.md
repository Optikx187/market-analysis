# Compose deployment profiles

Always combine one overlay with the root `docker-compose.yml`.

| Overlay | Purpose |
| --- | --- |
| `compose.single-node.yml` | Explicit form of the supported default deployment |
| `compose.sandbox.yml` | Offline-safe deterministic market, paper-broker, and notification boundaries |
| `compose.multi-worker.yml` | Remote-lab topology with private stateless quant workers |
| `compose.postgresql.yml` | PostgreSQL 16 market-data and portfolio adapters |

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
`MARKET_DATA_STORAGE_BACKEND=sqlite-transition`; that explicit compatibility
mode preserves the existing `data-ingestion-db` and `portfolio-db` volumes
until the separate import and reconciliation stage completes. The PostgreSQL
overlay switches both services to PostgreSQL, mounts both SQLite sources
read-only, and does not import, delete, or rewrite them. Existing deployments
must stay on `sqlite-transition` until the separate import proves exact candle,
cash, equity, order, fill, reservation, and P&L reconciliation.

If a fresh target migration fails, stop the overlay, drop the incomplete
`portfolio` schema, rerun the service migrations, and replay deterministic
tests. Do not clean or restore over either source SQLite volume. Historical
import/cutover remains issue #94.
