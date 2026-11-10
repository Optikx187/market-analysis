# Scanner worker capacity and recovery proof

This report records the deterministic Stage 2 baseline for dedicated scanner
workers. It is a correctness and capacity-planning fixture, not a production
load-test guarantee.

## Tested configuration

| Dimension | Tested value |
| --- | --- |
| Revision | Issue #103 verification branch |
| Platform | Linux development VM |
| Durable queue | SQLite transition store |
| Worker count | 2 concurrent claimers |
| Queued runs | 250 |
| Tickers in one scan | 250 deterministic crypto fixtures |
| Schedule cadence | 15 minutes |
| Lease failure | First worker abandoned one 1-second lease |
| Providers/brokers | Deterministic fakes only; no broker submission |

Run:

```bash
cd services/quant-engine
PYTHONPATH=. pytest tests/test_scanner_capacity.py -q --durations=2
cd ../..
python scripts/verify_scanner_failover.py
```

Observed on the recorded VM:

- the 250-ticker scanner fixture completed in `0.29s`;
- the two-worker 250-run queue/failover fixture completed in `0.56s`;
- all 250 durable run IDs completed exactly once;
- the abandoned run resumed with `attempt_count=2`;
- queue depth, running leases, dead jobs, and duplicate keys ended at zero;
- both workers claimed work;
- one recovery attempt was visible in throughput metrics.

The process-level proof also started two real scanner-worker processes against
one migrated durable store, killed the process holding an active manual-run
lease, and observed the surviving process complete the same correlation ID
with `attempt_count=2`. The recovered deterministic scan processed one ticker
in about `4.6s` while the fixture intentionally delayed downstream responses;
the killed heartbeat became stale, the survivor remained active, queue depth
returned to zero, and no duplicate idempotency key appeared.

These durations exclude real network latency and must not be used as a cloud
instance sizing promise.

## Capacity interpretation

A 250-ticker, 15-minute cadence requires an average processing rate of
`0.278` tickers/second. The deterministic scan made 753 data-ingestion calls
(assets, quality, earnings, daily, 4-hour, and 1-hour data) and 250 portfolio
risk calls. Provider/service latency—not local scoring—will therefore be the
first expected bottleneck. The observability endpoint reports call count,
errors, average latency, and maximum latency by downstream service so an
operator can verify that assumption.

SQLite serializes claims and is retained for simple single-node operation.
PostgreSQL uses `FOR UPDATE SKIP LOCKED` and is the required durable queue for
multi-host workers. Scale worker replicas only after provider limits and
portfolio capacity are measured with the intended ticker count.

## Operational thresholds

`GET /api/scanner/observability` exposes per-schedule queue age/depth, active
and expired leases, dead jobs, throughput, retries, duration percentiles,
provider metrics, problem correlation IDs, and worker heartbeats.

Default alerts:

| Alert | Default threshold |
| --- | --- |
| Stale worker | 30 seconds without heartbeat |
| Persistent queue lag | 300 seconds |
| Slow run | 120-second P95 duration |
| Error rate | 20% over 15 minutes |
| Dead job | Any run exhausting configured attempts |
| Expired lease | Any running run beyond lease expiry |

Tune these through the corresponding `SCANNER_*` environment values. Alert
responses are additive JSON contracts; external metrics/dashboard exporters
can consume them in a later issue.

## Reproduction and rollback

The `0004_research` migration only adds
`scanner_worker_heartbeats`. Run both service-owned research migrations before
starting workers. For rollback to code expecting `0003_research`, stop all
workers and restore the verified pre-migration research backup; do not stamp
an active database backward or discard run evidence.
