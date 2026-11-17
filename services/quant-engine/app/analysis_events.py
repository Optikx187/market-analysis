"""Producer-side schema for normalized analysis recommendation events."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.analysis_modules import (
    AnalysisEvidence,
    NormalizedAnalysisEvent,
)

EVENT_SCHEMA_VERSION = "1.0.0"
PRODUCER_VERSION = "1.0.0"
_SAFE_DEDUPLICATION_PART = re.compile(r"[^A-Za-z0-9._/-]+")


class EventProducer(BaseModel):
    service: str
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$")

    model_config = ConfigDict(extra="forbid")


class EventAudience(BaseModel):
    tenant_id: UUID
    user_keys: list[str]
    route_keys: list[str]

    model_config = ConfigDict(extra="forbid")


class EventSubject(BaseModel):
    subject_type: str
    subject_id: str
    symbol: str | None = None

    model_config = ConfigDict(extra="forbid")


class EventDeepLink(BaseModel):
    label: str
    url: str

    model_config = ConfigDict(extra="forbid")


class SafeEventSummary(BaseModel):
    title: str
    body: str
    disclaimer: str

    model_config = ConfigDict(extra="forbid")


class EventMetadata(BaseModel):
    correlation_id: str | None
    causation_id: str | None
    trace_id: str | None
    tags: dict[str, str]
    redacted_fields: list[str]

    model_config = ConfigDict(extra="forbid")


class RecommendationEventData(BaseModel):
    module_key: str
    module_version: str
    ticker: str
    asset_type: Literal["stock", "crypto"]
    horizon: str
    recommendation: dict[str, object]
    evidence: list[dict[str, object]]
    lineage: dict[str, object]

    model_config = ConfigDict(extra="forbid")


class RecommendationEventPayload(BaseModel):
    kind: Literal["recommendation"] = "recommendation"
    schema_version: Literal["1.0.0"] = EVENT_SCHEMA_VERSION
    data: RecommendationEventData

    model_config = ConfigDict(extra="forbid")


class RecommendationEventEnvelope(BaseModel):
    event_id: UUID
    event_type: str = Field(
        pattern=r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9-]*)+$",
        max_length=160,
    )
    schema_version: Literal["1.0.0"] = EVENT_SCHEMA_VERSION
    producer: EventProducer
    tenant_id: UUID
    audience: EventAudience
    severity: Literal["info", "notice", "warning", "critical"]
    occurred_at: datetime
    expires_at: datetime
    deduplication_key: str = Field(
        min_length=8,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9:._/-]+$",
    )
    subject: EventSubject
    deep_links: list[EventDeepLink]
    summary: SafeEventSummary
    payload: RecommendationEventPayload
    metadata: EventMetadata
    research_only: bool

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_time_range(self) -> "RecommendationEventEnvelope":
        if self.expires_at <= self.occurred_at:
            raise ValueError("expires_at must follow occurred_at")
        if self.audience.tenant_id != self.tenant_id:
            raise ValueError("audience tenant must match envelope tenant")
        return self


def _safe_deduplication_part(value: object) -> str:
    normalized = _SAFE_DEDUPLICATION_PART.sub("-", str(value).strip()).strip("-")
    return (normalized or "unknown")[:48]


def _deduplication_key(
    *,
    event_type: str,
    tenant_id: str,
    subject_type: str,
    subject_id: str,
    payload: dict[str, object],
) -> str:
    digest_input = {
        "event_type": event_type,
        "tenant_id": tenant_id,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "payload": payload,
    }
    canonical = json.dumps(
        digest_input,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()[:24]
    return ":".join(
        (
            "event",
            _safe_deduplication_part(event_type),
            _safe_deduplication_part(subject_type),
            _safe_deduplication_part(subject_id),
            digest,
        )
    )


def _safe_summary(event: NormalizedAnalysisEvent) -> SafeEventSummary:
    signal = event.recommendation.get("signal")
    if not isinstance(signal, dict):
        return SafeEventSummary(
            title=f"{event.ticker} analysis completed",
            body="No actionable signal was produced for this analysis run.",
            disclaimer="Research output only; not financial advice or an order.",
        )
    direction = str(signal.get("direction") or signal.get("status") or "HOLD")
    reason = str(signal.get("reason") or "Review the analysis evidence.")
    return SafeEventSummary(
        title=f"{event.ticker} {direction} analysis",
        body=reason[:2000],
        disclaimer="Research output only; not financial advice or an order.",
    )


def build_recommendation_event(
    *,
    event_id: str,
    tenant_id: str,
    user_key: str,
    event: NormalizedAnalysisEvent,
    evidence: list[AnalysisEvidence],
    lineage: dict[str, object],
) -> RecommendationEventEnvelope:
    payload = RecommendationEventPayload(
        data=RecommendationEventData(
            module_key=event.module_key,
            module_version=event.module_version,
            ticker=event.ticker,
            asset_type=event.asset_type,
            horizon=event.horizon,
            recommendation=event.recommendation,
            evidence=[
                item.model_dump(mode="json")
                for item in evidence
            ],
            lineage=lineage,
        )
    )
    payload_dict = payload.model_dump(mode="json")
    severity = (
        "notice"
        if isinstance(event.recommendation.get("signal"), dict)
        else "info"
    )
    return RecommendationEventEnvelope(
        event_id=event_id,
        event_type="analysis.recommendation.generated",
        producer=EventProducer(
            service="quant-engine",
            version=PRODUCER_VERSION,
        ),
        tenant_id=tenant_id,
        audience=EventAudience(
            tenant_id=tenant_id,
            user_keys=[user_key],
            route_keys=[f"analysis:{event.module_key}"],
        ),
        severity=severity,
        occurred_at=event.generated_at,
        expires_at=event.expires_at,
        deduplication_key=_deduplication_key(
            event_type="analysis.recommendation.generated",
            tenant_id=tenant_id,
            subject_type="instrument",
            subject_id=event.ticker,
            payload=payload_dict,
        ),
        subject=EventSubject(
            subject_type="instrument",
            subject_id=event.ticker,
            symbol=event.ticker,
        ),
        deep_links=[
            EventDeepLink(
                label="Open analysis",
                url=f"/analysis/runs/{event_id}",
            )
        ],
        summary=_safe_summary(event),
        payload=payload,
        metadata=EventMetadata(
            correlation_id=event_id,
            causation_id=None,
            trace_id=None,
            tags={"source": "analysis-run"},
            redacted_fields=[],
        ),
        research_only=event.research_only,
    )
