"""Durable storage for versioned analysis modules and research results."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

from app.analysis_modules import (
    AnalysisModuleDefinition,
    AnalysisModuleRegistry,
    AnalysisModuleResult,
    AnalysisRunRequest,
)
from app.backtest_store import uuid7
from app.config import settings
from app.migration_state import validate_schema


def _database_url(value: str) -> str:
    if "://" in value:
        if value.startswith("postgresql://"):
            return value.replace(
                "postgresql://",
                "postgresql+psycopg://",
                1,
            )
        return value
    return f"sqlite:///{Path(value).resolve()}"


def _json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    )


def _decoded(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


class AnalysisStore:
    def __init__(
        self,
        database_url_or_path: str | None = None,
        *,
        tenant_id: str | None = None,
        user_key: str | None = None,
    ) -> None:
        self.database_url = _database_url(
            database_url_or_path or settings.research_database_url
        )
        self.tenant_id = tenant_id or settings.RESEARCH_TENANT_ID
        self.user_key = (user_key or settings.RESEARCH_USER_KEY).strip()
        self.engine: Engine = create_engine(self.database_url)
        self.postgresql = self.engine.dialect.name == "postgresql"
        if self.postgresql:
            event.listen(self.engine, "connect", self._set_postgresql_utc)
        prefix = "research." if self.postgresql else ""
        self.definition_table = f"{prefix}analysis_module_definitions"
        self.selection_table = f"{prefix}analysis_module_selections"
        self.run_table = f"{prefix}analysis_module_runs"
        validate_schema(self.database_url)

    @staticmethod
    def _set_postgresql_utc(
        dbapi_connection: object,
        _connection_record: object,
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("SET TIME ZONE 'UTC'")
        cursor.close()

    def _tenant(self) -> str:
        return "CAST(:tenant_id AS UUID)" if self.postgresql else ":tenant_id"

    def _time(self, name: str) -> str:
        return (
            f"CAST(:{name} AS TIMESTAMPTZ)"
            if self.postgresql
            else f":{name}"
        )

    def _json_cast(self, name: str) -> str:
        return f"CAST(:{name} AS JSONB)" if self.postgresql else f":{name}"

    def sync_registry(self, registry: AnalysisModuleRegistry) -> None:
        definitions = registry.definitions()
        for definition in definitions:
            self.register_definition(definition)
        for module_key in dict.fromkeys(
            definition.module_key for definition in definitions
        ):
            self.ensure_selection(registry.get(module_key).definition)

    def register_definition(
        self,
        definition: AnalysisModuleDefinition,
        *,
        now: datetime | None = None,
    ) -> None:
        current = now or datetime.now(timezone.utc)
        payload = _json(definition.model_dump(mode="json"))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        values = {
            "module_key": definition.module_key,
            "module_version": definition.module_version,
            "definition_json": payload,
            "definition_sha256": digest,
            "created_at": (
                current if self.postgresql else current.isoformat()
            ),
        }
        with self.engine.begin() as connection:
            existing = connection.execute(
                text(
                    f"""
                    SELECT definition_sha256
                    FROM {self.definition_table}
                    WHERE module_key = :module_key
                      AND module_version = :module_version
                    """
                ),
                values,
            ).scalar_one_or_none()
            if existing is not None:
                if str(existing) != digest:
                    raise ValueError(
                        "Registered analysis module version is immutable"
                    )
                return
            connection.execute(
                text(
                    f"""
                    INSERT INTO {self.definition_table} (
                        module_key, module_version, definition_json,
                        definition_sha256, created_at
                    ) VALUES (
                        :module_key, :module_version,
                        {self._json_cast("definition_json")},
                        :definition_sha256, {self._time("created_at")}
                    )
                    """
                ),
                values,
            )

    def ensure_selection(
        self,
        definition: AnalysisModuleDefinition,
        *,
        now: datetime | None = None,
    ) -> None:
        current = now or datetime.now(timezone.utc)
        values = {
            "tenant_id": self.tenant_id,
            "user_key": self.user_key,
            "module_key": definition.module_key,
            "module_version": definition.module_version,
            "enabled": (
                definition.enabled_by_default
                if self.postgresql
                else int(definition.enabled_by_default)
            ),
            "config_json": "{}",
            "updated_at": (
                current if self.postgresql else current.isoformat()
            ),
        }
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    f"""
                    INSERT INTO {self.selection_table} (
                        tenant_id, user_key, module_key, module_version,
                        enabled, config_json, updated_at
                    ) VALUES (
                        {self._tenant()}, :user_key, :module_key,
                        :module_version, :enabled,
                        {self._json_cast("config_json")},
                        {self._time("updated_at")}
                    )
                    ON CONFLICT (tenant_id, user_key, module_key) DO NOTHING
                    """
                ),
                values,
            )

    def set_selection(
        self,
        module_key: str,
        module_version: str,
        *,
        enabled: bool,
        config: dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> dict[str, object]:
        current = now or datetime.now(timezone.utc)
        values = {
            "tenant_id": self.tenant_id,
            "user_key": self.user_key,
            "module_key": module_key,
            "module_version": module_version,
            "enabled": enabled if self.postgresql else int(enabled),
            "config_json": _json(config or {}),
            "updated_at": (
                current if self.postgresql else current.isoformat()
            ),
        }
        with self.engine.begin() as connection:
            result = connection.execute(
                text(
                    f"""
                    UPDATE {self.selection_table}
                    SET module_version = :module_version,
                        enabled = :enabled,
                        config_json = {self._json_cast("config_json")},
                        updated_at = {self._time("updated_at")}
                    WHERE tenant_id = {self._tenant()}
                      AND user_key = :user_key
                      AND module_key = :module_key
                    """
                ),
                values,
            )
            if result.rowcount != 1:
                raise KeyError(module_key)
        return self.get_selection(module_key)

    def get_selection(self, module_key: str) -> dict[str, object]:
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    f"""
                    SELECT module_key, module_version, enabled,
                           config_json, updated_at
                    FROM {self.selection_table}
                    WHERE tenant_id = {self._tenant()}
                      AND user_key = :user_key
                      AND module_key = :module_key
                    """
                ),
                {
                    "tenant_id": self.tenant_id,
                    "user_key": self.user_key,
                    "module_key": module_key,
                },
            ).mappings().one()
        config = _decoded(row["config_json"])
        return {
            "module_key": str(row["module_key"]),
            "module_version": str(row["module_version"]),
            "enabled": bool(row["enabled"]),
            "config": config if isinstance(config, dict) else {},
            "updated_at": str(row["updated_at"]),
        }

    def list_modules(self) -> list[dict[str, object]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT d.definition_json, d.definition_sha256,
                           s.enabled, s.config_json, s.updated_at
                    FROM {self.definition_table} d
                    JOIN {self.selection_table} s
                      ON s.module_key = d.module_key
                     AND s.module_version = d.module_version
                    WHERE s.tenant_id = {self._tenant()}
                      AND s.user_key = :user_key
                    ORDER BY d.module_key, d.module_version
                    """
                ),
                {
                    "tenant_id": self.tenant_id,
                    "user_key": self.user_key,
                },
            ).mappings().all()
        modules = []
        for row in rows:
            definition = _decoded(row["definition_json"])
            config = _decoded(row["config_json"])
            if not isinstance(definition, dict):
                continue
            modules.append(
                {
                    **definition,
                    "definition_sha256": str(
                        row["definition_sha256"]
                    ),
                    "enabled": bool(row["enabled"]),
                    "config": config if isinstance(config, dict) else {},
                    "selection_updated_at": str(row["updated_at"]),
                }
            )
        return modules

    def save_run(
        self,
        definition: AnalysisModuleDefinition,
        request: AnalysisRunRequest,
        result: AnalysisModuleResult,
        *,
        started_at: datetime,
        completed_at: datetime,
    ) -> dict[str, object]:
        run_id = str(uuid7())
        event = result.normalized_event
        values = {
            "run_id": run_id,
            "tenant_id": self.tenant_id,
            "user_key": self.user_key,
            "module_key": definition.module_key,
            "module_version": definition.module_version,
            "ticker": request.ticker,
            "asset_type": request.asset_type,
            "horizon": request.horizon,
            "request_json": _json(request.model_dump(mode="json")),
            "result_json": _json(result.output),
            "evidence_json": _json(
                [
                    evidence.model_dump(mode="json")
                    for evidence in result.evidence
                ]
            ),
            "lineage_json": _json(result.lineage),
            "normalized_event_json": _json(
                event.model_dump(mode="json")
            ),
            "started_at": (
                started_at if self.postgresql else started_at.isoformat()
            ),
            "completed_at": (
                completed_at
                if self.postgresql
                else completed_at.isoformat()
            ),
            "expires_at": (
                event.expires_at
                if self.postgresql
                else event.expires_at.isoformat()
            ),
        }
        run_cast = (
            "CAST(:run_id AS UUID)" if self.postgresql else ":run_id"
        )
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    f"""
                    INSERT INTO {self.run_table} (
                        run_id, tenant_id, user_key, module_key,
                        module_version, ticker, asset_type, horizon, status,
                        request_json, result_json, evidence_json,
                        lineage_json, normalized_event_json, error,
                        started_at, completed_at, expires_at
                    ) VALUES (
                        {run_cast}, {self._tenant()}, :user_key, :module_key,
                        :module_version, :ticker, :asset_type, :horizon,
                        'completed', {self._json_cast("request_json")},
                        {self._json_cast("result_json")},
                        {self._json_cast("evidence_json")},
                        {self._json_cast("lineage_json")},
                        {self._json_cast("normalized_event_json")}, NULL,
                        {self._time("started_at")},
                        {self._time("completed_at")},
                        {self._time("expires_at")}
                    )
                    """
                ),
                values,
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, object]:
        run_cast = (
            "CAST(:run_id AS UUID)" if self.postgresql else ":run_id"
        )
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    f"""
                    SELECT *
                    FROM {self.run_table}
                    WHERE run_id = {run_cast}
                      AND tenant_id = {self._tenant()}
                      AND user_key = :user_key
                    """
                ),
                {
                    "run_id": run_id,
                    "tenant_id": self.tenant_id,
                    "user_key": self.user_key,
                },
            ).mappings().one()
        payload: dict[str, Any] = dict(row)
        for key in (
            "request_json",
            "result_json",
            "evidence_json",
            "lineage_json",
            "normalized_event_json",
        ):
            payload[key.removesuffix("_json")] = _decoded(payload.pop(key))
        payload["run_id"] = str(payload["run_id"])
        payload["tenant_id"] = str(payload["tenant_id"])
        for key in ("started_at", "completed_at", "expires_at"):
            payload[key] = str(payload[key])
        return payload
