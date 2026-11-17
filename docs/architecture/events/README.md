# Versioned event contract

The application uses a transport-neutral event envelope between analysis,
risk, order, and operational producers and notification consumers. The
contract does not require a broker, queue, or notification provider.

The canonical v1 schema is
`schemas/events/v1/envelope.schema.json`. Deterministic examples are in
`schemas/events/v1/fixtures/`.

## Envelope

Every event includes:

- a stable UUID event identity and semantic event type;
- envelope and payload schema versions;
- producer service and version;
- tenant and audience routing identity;
- severity, occurrence time, and expiry time;
- a deterministic deduplication key;
- subject identity and safe deep links;
- a bounded, human-readable summary and disclaimer;
- a typed payload;
- correlation, causation, trace, tag, and redaction metadata;
- an explicit research-only marker.

The v1 payload kinds are:

| Kind | Purpose |
| --- | --- |
| `recommendation` | Versioned analysis output, evidence, and lineage |
| `incident` | Risk, data-quality, security, or operational incident |
| `order_state` | Paper or live order lifecycle state |
| `operation` | Worker or service status transition |

Prices and quantities in order events are decimal strings. This avoids
binary-float precision loss across serializers and transports.

## Producer and consumer boundary

The quant engine owns a producer-side Pydantic schema and converts a completed
analysis run into a `recommendation` envelope when the immutable run identity
is assigned. Analysis modules emit research data only and do not import
Discord, Telegram, Slack, email, SMS, or broker adapters.

The notification gateway owns the consumer validator. Its
`POST /api/events/validate` endpoint validates and redacts an envelope without
delivering it. Existing provider-specific `/api/notify` behavior is unchanged.
A provider adapter can use the validated `summary`, `deep_links`, and routing
metadata without importing any analysis module.

## Compatibility and quarantine

The consumer accepts only explicitly supported envelope versions. It never
guesses at an unknown version or silently drops an incompatible event.

Unknown versions, schema failures, invalid time ranges, audience/tenant
mismatches, and unsafe deep links are quarantined. Quarantine records contain:

- a reason code;
- event identity and declared schema version;
- a SHA-256 fingerprint of the original event;
- the redacted event;
- the paths of redacted fields.

`EventQuarantine` can write append-only JSON Lines when a path is configured.
Without a path it remains an in-process contract boundary suitable for tests
and the current single-node deployment. Durable broker/outbox processing is a
separate change.

## Redaction and safe links

Secret-like keys, including tokens, passwords, API keys, credentials,
authorization values, private keys, and webhook URLs, are replaced with
`[REDACTED]`. Matching secret values are also removed from human-readable
strings. Redacted paths are merged into `metadata.redacted_fields`.

Deep links must be an application-relative path or an absolute `http`/`https`
URL without embedded credentials. Unsafe schemes such as `javascript:` are
quarantined.

## Deduplication

The deterministic key hashes canonical JSON containing:

- event type;
- tenant identity;
- subject type and identity;
- typed payload.

Transport identity, delivery attempt, and provider destination are excluded.
Retries therefore keep the same key, while a materially different payload
receives a different key.

## Local verification

Use deterministic fixtures and fake providers only:

```bash
./verify-changes service notification-gateway --base-ref HEAD
./verify-changes service quant-engine --base-ref HEAD
docker compose build notification-gateway quant-engine
```

No test requires production credentials, an external notification provider, or
a broker order.

## Migration and rollback

The change is additive:

- no database migration is required;
- existing provider endpoints remain available;
- completed analysis runs now persist the v1 recommendation envelope in the
  existing normalized-event JSON field;
- old run rows remain readable as historical JSON.

To roll back, restore the prior quant-engine and notification-gateway images.
That restores the previous analysis-event JSON shape and removes the validation
endpoint. No schema downgrade or data rewrite is needed. Consumers must not
reinterpret old rows as v1 envelopes unless their `schema_version` and shape
validate against the v1 contract.
