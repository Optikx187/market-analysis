import copy
import json
from pathlib import Path

import main
from event_contracts import (
    EventContractConsumer,
    EventQuarantine,
    build_deduplication_key,
)
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas/events/v1/envelope.schema.json"
FIXTURE_DIRECTORY = ROOT / "schemas/events/v1/fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIRECTORY / name).read_text(encoding="utf-8"))


def _consumer(tmp_path: Path) -> EventContractConsumer:
    return EventContractConsumer(
        schema_path=SCHEMA_PATH,
        quarantine=EventQuarantine(tmp_path / "quarantine.jsonl"),
    )


def test_all_contract_fixtures_are_independently_accepted(tmp_path: Path) -> None:
    consumer = _consumer(tmp_path)

    results = [
        consumer.consume(_fixture(path.name))
        for path in sorted(FIXTURE_DIRECTORY.glob("*.json"))
    ]

    assert len(results) == 4
    assert all(result.status == "accepted" for result in results)
    assert not consumer.quarantine.records


def test_unknown_version_is_persisted_to_redacted_quarantine(
    tmp_path: Path,
) -> None:
    consumer = _consumer(tmp_path)
    event = _fixture("incident.json")
    event["schema_version"] = "2.0.0"
    event["payload"]["data"]["details"]["api_key"] = "never-write-this-secret"
    event["summary"]["body"] += " never-write-this-secret"

    result = consumer.consume(event)
    persisted = (tmp_path / "quarantine.jsonl").read_text(encoding="utf-8")

    assert result.status == "quarantined"
    assert result.quarantine["reason"] == "unsupported_schema_version"
    assert result.quarantine["event"]["payload"]["data"]["details"]["api_key"] == (
        "[REDACTED]"
    )
    assert "never-write-this-secret" not in persisted
    assert "[REDACTED]" in result.quarantine["event"]["summary"]["body"]
    assert result.quarantine["event_sha256"]
    reloaded = EventQuarantine(tmp_path / "quarantine.jsonl")
    assert len(reloaded.records) == 1
    assert reloaded.records[0]["reason"] == "unsupported_schema_version"


def test_incompatible_event_is_quarantined_instead_of_guessed(tmp_path: Path) -> None:
    consumer = _consumer(tmp_path)
    event = _fixture("operation.json")
    event["payload"]["kind"] = "recommendation"

    result = consumer.consume(event)

    assert result.status == "quarantined"
    assert result.quarantine["reason"].startswith("schema_validation_failed:")
    assert result.event is None


def test_sensitive_fields_are_redacted_from_accepted_events(tmp_path: Path) -> None:
    consumer = _consumer(tmp_path)
    event = _fixture("recommendation.json")
    evidence = event["payload"]["data"]["evidence"][0]
    evidence["api_token"] = "sandbox-sensitive-value"
    event["summary"]["body"] += " sandbox-sensitive-value"

    result = consumer.consume(event)

    assert result.status == "accepted"
    assert result.event["payload"]["data"]["evidence"][0]["api_token"] == "[REDACTED]"
    assert "sandbox-sensitive-value" not in result.event["summary"]["body"]
    assert "payload.data.evidence[0].api_token" in result.event["metadata"][
        "redacted_fields"
    ]


def test_unsafe_deep_links_and_invalid_expiry_are_quarantined(
    tmp_path: Path,
) -> None:
    unsafe_link = _fixture("recommendation.json")
    unsafe_link["deep_links"][0]["url"] = "javascript:alert(1)"
    bad_expiry = _fixture("recommendation.json")
    bad_expiry["expires_at"] = bad_expiry["occurred_at"]

    link_result = _consumer(tmp_path / "link").consume(unsafe_link)
    expiry_result = _consumer(tmp_path / "expiry").consume(bad_expiry)

    assert link_result.quarantine["reason"] == "unsafe_deep_link"
    assert expiry_result.quarantine["reason"] == "event_expiry_must_follow_occurrence"


def test_deduplication_key_is_stable_and_payload_sensitive() -> None:
    event = _fixture("recommendation.json")
    fields = {
        "event_type": event["event_type"],
        "tenant_id": event["tenant_id"],
        "subject_type": event["subject"]["subject_type"],
        "subject_id": event["subject"]["subject_id"],
        "payload": event["payload"],
    }

    first = build_deduplication_key(**fields)
    second = build_deduplication_key(**copy.deepcopy(fields))
    changed_fields = copy.deepcopy(fields)
    changed_fields["payload"]["data"]["recommendation"]["direction"] = "BUY"
    changed = build_deduplication_key(**changed_fields)

    assert first == second
    assert first != changed
    assert len(first) <= 200


def test_provider_adapter_only_depends_on_validated_safe_summary(
    tmp_path: Path,
) -> None:
    class SampleProviderAdapter:
        def render(self, event: dict) -> str:
            summary = event["summary"]
            return f"{summary['title']}\n{summary['body']}"

    result = _consumer(tmp_path).consume(_fixture("recommendation.json"))

    rendered = SampleProviderAdapter().render(result.event)

    assert "AAPL volatility preview" in rendered
    assert "future-volatility-preview" not in SampleProviderAdapter.__module__


def test_event_validation_endpoint_accepts_or_quarantines_without_delivery(
    monkeypatch,
    tmp_path: Path,
) -> None:
    consumer = _consumer(tmp_path)
    monkeypatch.setattr(main, "_event_consumer", consumer)
    client = TestClient(main.app)
    valid = _fixture("recommendation.json")
    unknown = _fixture("recommendation.json")
    unknown["schema_version"] = "99.0.0"
    deliveries_before = list(main._fake_deliveries)

    accepted = client.post("/api/events/validate", json=valid)
    quarantined = client.post("/api/events/validate", json=unknown)

    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    assert quarantined.status_code == 202
    assert quarantined.json()["status"] == "quarantined"
    assert main._fake_deliveries == deliveries_before
