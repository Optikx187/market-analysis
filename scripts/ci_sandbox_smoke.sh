#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACT_DIR="${1:-$ROOT_DIR/artifacts/verification}"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-market-analysis-ci-$$}"
ENV_FILE="$ROOT_DIR/.env"
ENV_BACKUP=""
COMPOSE=(
  docker compose
  --project-name "$PROJECT_NAME"
  --file "$ROOT_DIR/docker-compose.yml"
  --file "$ROOT_DIR/deploy/compose.sandbox.yml"
)

mkdir -p "$ARTIFACT_DIR"
ARTIFACT_DIR="$(cd -- "$ARTIFACT_DIR" && pwd)"

cleanup() {
  local status=$?
  "${COMPOSE[@]}" logs --no-color >"$ARTIFACT_DIR/compose.log" 2>&1 || true
  "${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  if [[ -n "$ENV_BACKUP" ]]; then
    mv "$ENV_BACKUP" "$ENV_FILE"
  else
    rm -f "$ENV_FILE"
  fi
  exit "$status"
}
trap cleanup EXIT

if [[ -f "$ENV_FILE" ]]; then
  ENV_BACKUP="$(mktemp "$ROOT_DIR/.env.ci-backup.XXXXXX")"
  cp "$ENV_FILE" "$ENV_BACKUP"
fi
cp "$ROOT_DIR/.env.example" "$ENV_FILE"
cat >>"$ENV_FILE" <<'EOF'
CREDENTIAL_ENCRYPTION_KEYS=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
INTERNAL_SERVICE_TOKEN=verification-internal-token
SETTINGS_OPERATOR_TOKEN=verification-settings-token
JWT_SECRET=verification-only-jwt-secret-at-least-32-characters
LIVE_OPERATOR_TOKEN=verification-live-token
SANDBOX_AUTH_ENABLED=false
EOF
chmod 600 "$ENV_FILE"

"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" config --format json >"$ARTIFACT_DIR/compose.json"
"${COMPOSE[@]}" up --detach --build --wait --wait-timeout 180

"${COMPOSE[@]}" exec -T data-ingestion python - <<'PY'
import httpx

health = httpx.get("http://localhost:8000/health", timeout=10)
health.raise_for_status()
assert health.json()["service"] == "data-ingestion"

quote = httpx.get(
    "http://localhost:8000/api/quotes/BTC",
    params={"asset_type": "crypto"},
    timeout=10,
)
quote.raise_for_status()
payload = quote.json()
assert payload["ticker"] == "BTC"
assert payload["asset_type"] == "crypto"
assert payload["price"] is not None
PY

"${COMPOSE[@]}" exec -T notification-gateway python - <<'PY'
import httpx

health = httpx.get("http://localhost:8003/health", timeout=10)
health.raise_for_status()
assert health.json()["notification_mode"] == "fake"

response = httpx.post(
    "http://localhost:8003/api/notify/test",
    json={"message": "CI sandbox smoke"},
    timeout=10,
)
response.raise_for_status()

deliveries = httpx.get(
    "http://localhost:8003/api/notify/sandbox-deliveries",
    timeout=10,
)
deliveries.raise_for_status()
assert deliveries.json()["deliveries"]
PY

"${COMPOSE[@]}" exec -T portfolio-engine python - <<'PY'
import httpx

health = httpx.get("http://localhost:8002/health", timeout=10)
health.raise_for_status()
payload = health.json()
assert payload["deployment_profile"] == "sandbox"
assert payload["live_trading_enabled"] is False
PY

python - "$ARTIFACT_DIR/sandbox-smoke.json" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(
    json.dumps(
        {
            "schema_version": 1,
            "success": True,
            "checks": [
                "compose-config",
                "service-readiness",
                "fake-market-quote",
                "fake-notification-delivery",
                "live-trading-disabled",
            ],
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY
