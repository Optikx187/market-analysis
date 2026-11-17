"""Transport-neutral event validation and quarantine contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft7Validator, FormatChecker

SUPPORTED_ENVELOPE_VERSIONS = frozenset({"1.0.0"})
_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "authtoken",
        "clientsecret",
        "credential",
        "credentials",
        "password",
        "privatekey",
        "secret",
        "token",
        "webhook",
        "webhookurl",
    }
)
_SAFE_DEDUPLICATION_PART = re.compile(r"[^A-Za-z0-9._/-]+")


def _schema_path() -> Path:
    override = os.getenv("EVENT_SCHEMA_PATH", "").strip()
    if override:
        return Path(override)
    service_directory = Path(__file__).resolve().parent
    candidates = [service_directory / "schemas/events/v1/envelope.schema.json"]
    if len(service_directory.parents) > 1:
        candidates.append(
            service_directory.parents[1]
            / "schemas/events/v1/envelope.schema.json"
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("Event envelope schema is unavailable")


def load_event_schema(path: Path | None = None) -> dict[str, Any]:
    schema_path = path or _schema_path()
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _safe_deduplication_part(value: object) -> str:
    normalized = _SAFE_DEDUPLICATION_PART.sub("-", str(value).strip()).strip("-")
    return (normalized or "unknown")[:48]


def build_deduplication_key(
    *,
    event_type: str,
    tenant_id: str,
    subject_type: str,
    subject_id: str,
    payload: Mapping[str, Any],
) -> str:
    """Build a stable key from semantic identity and canonical payload data."""
    digest_input = {
        "event_type": event_type,
        "tenant_id": tenant_id,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "payload": payload,
    }
    digest = hashlib.sha256(_canonical_json(digest_input)).hexdigest()[:24]
    return ":".join(
        (
            "event",
            _safe_deduplication_part(event_type),
            _safe_deduplication_part(subject_type),
            _safe_deduplication_part(subject_id),
            digest,
        )
    )


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    return any(
        normalized == sensitive or normalized.endswith(sensitive)
        for sensitive in _SENSITIVE_KEYS
    )


def _sensitive_values(value: Any) -> set[str]:
    found: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, child in node.items():
                if _is_sensitive_key(key):
                    collect(child)
                else:
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    def collect(node: Any) -> None:
        if isinstance(node, Mapping):
            for child in node.values():
                collect(child)
        elif isinstance(node, list):
            for child in node:
                collect(child)
        elif isinstance(node, (str, int, float)):
            rendered = str(node)
            if len(rendered) >= 4:
                found.add(rendered)

    visit(value)
    return found


def redact_sensitive_fields(value: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Redact secret-like fields and any duplicate secret values from an event."""
    secret_values = _sensitive_values(value)
    paths: list[str] = []

    def redact(node: Any, path: str) -> Any:
        if isinstance(node, Mapping):
            result: dict[str, Any] = {}
            for key, child in node.items():
                child_path = f"{path}.{key}" if path else str(key)
                if _is_sensitive_key(key):
                    result[str(key)] = _REDACTED
                    paths.append(child_path[:200])
                else:
                    result[str(key)] = redact(child, child_path)
            return result
        if isinstance(node, list):
            return [
                redact(child, f"{path}[{index}]")
                for index, child in enumerate(node)
            ]
        if isinstance(node, str):
            redacted = node
            for secret in sorted(secret_values, key=len, reverse=True):
                redacted = redacted.replace(secret, _REDACTED)
            return redacted
        return copy.deepcopy(node)

    redacted = redact(value, "")
    metadata = redacted.get("metadata")
    if isinstance(metadata, dict) and paths:
        existing = metadata.get("redacted_fields")
        existing_paths = [str(item) for item in existing] if isinstance(existing, list) else []
        metadata["redacted_fields"] = sorted(set(existing_paths + paths))
    return redacted, sorted(set(paths))


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("timestamp must be a string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))  # noqa: FURB162


def _semantic_error(event: Mapping[str, Any]) -> str | None:
    try:
        occurred_at = _parse_timestamp(event["occurred_at"])
        expires_at = _parse_timestamp(event["expires_at"])
    except (KeyError, TypeError, ValueError):
        return "invalid_event_timestamps"
    if expires_at <= occurred_at:
        return "event_expiry_must_follow_occurrence"

    audience = event.get("audience")
    if isinstance(audience, Mapping):
        audience_tenant = audience.get("tenant_id")
        if audience_tenant != event.get("tenant_id"):
            return "audience_tenant_mismatch"

    for link in event.get("deep_links", []):
        url = link.get("url") if isinstance(link, Mapping) else None
        if not isinstance(url, str):
            return "invalid_deep_link"
        parsed = urlsplit(url)
        relative_path = (
            not parsed.scheme
            and not parsed.netloc
            and url.startswith("/")
            and not url.startswith("//")
        )
        safe_absolute = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        )
        if not relative_path and not safe_absolute:
            return "unsafe_deep_link"
    return None


def _schema_error(validator: Draft7Validator, event: Mapping[str, Any]) -> str | None:
    errors = sorted(
        validator.iter_errors(event),
        key=lambda error: tuple(str(part) for part in error.path),
    )
    if not errors:
        return None
    error = errors[0]
    path = ".".join(str(part) for part in error.absolute_path) or "envelope"
    return f"schema_validation_failed:{path}:{error.validator}"


@dataclass(frozen=True)
class EventValidationResult:
    status: str
    event: dict[str, Any] | None
    quarantine: dict[str, Any] | None

    def public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "event": self.event,
            "quarantine": self.quarantine,
        }


class EventQuarantine:
    """Bounded quarantine with optional append-only JSONL persistence."""

    def __init__(self, path: Path | None = None, max_records: int = 500) -> None:
        self.path = path
        self.max_records = max_records
        self._records: list[dict[str, Any]] = []
        if path and path.is_file():
            recent_lines: deque[str] = deque(maxlen=max_records)
            with path.open(encoding="utf-8") as input_file:
                recent_lines.extend(input_file)
            for line in recent_lines:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    self._records.append(record)

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        return tuple(copy.deepcopy(self._records))

    def add(
        self,
        *,
        reason: str,
        event: Mapping[str, Any],
    ) -> dict[str, Any]:
        redacted_event, redacted_fields = redact_sensitive_fields(event)
        record = {
            "quarantined_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "event_id": str(event.get("event_id", "")) or None,
            "schema_version": str(event.get("schema_version", "")) or None,
            "event_sha256": hashlib.sha256(_canonical_json(event)).hexdigest(),
            "redacted_fields": redacted_fields,
            "event": redacted_event,
        }
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(record, sort_keys=True) + "\n")
                output.flush()
                os.fsync(output.fileno())
        self._records.append(record)
        if len(self._records) > self.max_records:
            self._records.pop(0)
        return copy.deepcopy(record)


class EventContractConsumer:
    """Validate supported envelopes and quarantine anything incompatible."""

    def __init__(
        self,
        *,
        schema_path: Path | None = None,
        quarantine: EventQuarantine | None = None,
    ) -> None:
        schema = load_event_schema(schema_path)
        Draft7Validator.check_schema(schema)
        self.validator = Draft7Validator(schema, format_checker=FormatChecker())
        self.quarantine = quarantine or EventQuarantine()

    def consume(self, event: Mapping[str, Any]) -> EventValidationResult:
        if not isinstance(event, Mapping):
            raise TypeError("event must be a mapping")
        version = event.get("schema_version")
        if version not in SUPPORTED_ENVELOPE_VERSIONS:
            return self._quarantine("unsupported_schema_version", event)

        error = _schema_error(self.validator, event)
        if error:
            return self._quarantine(error, event)
        semantic_error = _semantic_error(event)
        if semantic_error:
            return self._quarantine(semantic_error, event)

        redacted_event, _ = redact_sensitive_fields(event)
        return EventValidationResult(
            status="accepted",
            event=redacted_event,
            quarantine=None,
        )

    def _quarantine(
        self,
        reason: str,
        event: Mapping[str, Any],
    ) -> EventValidationResult:
        record = self.quarantine.add(reason=reason, event=event)
        return EventValidationResult(
            status="quarantined",
            event=None,
            quarantine=record,
        )
