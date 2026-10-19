"""Tenant-scoped durable storage for reproducible backtest runs."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection

from app.config import settings
from app.migration_state import validate_schema


MODULE_KEY = "walk-forward-backtest"
MODULE_VERSION = "walk-forward-v1"


def uuid7() -> uuid.UUID:
    timestamp_ms = int(time.time_ns() // 1_000_000)
    random_bits = int.from_bytes(uuid.uuid4().bytes, "big")
    value = (
        (timestamp_ms & ((1 << 48) - 1)) << 80
        | 0x7 << 76
        | ((random_bits >> 64) & 0xFFF) << 64
        | 0x2 << 62
        | (random_bits & ((1 << 62) - 1))
    )
    return uuid.UUID(int=value)


def _database_url(database_url_or_path: str) -> str:
    if "://" in database_url_or_path:
        if database_url_or_path.startswith("postgresql://"):
            return database_url_or_path.replace(
                "postgresql://",
                "postgresql+psycopg://",
                1,
            )
        return database_url_or_path
    return f"sqlite:///{Path(database_url_or_path).resolve()}"


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    ).encode()


def _json_value(value: object) -> object:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _add_years(value: datetime, years: int) -> datetime:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(month=2, day=28, year=value.year + years)


def _file_uri_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"Unsupported backtest artifact URI: {uri}")
    return Path(url2pathname(unquote(parsed.path))).resolve()


class BacktestStore:
    def __init__(
        self,
        database_url_or_path: str | None = None,
        *,
        artifact_directory: str | Path | None = None,
        tenant_id: str | None = None,
        user_key: str | None = None,
        max_inline_result_bytes: int | None = None,
    ) -> None:
        configured_database = (
            database_url_or_path or settings.research_database_url
        )
        self.database_url = _database_url(configured_database)
        self.artifact_directory = Path(
            artifact_directory or settings.BACKTEST_ARTIFACT_DIRECTORY
        ).resolve()
        self.tenant_id = tenant_id or settings.RESEARCH_TENANT_ID
        self.user_key = (user_key or settings.RESEARCH_USER_KEY).strip()
        self.max_inline_result_bytes = (
            max_inline_result_bytes
            if max_inline_result_bytes is not None
            else settings.BACKTEST_INLINE_RESULT_MAX_BYTES
        )
        self.engine = create_engine(self.database_url)
        self.postgresql = self.engine.dialect.name == "postgresql"
        if self.postgresql:
            event.listen(self.engine, "connect", self._set_postgresql_utc)
        self.run_table = (
            "research.backtest_runs" if self.postgresql else "backtest_runs"
        )
        self.artifact_table = (
            "research.backtest_artifacts"
            if self.postgresql
            else "backtest_artifacts"
        )
        validate_schema(self.database_url)

    @staticmethod
    def _set_postgresql_utc(
        dbapi_connection: object,
        _connection_record: object,
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("SET TIME ZONE 'UTC'")
        cursor.close()

    def _write_artifact(
        self,
        run_id: str,
        payload: bytes,
    ) -> dict[str, object]:
        self.artifact_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        digest = hashlib.sha256(payload).hexdigest()
        artifact_path = self.artifact_directory / f"{run_id}-result.json"
        temporary_path = artifact_path.with_suffix(".json.tmp")
        temporary_path.write_bytes(payload)
        os.replace(temporary_path, artifact_path)
        artifact_path.chmod(0o600)
        return {
            "artifact_key": "full-result",
            "media_type": "application/json",
            "uri": artifact_path.as_uri(),
            "sha256": digest,
            "byte_size": len(payload),
        }

    def _read_artifact(self, reference: dict[str, object]) -> dict[str, object]:
        uri = str(reference["uri"])
        artifact_path = _file_uri_path(uri)
        try:
            artifact_path.relative_to(self.artifact_directory)
        except ValueError as error:
            raise ValueError(
                "Backtest artifact is outside the configured root"
            ) from error
        payload = artifact_path.read_bytes()
        if len(payload) != int(reference["byte_size"]):
            raise ValueError(
                "Backtest artifact size does not match its reference"
            )
        if hashlib.sha256(payload).hexdigest() != str(reference["sha256"]):
            raise ValueError(
                "Backtest artifact checksum does not match its reference"
            )
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise ValueError("Backtest artifact must contain a JSON object")
        return result

    def _json_parameter(self, value: object) -> str:
        return json.dumps(
            value,
            sort_keys=True,
            default=str,
            allow_nan=False,
        )

    def _insert_run(
        self,
        connection: Connection,
        *,
        run_id: str,
        ticker: str,
        strategy_version: str,
        created_at: datetime,
        request: dict[str, object],
        result: dict[str, object],
        eligibility: bool,
        input_lineage: dict[str, object],
        artifact_manifest: list[dict[str, object]],
        retention_class: str,
        retain_until: datetime,
    ) -> None:
        casts = {
            "run_id": "CAST(:run_id AS UUID)" if self.postgresql else ":run_id",
            "tenant_id": (
                "CAST(:tenant_id AS UUID)" if self.postgresql else ":tenant_id"
            ),
            "created_at": (
                "CAST(:created_at AS TIMESTAMPTZ)"
                if self.postgresql
                else ":created_at"
            ),
            "request_json": (
                "CAST(:request_json AS JSONB)"
                if self.postgresql
                else ":request_json"
            ),
            "result_json": (
                "CAST(:result_json AS JSONB)"
                if self.postgresql
                else ":result_json"
            ),
            "lineage": (
                "CAST(:input_lineage_json AS JSONB)"
                if self.postgresql
                else ":input_lineage_json"
            ),
            "manifest": (
                "CAST(:artifact_manifest_json AS JSONB)"
                if self.postgresql
                else ":artifact_manifest_json"
            ),
            "retain_until": (
                "CAST(:retain_until AS TIMESTAMPTZ)"
                if self.postgresql
                else ":retain_until"
            ),
        }
        connection.execute(
            text(
                f"""
                INSERT INTO {self.run_table} (
                    run_id, tenant_id, user_key, ticker, module_key,
                    module_version, strategy_version, created_at, status,
                    request_json, result_json, input_lineage_json,
                    artifact_manifest_json, alert_eligible, retention_class,
                    retain_until
                ) VALUES (
                    {casts["run_id"]}, {casts["tenant_id"]}, :user_key, :ticker,
                    :module_key, :module_version, :strategy_version,
                    {casts["created_at"]}, 'completed',
                    {casts["request_json"]}, {casts["result_json"]},
                    {casts["lineage"]}, {casts["manifest"]}, :alert_eligible,
                    :retention_class, {casts["retain_until"]}
                )
                """
            ),
            {
                "run_id": run_id,
                "tenant_id": self.tenant_id,
                "user_key": self.user_key,
                "ticker": ticker,
                "module_key": MODULE_KEY,
                "module_version": MODULE_VERSION,
                "strategy_version": strategy_version,
                "created_at": created_at.isoformat(),
                "request_json": self._json_parameter(request),
                "result_json": self._json_parameter(result),
                "input_lineage_json": self._json_parameter(input_lineage),
                "artifact_manifest_json": self._json_parameter(
                    artifact_manifest
                ),
                "alert_eligible": (
                    eligibility if self.postgresql else int(eligibility)
                ),
                "retention_class": retention_class,
                "retain_until": retain_until.isoformat(),
            },
        )
        for reference in artifact_manifest:
            artifact_cast = (
                "CAST(:artifact_id AS UUID)"
                if self.postgresql
                else ":artifact_id"
            )
            run_cast = (
                "CAST(:run_id AS UUID)" if self.postgresql else ":run_id"
            )
            tenant_cast = (
                "CAST(:tenant_id AS UUID)"
                if self.postgresql
                else ":tenant_id"
            )
            created_cast = (
                "CAST(:created_at AS TIMESTAMPTZ)"
                if self.postgresql
                else ":created_at"
            )
            retain_cast = (
                "CAST(:retain_until AS TIMESTAMPTZ)"
                if self.postgresql
                else ":retain_until"
            )
            connection.execute(
                text(
                    f"""
                    INSERT INTO {self.artifact_table} (
                        id, tenant_id, user_key, run_id, artifact_key,
                        media_type, uri, sha256, byte_size, created_at,
                        retain_until
                    ) VALUES (
                        {artifact_cast}, {tenant_cast}, :user_key, {run_cast},
                        :artifact_key, :media_type, :uri, :sha256, :byte_size,
                        {created_cast}, {retain_cast}
                    )
                    """
                ),
                {
                    "artifact_id": str(uuid7()),
                    "tenant_id": self.tenant_id,
                    "user_key": self.user_key,
                    "run_id": run_id,
                    **reference,
                    "created_at": created_at.isoformat(),
                    "retain_until": retain_until.isoformat(),
                },
            )

    def save(
        self,
        ticker: str,
        strategy_version: str,
        request: dict[str, object],
        result: dict[str, object],
        *,
        input_lineage: dict[str, object] | None = None,
    ) -> str:
        run_id = str(uuid7())
        created_at = datetime.now(timezone.utc)
        eligibility = result["alert_eligibility"]
        if not isinstance(eligibility, dict):
            raise ValueError("Backtest result is missing alert eligibility")
        eligible = bool(eligibility.get("eligible", False))
        lineage = input_lineage or {}
        stored_result = {
            **result,
            "run_id": run_id,
            "created_at": created_at.isoformat(),
            "module_key": MODULE_KEY,
            "module_version": MODULE_VERSION,
            "input_lineage": lineage,
        }
        payload = _json_bytes(stored_result)
        artifact_manifest: list[dict[str, object]] = []
        database_result = stored_result
        artifact_path: Path | None = None
        if len(payload) > self.max_inline_result_bytes:
            reference = self._write_artifact(run_id, payload)
            artifact_manifest.append(reference)
            artifact_path = _file_uri_path(str(reference["uri"]))
            database_result = {
                "run_id": run_id,
                "created_at": created_at.isoformat(),
                "ticker": ticker,
                "module_key": MODULE_KEY,
                "module_version": MODULE_VERSION,
                "strategy_version": strategy_version,
                "alert_eligibility": eligibility,
                "input_lineage": lineage,
                "artifact_result_key": "full-result",
            }
        retention_years = 7 if eligible else 2
        retention_class = (
            "recommendation-evidence" if eligible else "ad-hoc-research"
        )
        retain_until = _add_years(created_at, retention_years)
        try:
            with self.engine.begin() as connection:
                self._insert_run(
                    connection,
                    run_id=run_id,
                    ticker=ticker,
                    strategy_version=strategy_version,
                    created_at=created_at,
                    request=request,
                    result=database_result,
                    eligibility=eligible,
                    input_lineage=lineage,
                    artifact_manifest=artifact_manifest,
                    retention_class=retention_class,
                    retain_until=retain_until,
                )
        except Exception:
            if artifact_path is not None:
                artifact_path.unlink(missing_ok=True)
            raise
        return run_id

    def _load_result(self, row: Any) -> dict[str, object] | None:
        result = _json_value(row.result_json)
        manifest = _json_value(row.artifact_manifest_json)
        if not isinstance(result, dict):
            return None
        if isinstance(manifest, list):
            full_result = next(
                (
                    item
                    for item in manifest
                    if isinstance(item, dict)
                    and item.get("artifact_key") == "full-result"
                ),
                None,
            )
            if full_result is not None:
                return self._read_artifact(full_result)
        return result

    def _scope(self) -> dict[str, str]:
        return {"tenant_id": self.tenant_id, "user_key": self.user_key}

    def get(self, run_id: str) -> dict[str, object] | None:
        if self.postgresql:
            try:
                uuid.UUID(run_id)
            except ValueError:
                return None
        run_predicate = (
            "run_id = CAST(:run_id AS UUID)"
            if self.postgresql
            else "run_id = :run_id"
        )
        tenant_predicate = (
            "tenant_id = CAST(:tenant_id AS UUID)"
            if self.postgresql
            else "tenant_id = :tenant_id"
        )
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    f"""
                    SELECT result_json, artifact_manifest_json
                    FROM {self.run_table}
                    WHERE {run_predicate}
                      AND {tenant_predicate}
                      AND user_key = :user_key
                    """
                ),
                {"run_id": run_id, **self._scope()},
            ).fetchone()
        return None if row is None else self._load_result(row)

    def latest(self, ticker: str) -> dict[str, object] | None:
        tenant_predicate = (
            "tenant_id = CAST(:tenant_id AS UUID)"
            if self.postgresql
            else "tenant_id = :tenant_id"
        )
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    f"""
                    SELECT result_json, artifact_manifest_json
                    FROM {self.run_table}
                    WHERE ticker = :ticker
                      AND {tenant_predicate}
                      AND user_key = :user_key
                    ORDER BY created_at DESC, run_id DESC
                    LIMIT 1
                    """
                ),
                {"ticker": ticker, **self._scope()},
            ).fetchone()
        return None if row is None else self._load_result(row)

    def list(
        self,
        ticker: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        tenant_predicate = (
            "tenant_id = CAST(:tenant_id AS UUID)"
            if self.postgresql
            else "tenant_id = :tenant_id"
        )
        ticker_filter = " AND ticker = :ticker" if ticker else ""
        arguments: dict[str, object] = {**self._scope(), "limit": limit}
        if ticker:
            arguments["ticker"] = ticker
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT run_id, ticker, strategy_version, created_at,
                           alert_eligible
                    FROM {self.run_table}
                    WHERE {tenant_predicate}
                      AND user_key = :user_key
                      {ticker_filter}
                    ORDER BY created_at DESC, run_id DESC
                    LIMIT :limit
                    """
                ),
                arguments,
            ).fetchall()
        return [
            {
                "run_id": str(row.run_id),
                "ticker": row.ticker,
                "strategy_version": row.strategy_version,
                "created_at": (
                    row.created_at.isoformat()
                    if isinstance(row.created_at, datetime)
                    else row.created_at
                ),
                "alert_eligible": bool(row.alert_eligible),
            }
            for row in rows
        ]
