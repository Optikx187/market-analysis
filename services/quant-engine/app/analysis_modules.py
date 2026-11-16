"""Versioned, notification-independent analysis module contracts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from app.regimes import (
    classify_regime,
    regime_controls,
    timeframe_confluence,
)
from app.signals import evaluate_signals

MODULE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
MODULE_VERSION_PATTERN = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$"
)


class DataRequirement(BaseModel):
    interval: str
    minimum_candles: int = Field(ge=1)
    max_age_seconds: int = Field(ge=1)

    model_config = ConfigDict(frozen=True)


class AnalysisModuleDefinition(BaseModel):
    module_key: str
    module_version: str
    display_name: str
    description: str
    enabled_by_default: bool = True
    supported_asset_types: list[str]
    supported_horizons: list[str]
    requirements: list[DataRequirement]
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    result_ttl_seconds: int = Field(ge=60)
    normalized_event_type: str

    model_config = ConfigDict(frozen=True)

    @classmethod
    def validate_module_key(cls, value: str) -> str:
        if not MODULE_KEY_PATTERN.fullmatch(value):
            raise ValueError("module_key must be lowercase kebab-case")
        return value

    def model_post_init(self, __context: object, /) -> None:
        self.validate_module_key(self.module_key)
        if not MODULE_VERSION_PATTERN.fullmatch(self.module_version):
            raise ValueError("module_version must be semantic version text")
        if not self.supported_asset_types:
            raise ValueError("supported_asset_types must not be empty")
        if not self.supported_horizons:
            raise ValueError("supported_horizons must not be empty")


class AnalysisRunRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=30)
    asset_type: str
    horizon: str
    available_capital: float = Field(default=10_000.0, gt=0)
    timeframes: list[str] = Field(
        default_factory=lambda: ["1d", "4h", "1h"]
    )


class AnalysisEvidence(BaseModel):
    evidence_type: str
    summary: str
    payload: dict[str, object] = Field(default_factory=dict)


class NormalizedAnalysisEvent(BaseModel):
    event_type: str
    schema_version: str = "1.0.0"
    module_key: str
    module_version: str
    ticker: str
    asset_type: str
    horizon: str
    generated_at: datetime
    expires_at: datetime
    recommendation: dict[str, object]
    research_only: bool = True


class AnalysisModuleResult(BaseModel):
    output: dict[str, object]
    evidence: list[AnalysisEvidence]
    lineage: dict[str, object]
    normalized_event: NormalizedAnalysisEvent


class CurrentSignalPayload(BaseModel):
    ticker: str
    direction: str | None
    status: str
    trigger_price: float
    stop_loss: float
    target_price: float
    reason: str
    risk_reward: float
    atr_value: float
    rsi_value: float
    asset_type: str
    suppressed: bool
    kelly_pct: float
    optimal_size_usd: float
    volatility_scalar: float
    market_regime: dict[str, object]
    timeframe_agreement: dict[str, object]
    regime_controls: dict[str, object]


class CurrentSignalOutput(BaseModel):
    signal: CurrentSignalPayload | None


@dataclass(frozen=True)
class AnalysisContext:
    request: AnalysisRunRequest
    frames: dict[str, pd.DataFrame]
    quality: dict[str, object]
    generated_at: datetime


class AnalysisModule(Protocol):
    definition: AnalysisModuleDefinition

    def run(self, context: AnalysisContext) -> AnalysisModuleResult:
        ...


class AnalysisModuleRegistry:
    def __init__(self) -> None:
        self._modules: dict[tuple[str, str], AnalysisModule] = {}
        self._current_versions: dict[str, str] = {}

    def register(self, module: AnalysisModule) -> None:
        definition = module.definition
        key = (definition.module_key, definition.module_version)
        if key in self._modules:
            raise ValueError(
                f"Analysis module {definition.module_key}@"
                f"{definition.module_version} is already registered"
            )
        self._modules[key] = module
        current = self._current_versions.get(definition.module_key)
        if current is None or _version_tuple(
            definition.module_version
        ) > _version_tuple(current):
            self._current_versions[definition.module_key] = (
                definition.module_version
            )

    def get(
        self,
        module_key: str,
        module_version: str | None = None,
    ) -> AnalysisModule:
        version = module_version or self._current_versions.get(module_key)
        if version is None:
            raise KeyError(module_key)
        try:
            return self._modules[(module_key, version)]
        except KeyError as error:
            raise KeyError(f"{module_key}@{version}") from error

    def definitions(self) -> list[AnalysisModuleDefinition]:
        return sorted(
            (module.definition for module in self._modules.values()),
            key=lambda definition: (
                definition.module_key,
                _version_tuple(definition.module_version),
            ),
        )


class CurrentSignalAnalysisModule:
    definition = AnalysisModuleDefinition(
        module_key="current-signal-analysis",
        module_version="1.0.0",
        display_name="Current Signal Analysis",
        description=(
            "Existing EMA/RSI/ATR signal and regime controls, wrapped "
            "without changing financial math."
        ),
        supported_asset_types=["stock", "crypto"],
        supported_horizons=["short-term"],
        requirements=[
            DataRequirement(
                interval="1d",
                minimum_candles=201,
                max_age_seconds=432_000,
            )
        ],
        input_schema=AnalysisRunRequest.model_json_schema(),
        output_schema=CurrentSignalOutput.model_json_schema(),
        result_ttl_seconds=86_400,
        normalized_event_type="analysis.signal.v1",
    )

    def run(self, context: AnalysisContext) -> AnalysisModuleResult:
        daily = context.frames.get("1d", pd.DataFrame())
        if len(daily) < 201:
            raise ValueError("current signal analysis requires 201 daily candles")
        result = evaluate_signals(
            daily,
            context.request.available_capital,
        )
        signal: CurrentSignalPayload | None = None
        evidence = [
            AnalysisEvidence(
                evidence_type="data-quality",
                summary="Required market data passed quality gates",
                payload=context.quality,
            )
        ]
        if result is not None:
            snapshot = classify_regime(
                daily,
                context.request.asset_type,
            )
            confluence = timeframe_confluence(
                context.frames,
                str(result.direction) if result.direction else "NONE",
                context.request.asset_type,
            )
            controls = regime_controls(
                snapshot,
                str(result.direction) if result.direction else "NONE",
                confluence,
            )
            regime = snapshot.to_dict()
            reason = (
                f"{result.reason}. Regime: {regime['label']}. "
                f"Timeframe agreement: {float(confluence['score']):.1f}%"
            )
            signal = CurrentSignalPayload(
                ticker=context.request.ticker,
                direction=result.direction,
                status=result.status,
                trigger_price=result.trigger_price,
                stop_loss=result.stop_loss,
                target_price=result.target_price,
                reason=reason,
                risk_reward=result.risk_reward,
                atr_value=result.atr_value,
                rsi_value=result.rsi_value,
                asset_type=context.request.asset_type,
                suppressed=(
                    result.suppressed or not bool(controls["allowed"])
                ),
                kelly_pct=result.kelly_pct,
                optimal_size_usd=(
                    result.optimal_size_usd
                    * float(controls["size_multiplier"])
                ),
                volatility_scalar=result.volatility_scalar,
                market_regime=regime,
                timeframe_agreement=confluence,
                regime_controls=controls,
            )
            evidence.append(
                AnalysisEvidence(
                    evidence_type="regime",
                    summary=str(regime["label"]),
                    payload={
                        "regime": regime,
                        "timeframe_agreement": confluence,
                        "controls": controls,
                    },
                )
            )
        expires_at = context.generated_at + timedelta(
            seconds=self.definition.result_ttl_seconds
        )
        output = CurrentSignalOutput(signal=signal).model_dump(mode="json")
        return AnalysisModuleResult(
            output=output,
            evidence=evidence,
            lineage={
                "source_service": "data-ingestion",
                "frames": [
                    _frame_lineage(interval, frame)
                    for interval, frame in sorted(context.frames.items())
                ],
            },
            normalized_event=NormalizedAnalysisEvent(
                event_type=self.definition.normalized_event_type,
                module_key=self.definition.module_key,
                module_version=self.definition.module_version,
                ticker=context.request.ticker,
                asset_type=context.request.asset_type,
                horizon=context.request.horizon,
                generated_at=context.generated_at,
                expires_at=expires_at,
                recommendation=output,
            ),
        )


def default_analysis_registry() -> AnalysisModuleRegistry:
    registry = AnalysisModuleRegistry()
    registry.register(CurrentSignalAnalysisModule())
    return registry


def _version_tuple(
    value: str,
) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]:
    core, _, suffix = value.partition("-")
    major, minor, patch = (int(part) for part in core.split("."))
    prerelease = tuple(
        (0, int(identifier))
        if identifier.isdigit()
        else (1, identifier)
        for identifier in suffix.split(".")
        if identifier
    )
    return major, minor, patch, int(not suffix), prerelease


def _frame_lineage(
    interval: str,
    frame: pd.DataFrame,
) -> dict[str, object]:
    serialized = frame.to_json(
        orient="split",
        date_format="iso",
        date_unit="us",
        double_precision=15,
    ).encode()
    timestamps = (
        pd.to_datetime(frame["timestamp"], utc=True)
        if "timestamp" in frame.columns and not frame.empty
        else pd.Series(dtype="datetime64[ns, UTC]")
    )
    return {
        "interval": interval,
        "row_count": len(frame),
        "first_observed_at": (
            timestamps.min().isoformat() if not timestamps.empty else None
        ),
        "last_observed_at": (
            timestamps.max().isoformat() if not timestamps.empty else None
        ),
        "content_sha256": hashlib.sha256(serialized).hexdigest(),
    }
