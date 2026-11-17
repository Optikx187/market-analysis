from __future__ import annotations

import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from app import main
from app.analysis_modules import (
    AnalysisContext,
    AnalysisEvidence,
    AnalysisModuleDefinition,
    AnalysisModuleResult,
    AnalysisRunRequest,
    CurrentSignalAnalysisModule,
    NormalizedAnalysisEvent,
    default_analysis_registry,
)
from app.analysis_store import AnalysisStore
from app.config import settings
from app.main import DataQualityResponse

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def migrated_store(tmp_path: Path) -> AnalysisStore:
    database = tmp_path / "analysis.db"
    original = settings.BACKTEST_DATABASE_PATH
    settings.BACKTEST_DATABASE_PATH = str(database)
    try:
        command.upgrade(
            Config(str(SERVICE_ROOT / "alembic.ini")),
            "head",
        )
    finally:
        settings.BACKTEST_DATABASE_PATH = original
    return AnalysisStore(str(database), user_key="analysis-tester")


def frame() -> pd.DataFrame:
    values = pd.Series(range(360), dtype=float)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2025-01-01",
                periods=360,
                freq="D",
                tz="UTC",
            ),
            "open": 100 + values,
            "high": 102 + values,
            "low": 99 + values,
            "close": 101 + values,
            "volume": 1_000 + values,
        }
    )


def quality() -> DataQualityResponse:
    return DataQualityResponse(
        ticker="AAPL",
        asset_type="stock",
        interval="1d",
        status="healthy",
        is_eligible=True,
        candle_count=360,
        latest_timestamp="2025-12-26T00:00:00+00:00",
        age_hours=0.0,
        stale=False,
        duplicate_timestamps=0,
        missing_periods=0,
        invalid_timestamps=0,
        invalid_ohlc=0,
        anomaly_count=0,
        issues=[],
    )


def test_registry_accepts_future_module_without_mutating_current_module() -> None:
    class FutureModule:
        definition = AnalysisModuleDefinition(
            module_key="future-volatility-preview",
            module_version="0.1.0-preview",
            display_name="Future Volatility Preview",
            description="Registration-only compatibility fixture",
            enabled_by_default=False,
            supported_asset_types=["stock"],
            supported_horizons=["long-term"],
            requirements=[],
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            result_ttl_seconds=3600,
            normalized_event_type="analysis.volatility.v1",
        )

        def run(self, context: AnalysisContext) -> AnalysisModuleResult:
            raise AssertionError("Registration must not execute the module")

    registry = default_analysis_registry()
    current_before = registry.get("current-signal-analysis")
    registry.register(FutureModule())

    assert registry.get("current-signal-analysis") is current_before
    assert registry.get("future-volatility-preview").definition.enabled_by_default is False
    assert [definition.module_key for definition in registry.definitions()] == [
        "current-signal-analysis",
        "future-volatility-preview",
    ]


def test_registry_prefers_stable_and_orders_numeric_prereleases() -> None:
    class VersionedModule:
        def __init__(self, version: str) -> None:
            self.definition = AnalysisModuleDefinition(
                module_key="versioned-preview",
                module_version=version,
                display_name=version,
                description="Semantic version ordering fixture",
                supported_asset_types=["stock"],
                supported_horizons=["short-term"],
                requirements=[],
                input_schema={},
                output_schema={},
                result_ttl_seconds=60,
                normalized_event_type="analysis.preview.v1",
            )

        def run(self, context: AnalysisContext) -> AnalysisModuleResult:
            raise AssertionError("Version ordering must not execute modules")

    registry = default_analysis_registry()
    for version in ("1.0.0-rc.10", "1.0.0", "1.0.0-rc.2"):
        registry.register(VersionedModule(version))

    assert registry.get("versioned-preview").definition.module_version == "1.0.0"
    assert [
        definition.module_version
        for definition in registry.definitions()
        if definition.module_key == "versioned-preview"
    ] == ["1.0.0-rc.2", "1.0.0-rc.10", "1.0.0"]


def test_module_definition_rejects_invalid_keys_and_versions() -> None:
    with pytest.raises(ValidationError, match="kebab-case"):
        AnalysisModuleDefinition(
            module_key="Invalid Key",
            module_version="1.0.0",
            display_name="Invalid",
            description="Invalid",
            supported_asset_types=["stock"],
            supported_horizons=["short-term"],
            requirements=[],
            input_schema={},
            output_schema={},
            result_ttl_seconds=60,
            normalized_event_type="analysis.invalid.v1",
        )
    with pytest.raises(ValidationError, match="semantic version"):
        AnalysisModuleDefinition(
            module_key="valid-key",
            module_version="v1",
            display_name="Invalid",
            description="Invalid",
            supported_asset_types=["stock"],
            supported_horizons=["short-term"],
            requirements=[],
            input_schema={},
            output_schema={},
            result_ttl_seconds=60,
            normalized_event_type="analysis.invalid.v1",
        )


def test_current_module_matches_existing_analyze_output(
    monkeypatch,
) -> None:
    frames = {"1d": frame(), "4h": frame(), "1h": frame()}
    monkeypatch.setattr(
        main,
        "_require_eligible_data",
        lambda _ticker: asyncio.sleep(0, result=quality()),
    )
    monkeypatch.setattr(
        main,
        "_fetch_timeframe_frames",
        lambda _ticker, _timeframes=None: asyncio.sleep(
            0,
            result=frames,
        ),
    )
    request = AnalysisRunRequest(
        ticker="AAPL",
        asset_type="stock",
        horizon="short-term",
        available_capital=10_000,
    )
    existing = asyncio.run(
        main.analyze(
            main.AnalyzeRequest(
                ticker=request.ticker,
                asset_type=request.asset_type,
                available_capital=request.available_capital,
                timeframes=request.timeframes,
            )
        )
    )
    wrapped = CurrentSignalAnalysisModule().run(
        AnalysisContext(
            request=request,
            frames=frames,
            quality=quality().model_dump(mode="json"),
            generated_at=datetime.now(timezone.utc),
        )
    )

    assert existing is not None
    assert wrapped.output["signal"] == existing.model_dump()
    assert wrapped.normalized_event.research_only is True
    assert "notification" not in inspect.getsource(
        CurrentSignalAnalysisModule
    ).lower()


def test_store_persists_immutable_definition_selection_and_result(
    tmp_path: Path,
) -> None:
    store = migrated_store(tmp_path)
    registry = default_analysis_registry()
    store.sync_registry(registry)
    definition = registry.get("current-signal-analysis").definition
    modules = store.list_modules()

    assert len(modules) == 1
    assert modules[0]["module_key"] == definition.module_key
    assert modules[0]["enabled"] is True
    store.set_selection(
        definition.module_key,
        definition.module_version,
        enabled=False,
        config={"cadence_minutes": 30},
    )
    restarted = AnalysisStore(
        store.database_url,
        user_key="analysis-tester",
    )
    assert restarted.get_selection(definition.module_key) == {
        "module_key": definition.module_key,
        "module_version": definition.module_version,
        "enabled": False,
        "config": {"cadence_minutes": 30},
        "updated_at": restarted.get_selection(definition.module_key)[
            "updated_at"
        ],
    }

    request = AnalysisRunRequest(
        ticker="AAPL",
        asset_type="stock",
        horizon="short-term",
    )
    generated_at = datetime.now(timezone.utc)
    result = AnalysisModuleResult(
        output={"signal": None},
        evidence=[
            AnalysisEvidence(
                evidence_type="data-quality",
                summary="eligible",
            )
        ],
        lineage={"frames": []},
        normalized_event=NormalizedAnalysisEvent(
            event_type=definition.normalized_event_type,
            module_key=definition.module_key,
            module_version=definition.module_version,
            ticker="AAPL",
            asset_type="stock",
            horizon="short-term",
            generated_at=generated_at,
            expires_at=generated_at + timedelta(hours=1),
            recommendation={"signal": None},
        ),
    )
    persisted = store.save_run(
        definition,
        request,
        result,
        started_at=generated_at,
        completed_at=generated_at,
    )

    assert persisted["module_key"] == definition.module_key
    assert persisted["result"] == {"signal": None}
    assert persisted["normalized_event"]["research_only"] is True
    with pytest.raises(DatabaseError, match="immutable"):
        with store.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE analysis_module_runs "
                    "SET ticker='MSFT' WHERE run_id=:run_id"
                ),
                {"run_id": persisted["run_id"]},
            )

    changed = definition.model_copy(
        update={"description": "Silently changed"}
    )
    with pytest.raises(ValueError, match="immutable"):
        store.register_definition(changed)
    with pytest.raises(KeyError):
        store.get_run("not-a-uuid")
    with pytest.raises(KeyError):
        store.get_run("00000000-0000-0000-0000-000000000000")


def test_concurrent_definition_registration_is_idempotent(
    tmp_path: Path,
) -> None:
    store = migrated_store(tmp_path)
    definition = default_analysis_registry().get(
        "current-signal-analysis"
    ).definition
    stores = [
        AnalysisStore(store.database_url, user_key=f"worker-{index}")
        for index in range(2)
    ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(
            executor.map(
                lambda candidate: candidate.register_definition(definition),
                stores,
            )
        )

    with store.engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM analysis_module_definitions "
                "WHERE module_key=:module_key AND module_version=:version"
            ),
            {
                "module_key": definition.module_key,
                "version": definition.module_version,
            },
        ).scalar_one() == 1


def test_module_api_persists_valid_result_and_fails_closed_on_stale_data(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = migrated_store(tmp_path)
    registry = default_analysis_registry()
    store.sync_registry(registry)
    monkeypatch.setattr(main, "_analysis_store", store)
    monkeypatch.setattr(main, "_analysis_registry", registry)
    frames = {"1d": frame(), "4h": frame(), "1h": frame()}
    frames["1d"]["timestamp"] = pd.date_range(
        end=datetime.now(timezone.utc) - timedelta(days=3),
        periods=len(frames["1d"]),
        freq="D",
    )
    monkeypatch.setattr(
        main,
        "_require_eligible_data",
        AsyncMock(return_value=quality()),
    )
    monkeypatch.setattr(
        main,
        "_fetch_timeframe_frames",
        AsyncMock(return_value=frames),
    )
    request = AnalysisRunRequest(
        ticker="aapl",
        asset_type="stock",
        horizon="short-term",
    )

    persisted = asyncio.run(
        main.run_analysis_module(
            "current-signal-analysis",
            request,
        )
    )

    assert persisted["ticker"] == "AAPL"
    assert persisted["status"] == "completed"
    assert persisted["normalized_event"]["research_only"] is True
    assert (
        persisted["normalized_event"]["payload"]["data"]["module_version"]
        == "1.0.0"
    )
    assert (
        registry.get("current-signal-analysis")
        .definition.requirements[0]
        .max_age_seconds
        == 432_000
    )

    with pytest.raises(main.HTTPException) as unknown:
        asyncio.run(main.run_analysis_module("unknown-module", request))
    assert unknown.value.status_code == 404

    monkeypatch.setattr(
        main,
        "_require_eligible_data",
        AsyncMock(
            side_effect=main.HTTPException(
                422,
                "Data quality rejected for STALE",
            )
        ),
    )
    with pytest.raises(main.HTTPException) as rejected:
        asyncio.run(
            main.run_analysis_module(
                "current-signal-analysis",
                request.model_copy(update={"ticker": "STALE"}),
            )
        )
    assert rejected.value.status_code == 422
    with store.engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM analysis_module_runs")
        ).scalar_one() == 1


def test_module_api_uses_selected_version_and_enforces_freshness(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class SelectedVersionModule:
        def __init__(self, version: str, *, requires_data: bool) -> None:
            self.definition = AnalysisModuleDefinition(
                module_key="selected-version",
                module_version=version,
                display_name=f"Selected Version {version}",
                description="Selected-version execution fixture",
                supported_asset_types=["stock"],
                supported_horizons=["short-term"],
                requirements=(
                    [
                        {
                            "interval": "1d",
                            "minimum_candles": 1,
                            "max_age_seconds": 60,
                        }
                    ]
                    if requires_data
                    else []
                ),
                input_schema={},
                output_schema={},
                result_ttl_seconds=60,
                normalized_event_type="analysis.selected.v1",
            )

        def run(self, context: AnalysisContext) -> AnalysisModuleResult:
            generated_at = context.generated_at
            output = {"version": self.definition.module_version}
            return AnalysisModuleResult(
                output=output,
                evidence=[],
                lineage={},
                normalized_event=NormalizedAnalysisEvent(
                    event_type=self.definition.normalized_event_type,
                    module_key=self.definition.module_key,
                    module_version=self.definition.module_version,
                    ticker=context.request.ticker,
                    asset_type=context.request.asset_type,
                        horizon=context.request.horizon,
                        generated_at=generated_at,
                        expires_at=generated_at + timedelta(minutes=1),
                        recommendation=output,
                ),
            )

    store = migrated_store(tmp_path)
    registry = default_analysis_registry()
    registry.register(SelectedVersionModule("1.0.0", requires_data=True))
    registry.register(SelectedVersionModule("2.0.0", requires_data=False))
    store.sync_registry(registry)
    store.set_selection(
        "selected-version",
        "1.0.0",
        enabled=True,
    )
    monkeypatch.setattr(main, "_analysis_store", store)
    monkeypatch.setattr(main, "_analysis_registry", registry)
    monkeypatch.setattr(
        main,
        "_require_eligible_data",
        AsyncMock(return_value=quality()),
    )
    monkeypatch.setattr(
        main,
        "_fetch_timeframe_frames",
        AsyncMock(
            return_value={
                "1d": pd.DataFrame(
                    {
                        "timestamp": [
                            datetime(2020, 1, 1, tzinfo=timezone.utc)
                        ]
                    }
                )
            }
        ),
    )
    request = AnalysisRunRequest(
        ticker="AAPL",
        asset_type="stock",
        horizon="short-term",
    )

    with pytest.raises(main.HTTPException) as stale:
        asyncio.run(main.run_analysis_module("selected-version", request))

    assert stale.value.status_code == 422
    assert "newer than 60 seconds" in stale.value.detail
    store.set_selection(
        "selected-version",
        "2.0.0",
        enabled=True,
    )
    persisted = asyncio.run(
        main.run_analysis_module("selected-version", request)
    )
    assert persisted["module_version"] == "2.0.0"
    assert persisted["result"] == {"version": "2.0.0"}
