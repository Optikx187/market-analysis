from datetime import datetime, timedelta, timezone

from app.analysis_events import build_recommendation_event
from app.analysis_modules import AnalysisEvidence, NormalizedAnalysisEvent


def _analysis_event() -> NormalizedAnalysisEvent:
    generated_at = datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc)
    return NormalizedAnalysisEvent(
        event_type="analysis.recommendation.generated",
        module_key="future-volatility-preview",
        module_version="0.1.0",
        ticker="AAPL",
        asset_type="stock",
        horizon="short-term",
        generated_at=generated_at,
        expires_at=generated_at + timedelta(days=1),
        recommendation={
            "signal": {
                "direction": "HOLD",
                "reason": "Volatility is elevated.",
            }
        },
    )


def test_analysis_producer_builds_versioned_transport_envelope() -> None:
    envelope = build_recommendation_event(
        event_id="018f0c8a-7b9d-7cc2-9b34-000000000101",
        tenant_id="00000000-0000-7000-8000-000000000001",
        user_key="default",
        event=_analysis_event(),
        evidence=[
            AnalysisEvidence(
                evidence_type="volatility",
                summary="Elevated realized volatility",
                payload={"value": 0.42},
            )
        ],
        lineage={"source": "deterministic-fixture"},
    )
    payload = envelope.model_dump(mode="json")

    assert payload["schema_version"] == "1.0.0"
    assert payload["event_type"] == "analysis.recommendation.generated"
    assert payload["producer"] == {
        "service": "quant-engine",
        "version": "1.0.0",
    }
    assert payload["audience"]["route_keys"] == [
        "analysis:future-volatility-preview"
    ]
    assert payload["payload"]["kind"] == "recommendation"
    assert payload["payload"]["data"]["module_key"] == (
        "future-volatility-preview"
    )
    assert payload["research_only"] is True
    assert payload["deep_links"][0]["url"].startswith("/analysis/runs/")


def test_analysis_deduplication_ignores_transport_identity() -> None:
    common = {
        "tenant_id": "00000000-0000-7000-8000-000000000001",
        "user_key": "default",
        "event": _analysis_event(),
        "evidence": [],
        "lineage": {"source": "deterministic-fixture"},
    }

    first = build_recommendation_event(
        event_id="018f0c8a-7b9d-7cc2-9b34-000000000201",
        **common,
    )
    second = build_recommendation_event(
        event_id="018f0c8a-7b9d-7cc2-9b34-000000000202",
        **common,
    )
    changed_event = _analysis_event().model_copy(
        update={
            "recommendation": {
                "signal": {
                    "direction": "BUY",
                    "reason": "Trend confirmed.",
                }
            }
        }
    )
    changed = build_recommendation_event(
        event_id="018f0c8a-7b9d-7cc2-9b34-000000000203",
        **{**common, "event": changed_event},
    )

    assert first.deduplication_key == second.deduplication_key
    assert first.deduplication_key != changed.deduplication_key


def test_new_analysis_module_data_needs_no_provider_fields() -> None:
    envelope = build_recommendation_event(
        event_id="018f0c8a-7b9d-7cc2-9b34-000000000199",
        tenant_id="00000000-0000-7000-8000-000000000001",
        user_key="default",
        event=_analysis_event(),
        evidence=[],
        lineage={"source": "deterministic-fixture"},
    )
    serialized = envelope.model_dump_json()

    for provider_name in ("discord", "telegram", "slack", "twilio", "smtp"):
        assert provider_name not in serialized.lower()
