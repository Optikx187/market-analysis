# Compose deployment profiles

Always combine one overlay with the root `docker-compose.yml`.

| Overlay | Purpose |
| --- | --- |
| `compose.single-node.yml` | Explicit form of the supported default deployment |
| `compose.sandbox.yml` | Offline-safe deterministic market, paper-broker, and notification boundaries |
| `compose.multi-worker.yml` | Remote-lab topology with private stateless quant workers |

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
