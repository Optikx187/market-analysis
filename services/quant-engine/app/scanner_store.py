"""Durable scanner scheduling, lease, result, and action storage."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine

from app.backtest_store import uuid7
from app.config import settings
from app.migration_state import validate_schema

SCANNER_KEY = "market-opportunity-scan"
UTC_ZONE = ZoneInfo("UTC")


def _database_url(database_url_or_path: str) -> str:
    if "://" in database_url_or_path:
        if database_url_or_path.startswith("postgresql://"):
            return database_url_or_path.replace(
                "postgresql://", "postgresql+psycopg://", 1
            )
        return database_url_or_path
    return f"sqlite:///{Path(database_url_or_path).resolve()}"


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC_ZONE)
    if current.tzinfo is None:
        return current.replace(tzinfo=UTC_ZONE)
    return current.astimezone(UTC_ZONE)


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


class ScannerStore:
    def __init__(
        self,
        database_url_or_path: str | None = None,
        *,
        tenant_id: str | None = None,
        user_key: str | None = None,
        scanner_key: str = SCANNER_KEY,
    ) -> None:
        self.database_url = _database_url(
            database_url_or_path or settings.research_database_url
        )
        self.tenant_id = tenant_id or settings.RESEARCH_TENANT_ID
        self.user_key = (user_key or settings.RESEARCH_USER_KEY).strip()
        self.scanner_key = scanner_key
        self.engine: Engine = create_engine(
            self.database_url,
            connect_args=(
                {"timeout": 30}
                if self.database_url.startswith("sqlite:///")
                else {}
            ),
        )
        self.postgresql = self.engine.dialect.name == "postgresql"
        if self.postgresql:
            event.listen(self.engine, "connect", self._set_postgresql_utc)
        else:
            event.listen(self.engine, "connect", self._set_sqlite_pragmas)
        self.config_table = (
            "research.scanner_configs" if self.postgresql else "scanner_configs"
        )
        self.run_table = (
            "research.scanner_runs" if self.postgresql else "scanner_runs"
        )
        self.action_table = (
            "research.scanner_opportunity_actions"
            if self.postgresql
            else "scanner_opportunity_actions"
        )
        self.receipt_table = (
            "research.scanner_notification_receipts"
            if self.postgresql
            else "scanner_notification_receipts"
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

    @staticmethod
    def _set_sqlite_pragmas(
        dbapi_connection: object,
        _connection_record: object,
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA busy_timeout = 30000")
        cursor.close()

    def _tenant(self) -> str:
        return "CAST(:tenant_id AS UUID)" if self.postgresql else ":tenant_id"

    def _run_id(self, name: str = "run_id") -> str:
        return (
            f"CAST(:{name} AS UUID)" if self.postgresql else f":{name}"
        )

    def _time(self, name: str) -> str:
        return (
            f"CAST(:{name} AS TIMESTAMPTZ)"
            if self.postgresql
            else f":{name}"
        )

    def _json_cast(self, name: str) -> str:
        return f"CAST(:{name} AS JSONB)" if self.postgresql else f":{name}"

    def _params(self) -> dict[str, object]:
        return {
            "tenant_id": self.tenant_id,
            "user_key": self.user_key,
            "scanner_key": self.scanner_key,
        }

    def ensure_config(
        self,
        *,
        enabled: bool,
        interval_minutes: int,
        market_hours_only: bool,
        now: datetime | None = None,
    ) -> dict[str, object]:
        if interval_minutes < 5:
            raise ValueError("Minimum scan interval is 5 minutes")
        current = _utc(now)
        values = {
            **self._params(),
            "enabled": enabled if self.postgresql else int(enabled),
            "interval_minutes": interval_minutes,
            "market_hours_only": (
                market_hours_only
                if self.postgresql
                else int(market_hours_only)
            ),
            "next_run_at": current,
            "updated_at": current,
        }
        with self.engine.begin() as connection:
            if self.postgresql:
                connection.execute(
                    text(
                        f"""
                        INSERT INTO {self.config_table} (
                            tenant_id, user_key, scanner_key, enabled,
                            interval_minutes, market_hours_only,
                            next_run_at, updated_at
                        ) VALUES (
                            {self._tenant()}, :user_key, :scanner_key,
                            :enabled, :interval_minutes, :market_hours_only,
                            {self._time("next_run_at")},
                            {self._time("updated_at")}
                        )
                        ON CONFLICT (tenant_id, user_key, scanner_key)
                        DO NOTHING
                        """
                    ),
                    values,
                )
            else:
                connection.execute(
                    text(
                        f"""
                        INSERT OR IGNORE INTO {self.config_table} (
                            tenant_id, user_key, scanner_key, enabled,
                            interval_minutes, market_hours_only,
                            next_run_at, updated_at
                        ) VALUES (
                            :tenant_id, :user_key, :scanner_key, :enabled,
                            :interval_minutes, :market_hours_only,
                            :next_run_at, :updated_at
                        )
                        """
                    ),
                    {
                        **values,
                        "next_run_at": current.isoformat(),
                        "updated_at": current.isoformat(),
                    },
                )
        return self.get_config()

    def get_config(self) -> dict[str, object]:
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    f"""
                    SELECT enabled, interval_minutes, market_hours_only,
                           next_run_at, updated_at
                    FROM {self.config_table}
                    WHERE tenant_id = {self._tenant()}
                      AND user_key = :user_key
                      AND scanner_key = :scanner_key
                    """
                ),
                self._params(),
            ).mappings().one()
        return {
            "enabled": bool(row["enabled"]),
            "interval_minutes": int(row["interval_minutes"]),
            "market_hours_only": bool(row["market_hours_only"]),
            "next_run_at": str(row["next_run_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def update_config(
        self,
        *,
        enabled: bool | None = None,
        interval_minutes: int | None = None,
        market_hours_only: bool | None = None,
        now: datetime | None = None,
    ) -> dict[str, object]:
        if interval_minutes is not None and interval_minutes < 5:
            raise ValueError("Minimum scan interval is 5 minutes")
        current = _utc(now)
        assignments: list[str] = []
        values = {**self._params(), "updated_at": current}
        if enabled is not None:
            assignments.append("enabled = :enabled")
            values["enabled"] = (
                enabled if self.postgresql else int(enabled)
            )
        if interval_minutes is not None:
            assignments.extend(
                [
                    "interval_minutes = :interval_minutes",
                    f"next_run_at = {self._time('next_run_at')}",
                ]
            )
            values["interval_minutes"] = interval_minutes
            values["next_run_at"] = current
        if market_hours_only is not None:
            assignments.append("market_hours_only = :market_hours_only")
            values["market_hours_only"] = (
                market_hours_only
                if self.postgresql
                else int(market_hours_only)
            )
        if not assignments:
            return self.get_config()
        assignments.append(f"updated_at = {self._time('updated_at')}")
        if not self.postgresql:
            for key in ("updated_at", "next_run_at"):
                if key in values:
                    values[key] = _utc(values[key]).isoformat()  # type: ignore[arg-type]
        with self.engine.begin() as connection:
            result = connection.execute(
                text(
                    f"""
                    UPDATE {self.config_table}
                    SET {", ".join(assignments)}
                    WHERE tenant_id = {self._tenant()}
                      AND user_key = :user_key
                      AND scanner_key = :scanner_key
                    """
                ),
                values,
            )
            if result.rowcount != 1:
                raise RuntimeError("Scanner configuration is missing")
        return self.get_config()

    def defer_until(
        self,
        next_run_at: datetime,
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        deferred = _utc(next_run_at)
        current = _utc(now)
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    f"""
                    UPDATE {self.config_table}
                    SET next_run_at = CASE
                            WHEN next_run_at < {self._time("next_run_at")}
                            THEN {self._time("next_run_at")}
                            ELSE next_run_at
                        END,
                        updated_at = {self._time("updated_at")}
                    WHERE tenant_id = {self._tenant()}
                      AND user_key = :user_key
                      AND scanner_key = :scanner_key
                    """
                ),
                {
                    **self._params(),
                    "next_run_at": (
                        deferred
                        if self.postgresql
                        else deferred.isoformat()
                    ),
                    "updated_at": (
                        current
                        if self.postgresql
                        else current.isoformat()
                    ),
                },
            )
        return self.get_config()

    def _insert_run(
        self,
        connection: Connection,
        *,
        run_id: str,
        idempotency_key: str,
        trigger_type: str,
        scheduled_for: datetime,
        now: datetime,
    ) -> None:
        values = {
            **self._params(),
            "run_id": run_id,
            "idempotency_key": idempotency_key,
            "trigger_type": trigger_type,
            "scheduled_for": scheduled_for,
            "available_at": now,
            "created_at": now,
            "updated_at": now,
        }
        if self.postgresql:
            connection.execute(
                text(
                    f"""
                    INSERT INTO {self.run_table} (
                        run_id, tenant_id, user_key, scanner_key,
                        idempotency_key, trigger_type, status,
                        scheduled_for, available_at, created_at, updated_at
                    ) VALUES (
                        {self._run_id()}, {self._tenant()}, :user_key,
                        :scanner_key, :idempotency_key, :trigger_type,
                        'queued', {self._time("scheduled_for")},
                        {self._time("available_at")},
                        {self._time("created_at")},
                        {self._time("updated_at")}
                    )
                    ON CONFLICT (
                        tenant_id, user_key, scanner_key, idempotency_key
                    ) DO NOTHING
                    """
                ),
                values,
            )
        else:
            connection.execute(
                text(
                    f"""
                    INSERT OR IGNORE INTO {self.run_table} (
                        run_id, tenant_id, user_key, scanner_key,
                        idempotency_key, trigger_type, status,
                        scheduled_for, available_at, created_at, updated_at
                    ) VALUES (
                        :run_id, :tenant_id, :user_key, :scanner_key,
                        :idempotency_key, :trigger_type, 'queued',
                        :scheduled_for, :available_at, :created_at, :updated_at
                    )
                    """
                ),
                {
                    **values,
                    "scheduled_for": scheduled_for.isoformat(),
                    "available_at": now.isoformat(),
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                },
            )

    def enqueue_manual(
        self,
        idempotency_key: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        current = _utc(now)
        with self.engine.begin() as connection:
            self._insert_run(
                connection,
                run_id=str(uuid7()),
                idempotency_key=idempotency_key,
                trigger_type="manual",
                scheduled_for=current,
                now=current,
            )
        return self.get_run_by_idempotency(idempotency_key)

    def enqueue_due(self, *, now: datetime | None = None) -> None:
        current = _utc(now)
        connection = self.engine.connect()
        try:
            if self.postgresql:
                transaction = connection.begin()
            else:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                transaction = None
            lock = " FOR UPDATE" if self.postgresql else ""
            config = connection.execute(
                text(
                    f"""
                    SELECT enabled, interval_minutes, next_run_at
                    FROM {self.config_table}
                    WHERE tenant_id = {self._tenant()}
                      AND user_key = :user_key
                      AND scanner_key = :scanner_key{lock}
                    """
                ),
                self._params(),
            ).mappings().one_or_none()
            if not config or not bool(config["enabled"]):
                if transaction:
                    transaction.commit()
                else:
                    connection.commit()
                return
            due = _utc(
                datetime.fromisoformat(str(config["next_run_at"]))
                if isinstance(config["next_run_at"], str)
                else config["next_run_at"]
            )
            if due > current:
                if transaction:
                    transaction.commit()
                else:
                    connection.commit()
                return
            idempotency_key = f"scheduled:{self.scanner_key}:{due.isoformat()}"
            self._insert_run(
                connection,
                run_id=str(uuid7()),
                idempotency_key=idempotency_key,
                trigger_type="scheduled",
                scheduled_for=due,
                now=current,
            )
            interval = timedelta(minutes=int(config["interval_minutes"]))
            next_run = due + interval
            while next_run <= current:
                next_run += interval
            connection.execute(
                text(
                    f"""
                    UPDATE {self.config_table}
                    SET next_run_at = {self._time("next_run_at")},
                        updated_at = {self._time("updated_at")}
                    WHERE tenant_id = {self._tenant()}
                      AND user_key = :user_key
                      AND scanner_key = :scanner_key
                    """
                ),
                {
                    **self._params(),
                    "next_run_at": (
                        next_run
                        if self.postgresql
                        else next_run.isoformat()
                    ),
                    "updated_at": (
                        current
                        if self.postgresql
                        else current.isoformat()
                    ),
                },
            )
            if transaction:
                transaction.commit()
            else:
                connection.commit()
        except Exception:
            if connection.in_transaction():
                connection.rollback()
            raise
        finally:
            connection.close()

    def claim_next(
        self,
        owner: str,
        *,
        now: datetime | None = None,
        lease_seconds: int = 120,
        max_attempts: int = 3,
        allow_scheduled: bool = True,
    ) -> dict[str, object] | None:
        current = _utc(now)
        if allow_scheduled:
            self.enqueue_due(now=current)
        expires = current + timedelta(seconds=lease_seconds)
        params = {
            **self._params(),
            "owner": owner,
            "now": current,
            "expires": expires,
            "max_attempts": max_attempts,
            "allow_scheduled": (
                allow_scheduled if self.postgresql else int(allow_scheduled)
            ),
        }
        if self.postgresql:
            with self.engine.begin() as connection:
                row = connection.execute(
                    text(
                        f"""
                        WITH candidate AS (
                            SELECT run_id
                            FROM {self.run_table}
                            WHERE tenant_id = {self._tenant()}
                              AND user_key = :user_key
                              AND scanner_key = :scanner_key
                              AND attempt_count < :max_attempts
                              AND (
                                  :allow_scheduled
                                  OR trigger_type = 'manual'
                              )
                              AND (
                                  (
                                      status IN ('queued', 'failed')
                                      AND available_at <= {self._time("now")}
                                  )
                                  OR (
                                      status = 'running'
                                      AND lease_expires_at <= {self._time("now")}
                                  )
                              )
                            ORDER BY scheduled_for, created_at
                            FOR UPDATE SKIP LOCKED
                            LIMIT 1
                        )
                        UPDATE {self.run_table} AS run
                        SET status = 'running',
                            lease_owner = :owner,
                            lease_expires_at = {self._time("expires")},
                            attempt_count = run.attempt_count + 1,
                            started_at = COALESCE(
                                run.started_at, {self._time("now")}
                            ),
                            error = NULL,
                            updated_at = {self._time("now")}
                        FROM candidate
                        WHERE run.run_id = candidate.run_id
                        RETURNING run.*
                        """
                    ),
                    params,
                ).mappings().one_or_none()
        else:
            connection = self.engine.connect()
            try:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                row = connection.execute(
                    text(
                        f"""
                        SELECT *
                        FROM {self.run_table}
                        WHERE tenant_id = :tenant_id
                          AND user_key = :user_key
                          AND scanner_key = :scanner_key
                          AND attempt_count < :max_attempts
                          AND (
                              :allow_scheduled
                              OR trigger_type = 'manual'
                          )
                          AND (
                              (
                                  status IN ('queued', 'failed')
                                  AND available_at <= :now
                              )
                              OR (
                                  status = 'running'
                                  AND lease_expires_at <= :now
                              )
                          )
                        ORDER BY scheduled_for, created_at
                        LIMIT 1
                        """
                    ),
                    {
                        **params,
                        "now": current.isoformat(),
                    },
                ).mappings().one_or_none()
                if row:
                    connection.execute(
                        text(
                            f"""
                            UPDATE {self.run_table}
                            SET status = 'running', lease_owner = :owner,
                                lease_expires_at = :expires,
                                attempt_count = attempt_count + 1,
                                started_at = COALESCE(started_at, :now),
                                error = NULL, updated_at = :now
                            WHERE run_id = :run_id
                            """
                        ),
                        {
                            "owner": owner,
                            "expires": expires.isoformat(),
                            "now": current.isoformat(),
                            "run_id": row["run_id"],
                        },
                    )
                    row = connection.execute(
                        text(
                            f"SELECT * FROM {self.run_table} "
                            "WHERE run_id = :run_id"
                        ),
                        {"run_id": row["run_id"]},
                    ).mappings().one()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        return self._run_payload(row) if row else None

    def claim_run(
        self,
        run_id: str,
        owner: str,
        *,
        now: datetime | None = None,
        lease_seconds: int = 120,
        max_attempts: int = 3,
    ) -> dict[str, object] | None:
        current = _utc(now)
        expires = current + timedelta(seconds=lease_seconds)
        with self.engine.begin() as connection:
            row = connection.execute(
                text(
                    f"""
                    UPDATE {self.run_table}
                    SET status = 'running', lease_owner = :owner,
                        lease_expires_at = {self._time("expires")},
                        attempt_count = attempt_count + 1,
                        started_at = COALESCE(
                            started_at, {self._time("now")}
                        ),
                        error = NULL, updated_at = {self._time("now")}
                    WHERE run_id = {self._run_id()}
                      AND tenant_id = {self._tenant()}
                      AND user_key = :user_key
                      AND attempt_count < :max_attempts
                      AND (
                          (
                              status IN ('queued', 'failed')
                              AND available_at <= {self._time("now")}
                          )
                          OR (
                              status = 'running'
                              AND lease_expires_at <= {self._time("now")}
                          )
                      )
                    RETURNING *
                    """
                ),
                {
                    **self._params(),
                    "run_id": run_id,
                    "owner": owner,
                    "max_attempts": max_attempts,
                    "expires": (
                        expires if self.postgresql else expires.isoformat()
                    ),
                    "now": (
                        current if self.postgresql else current.isoformat()
                    ),
                },
            ).mappings().one_or_none()
        return self._run_payload(row) if row else None

    def heartbeat(
        self,
        run_id: str,
        owner: str,
        *,
        now: datetime | None = None,
        lease_seconds: int = 120,
    ) -> bool:
        current = _utc(now)
        with self.engine.begin() as connection:
            result = connection.execute(
                text(
                    f"""
                    UPDATE {self.run_table}
                    SET lease_expires_at = {self._time("expires")},
                        updated_at = {self._time("now")}
                    WHERE run_id = {self._run_id()}
                      AND tenant_id = {self._tenant()}
                      AND user_key = :user_key
                      AND lease_owner = :owner
                      AND status = 'running'
                    """
                ),
                {
                    **self._params(),
                    "run_id": run_id,
                    "owner": owner,
                    "expires": (
                        current + timedelta(seconds=lease_seconds)
                        if self.postgresql
                        else (
                            current + timedelta(seconds=lease_seconds)
                        ).isoformat()
                    ),
                    "now": (
                        current if self.postgresql else current.isoformat()
                    ),
                },
            )
        return result.rowcount == 1

    def complete(
        self,
        run_id: str,
        owner: str,
        result: dict[str, object],
        *,
        now: datetime | None = None,
    ) -> None:
        current = _utc(now)
        with self.engine.begin() as connection:
            update = connection.execute(
                text(
                    f"""
                    UPDATE {self.run_table}
                    SET status = 'completed',
                        result_json = {self._json_cast("result_json")},
                        lease_owner = NULL, lease_expires_at = NULL,
                        completed_at = {self._time("now")},
                        updated_at = {self._time("now")}
                    WHERE run_id = {self._run_id()}
                      AND tenant_id = {self._tenant()}
                      AND user_key = :user_key
                      AND lease_owner = :owner
                      AND status = 'running'
                    """
                ),
                {
                    **self._params(),
                    "run_id": run_id,
                    "owner": owner,
                    "result_json": _json(result),
                    "now": (
                        current if self.postgresql else current.isoformat()
                    ),
                },
            )
            if update.rowcount != 1:
                raise RuntimeError("Scanner run lease was lost before completion")

    def fail(
        self,
        run_id: str,
        owner: str,
        error: str,
        *,
        now: datetime | None = None,
        retry_seconds: int = 30,
    ) -> None:
        current = _utc(now)
        with self.engine.begin() as connection:
            update = connection.execute(
                text(
                    f"""
                    UPDATE {self.run_table}
                    SET status = 'failed', error = :error,
                        available_at = {self._time("available_at")},
                        lease_owner = NULL, lease_expires_at = NULL,
                        updated_at = {self._time("now")}
                    WHERE run_id = {self._run_id()}
                      AND tenant_id = {self._tenant()}
                      AND user_key = :user_key
                      AND lease_owner = :owner
                      AND status = 'running'
                    """
                ),
                {
                    **self._params(),
                    "run_id": run_id,
                    "owner": owner,
                    "error": error[:2000],
                    "available_at": (
                        current + timedelta(seconds=retry_seconds)
                        if self.postgresql
                        else (
                            current + timedelta(seconds=retry_seconds)
                        ).isoformat()
                    ),
                    "now": (
                        current if self.postgresql else current.isoformat()
                    ),
                },
            )
            if update.rowcount != 1:
                raise RuntimeError("Scanner run lease was lost before failure")

    def get_run(self, run_id: str) -> dict[str, object]:
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    f"""
                    SELECT * FROM {self.run_table}
                    WHERE run_id = {self._run_id()}
                      AND tenant_id = {self._tenant()}
                      AND user_key = :user_key
                    """
                ),
                {**self._params(), "run_id": run_id},
            ).mappings().one()
        return self._run_payload(row)

    def get_run_by_idempotency(
        self,
        idempotency_key: str,
    ) -> dict[str, object]:
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    f"""
                    SELECT * FROM {self.run_table}
                    WHERE tenant_id = {self._tenant()}
                      AND user_key = :user_key
                      AND scanner_key = :scanner_key
                      AND idempotency_key = :idempotency_key
                    """
                ),
                {
                    **self._params(),
                    "idempotency_key": idempotency_key,
                },
            ).mappings().one()
        return self._run_payload(row)

    def latest_completed(self) -> dict[str, object] | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    f"""
                    SELECT * FROM {self.run_table}
                    WHERE tenant_id = {self._tenant()}
                      AND user_key = :user_key
                      AND scanner_key = :scanner_key
                      AND status = 'completed'
                    ORDER BY completed_at DESC, created_at DESC
                    LIMIT 1
                    """
                ),
                self._params(),
            ).mappings().one_or_none()
        return self._run_payload(row) if row else None

    def status(self) -> dict[str, object]:
        config = self.get_config()
        with self.engine.connect() as connection:
            counts = connection.execute(
                text(
                    f"""
                    SELECT status, count(*) AS count
                    FROM {self.run_table}
                    WHERE tenant_id = {self._tenant()}
                      AND user_key = :user_key
                      AND scanner_key = :scanner_key
                    GROUP BY status
                    """
                ),
                self._params(),
            ).mappings().all()
            totals = connection.execute(
                text(
                    f"""
                    SELECT count(*) AS total_scans,
                           COALESCE(sum(
                               CASE WHEN status = 'completed' THEN 1 ELSE 0 END
                           ), 0) AS completed_scans
                    FROM {self.run_table}
                    WHERE tenant_id = {self._tenant()}
                      AND user_key = :user_key
                      AND scanner_key = :scanner_key
                    """
                ),
                self._params(),
            ).mappings().one()
            completed_results = connection.execute(
                text(
                    f"""
                    SELECT result_json
                    FROM {self.run_table}
                    WHERE tenant_id = {self._tenant()}
                      AND user_key = :user_key
                      AND scanner_key = :scanner_key
                      AND status = 'completed'
                    """
                ),
                self._params(),
            ).scalars().all()
        latest = self.latest_completed()
        result = latest.get("result") if latest else None
        signals = result.get("signals", []) if isinstance(result, dict) else []
        return {
            **config,
            "next_scan_at": config["next_run_at"],
            "last_scan_at": (
                latest.get("completed_at") if latest else None
            ),
            "last_scan_result": result,
            "total_scans": int(totals["completed_scans"]),
            "total_signals_found": sum(
                int(
                    (decoded.get("signals_found", 0))
                    if isinstance(decoded, dict)
                    else 0
                )
                for decoded in (_decoded(value) for value in completed_results)
            ),
            "latest_signal_count": (
                len(signals) if isinstance(signals, list) else 0
            ),
            "runs_by_status": {
                str(row["status"]): int(row["count"]) for row in counts
            },
        }

    def save_action(
        self,
        opportunity_id: str,
        action: str,
        state: dict[str, object],
        *,
        now: datetime | None = None,
    ) -> None:
        current = _utc(now)
        values = {
            **self._params(),
            "opportunity_id": opportunity_id,
            "action": action,
            "state_json": _json(state),
            "updated_at": (
                current if self.postgresql else current.isoformat()
            ),
        }
        with self.engine.begin() as connection:
            if self.postgresql:
                connection.execute(
                    text(
                        f"""
                        INSERT INTO {self.action_table} (
                            tenant_id, user_key, opportunity_id,
                            action, state_json, updated_at
                        ) VALUES (
                            {self._tenant()}, :user_key, :opportunity_id,
                            :action, {self._json_cast("state_json")},
                            {self._time("updated_at")}
                        )
                        ON CONFLICT (tenant_id, user_key, opportunity_id)
                        DO UPDATE SET action = EXCLUDED.action,
                                      state_json = EXCLUDED.state_json,
                                      updated_at = EXCLUDED.updated_at
                        """
                    ),
                    values,
                )
            else:
                connection.execute(
                    text(
                        f"""
                        INSERT INTO {self.action_table} (
                            tenant_id, user_key, opportunity_id,
                            action, state_json, updated_at
                        ) VALUES (
                            :tenant_id, :user_key, :opportunity_id,
                            :action, :state_json, :updated_at
                        )
                        ON CONFLICT (tenant_id, user_key, opportunity_id)
                        DO UPDATE SET action = excluded.action,
                                      state_json = excluded.state_json,
                                      updated_at = excluded.updated_at
                        """
                    ),
                    values,
                )

    def get_action(self, opportunity_id: str) -> dict[str, object] | None:
        with self.engine.connect() as connection:
            value = connection.execute(
                text(
                    f"""
                    SELECT state_json FROM {self.action_table}
                    WHERE tenant_id = {self._tenant()}
                      AND user_key = :user_key
                      AND opportunity_id = :opportunity_id
                    """
                ),
                {**self._params(), "opportunity_id": opportunity_id},
            ).scalar_one_or_none()
        decoded = _decoded(value) if value is not None else None
        return decoded if isinstance(decoded, dict) else None

    def claim_notification(
        self,
        run_id: str,
        opportunity_id: str,
        idempotency_key: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        current = _utc(now)
        cutoff = current - timedelta(hours=24)
        values = {
            **self._params(),
            "run_id": run_id,
            "opportunity_id": opportunity_id,
            "idempotency_key": idempotency_key,
            "now": current if self.postgresql else current.isoformat(),
            "cutoff": (
                cutoff if self.postgresql else cutoff.isoformat()
            ),
        }
        with self.engine.begin() as connection:
            if self.postgresql:
                row = connection.execute(
                    text(
                        f"""
                        INSERT INTO {self.receipt_table} (
                            tenant_id, user_key, idempotency_key, run_id,
                            opportunity_id, status, created_at, updated_at
                        ) VALUES (
                            {self._tenant()}, :user_key, :idempotency_key,
                            {self._run_id()}, :opportunity_id, 'pending',
                            {self._time("now")}, {self._time("now")}
                        )
                        ON CONFLICT (
                            tenant_id, user_key, idempotency_key
                        ) DO UPDATE SET
                            run_id = EXCLUDED.run_id,
                            opportunity_id = EXCLUDED.opportunity_id,
                            status = 'pending', error = NULL,
                            updated_at = EXCLUDED.updated_at
                        WHERE {self.receipt_table}.status = 'failed'
                           OR (
                               {self.receipt_table}.status = 'sent'
                               AND {self.receipt_table}.updated_at
                                   <= {self._time("cutoff")}
                           )
                        RETURNING status
                        """
                    ),
                    values,
                ).scalar_one_or_none()
            else:
                existing = connection.execute(
                    text(
                        f"""
                        SELECT status FROM {self.receipt_table}
                        WHERE tenant_id = :tenant_id
                          AND user_key = :user_key
                          AND idempotency_key = :idempotency_key
                        """
                    ),
                    values,
                ).scalar_one_or_none()
                if existing is None:
                    connection.execute(
                        text(
                            f"""
                            INSERT INTO {self.receipt_table} (
                                tenant_id, user_key, idempotency_key, run_id,
                                opportunity_id, status, created_at, updated_at
                            ) VALUES (
                                :tenant_id, :user_key, :idempotency_key,
                                :run_id, :opportunity_id, 'pending', :now, :now
                            )
                            """
                        ),
                        values,
                    )
                    row = "pending"
                elif existing == "failed":
                    connection.execute(
                        text(
                            f"""
                            UPDATE {self.receipt_table}
                            SET run_id = :run_id,
                                opportunity_id = :opportunity_id,
                                status = 'pending', error = NULL,
                                updated_at = :now
                            WHERE tenant_id = :tenant_id
                              AND user_key = :user_key
                              AND idempotency_key = :idempotency_key
                            """
                        ),
                        values,
                    )
                    row = "pending"
                elif existing == "sent":
                    refreshed = connection.execute(
                        text(
                            f"""
                            UPDATE {self.receipt_table}
                            SET run_id = :run_id,
                                opportunity_id = :opportunity_id,
                                status = 'pending', error = NULL,
                                updated_at = :now
                            WHERE tenant_id = :tenant_id
                              AND user_key = :user_key
                              AND idempotency_key = :idempotency_key
                              AND updated_at <= :cutoff
                            """
                        ),
                        values,
                    )
                    row = "pending" if refreshed.rowcount == 1 else None
                else:
                    row = None
        return row == "pending"

    def finish_notification(
        self,
        idempotency_key: str,
        *,
        sent: bool,
        error: str | None = None,
        now: datetime | None = None,
    ) -> None:
        current = _utc(now)
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    f"""
                    UPDATE {self.receipt_table}
                    SET status = :status, error = :error,
                        updated_at = {self._time("now")}
                    WHERE tenant_id = {self._tenant()}
                      AND user_key = :user_key
                      AND idempotency_key = :idempotency_key
                      AND status = 'pending'
                    """
                ),
                {
                    **self._params(),
                    "idempotency_key": idempotency_key,
                    "status": "sent" if sent else "failed",
                    "error": error[:2000] if error else None,
                    "now": (
                        current if self.postgresql else current.isoformat()
                    ),
                },
            )

    @staticmethod
    def _run_payload(row: Any) -> dict[str, object]:
        result = _decoded(row["result_json"]) if row["result_json"] else None
        return {
            "run_id": str(row["run_id"]),
            "idempotency_key": str(row["idempotency_key"]),
            "trigger_type": str(row["trigger_type"]),
            "status": str(row["status"]),
            "scheduled_for": str(row["scheduled_for"]),
            "available_at": str(row["available_at"]),
            "lease_owner": row["lease_owner"],
            "lease_expires_at": (
                str(row["lease_expires_at"])
                if row["lease_expires_at"] is not None
                else None
            ),
            "attempt_count": int(row["attempt_count"]),
            "result": result if isinstance(result, dict) else None,
            "error": row["error"],
            "created_at": str(row["created_at"]),
            "started_at": (
                str(row["started_at"])
                if row["started_at"] is not None
                else None
            ),
            "completed_at": (
                str(row["completed_at"])
                if row["completed_at"] is not None
                else None
            ),
            "updated_at": str(row["updated_at"]),
        }
