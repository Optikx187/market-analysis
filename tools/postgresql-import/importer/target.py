from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from importer.common import canonical_json, uuid7

DRY_RUN_NAMESPACE = uuid.UUID("6a88f156-b0c7-4c15-b0bb-a15512696075")


class Target:
    def __init__(
        self,
        database_url: str,
        *,
        tenant_id: str,
        operator_id: str,
        mode: str,
        fingerprint_sha256: str,
        fingerprint: dict[str, object],
        options: dict[str, object],
        resume_run_id: str | None = None,
    ) -> None:
        self.connection = psycopg.connect(
            database_url.replace("postgresql+psycopg://", "postgresql://", 1),
            row_factory=dict_row,
        )
        self.connection.execute("SET TIME ZONE 'UTC'")
        self.tenant_id = tenant_id
        self.operator_id = operator_id
        self.mode = mode
        self.fingerprint_sha256 = fingerprint_sha256
        self.run_id: str | None = resume_run_id
        self.mapping_cache: dict[tuple[str, str, str], str] = {}
        if mode == "apply":
            self._start_run(fingerprint, options, resume_run_id)

    def close(self) -> None:
        self.connection.close()

    def _start_run(
        self,
        fingerprint: dict[str, object],
        options: dict[str, object],
        resume_run_id: str | None,
    ) -> None:
        if resume_run_id:
            row = self.connection.execute(
                """
                SELECT id, source_fingerprint_sha256, status
                FROM operations.import_runs
                WHERE id = %s
                """,
                (resume_run_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Import run not found: {resume_run_id}")
            if row["source_fingerprint_sha256"] != self.fingerprint_sha256:
                raise ValueError(
                    "Resume source fingerprint does not match the original run"
                )
            if row["status"] == "reconciled":
                raise ValueError("A reconciled import run does not need resume")
            self.connection.execute(
                """
                UPDATE operations.import_runs
                SET status='running', updated_at=CURRENT_TIMESTAMP
                WHERE id=%s
                """,
                (resume_run_id,),
            )
            self.connection.commit()
            return
        self.run_id = str(uuid7())
        self.connection.execute(
            """
            INSERT INTO operations.import_runs (
                id, tenant_id, source_fingerprint,
                source_fingerprint_sha256, mode, status, options_json,
                operator_id
            ) VALUES (
                %s, %s, %s::jsonb, %s, 'apply', 'running', %s::jsonb, %s
            )
            """,
            (
                self.run_id,
                self.tenant_id,
                canonical_json(fingerprint),
                self.fingerprint_sha256,
                canonical_json(options),
                self.operator_id,
            ),
        )
        self.connection.commit()

    def mapping(
        self,
        *,
        source_store: str,
        source_table: str,
        source_key: str,
        source_row_checksum: str,
        target_schema: str,
        target_table: str,
        preserve_uuid: str | None = None,
        target_id_override: str | None = None,
    ) -> str:
        row = self.connection.execute(
            """
            SELECT target_id, source_row_checksum, target_schema, target_table
            FROM operations.import_mappings
            WHERE source_fingerprint_sha256=%s
              AND source_store=%s AND source_table=%s AND source_key=%s
            """,
            (
                self.fingerprint_sha256,
                source_store,
                source_table,
                source_key,
            ),
        ).fetchone()
        if row is not None:
            if row["source_row_checksum"] != source_row_checksum:
                raise ValueError(
                    f"Source row changed after mapping: "
                    f"{source_store}.{source_table}:{source_key}"
                )
            if (
                row["target_schema"] != target_schema
                or row["target_table"] != target_table
            ):
                raise ValueError(
                    f"Mapped target changed for "
                    f"{source_store}.{source_table}:{source_key}"
                )
            target_id = str(row["target_id"])
            self.mapping_cache[
                (source_store, source_table, source_key)
            ] = target_id
            return target_id
        if target_id_override is not None:
            target_id = target_id_override
        elif preserve_uuid:
            target_id = str(uuid.UUID(preserve_uuid))
        elif self.mode == "dry-run":
            target_id = str(
                uuid.uuid5(
                    DRY_RUN_NAMESPACE,
                    f"{self.fingerprint_sha256}|{source_store}|"
                    f"{source_table}|{source_key}",
                )
            )
        else:
            target_id = str(uuid7())
        if self.mode == "apply":
            assert self.run_id is not None
            self.connection.execute(
                """
                INSERT INTO operations.import_mappings (
                    id, first_import_run_id, source_fingerprint_sha256,
                    source_store, source_table, source_key,
                    source_row_checksum, target_schema, target_table,
                    target_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid7()),
                    self.run_id,
                    self.fingerprint_sha256,
                    source_store,
                    source_table,
                    source_key,
                    source_row_checksum,
                    target_schema,
                    target_table,
                    target_id,
                ),
            )
        self.mapping_cache[(source_store, source_table, source_key)] = target_id
        return target_id

    def count(self, schema: str, table: str) -> int:
        query = sql.SQL("SELECT COUNT(*) AS count FROM {}.{}").format(
            sql.Identifier(schema),
            sql.Identifier(table),
        )
        return int(self.connection.execute(query).fetchone()["count"])

    def mapping_count(self) -> int:
        return int(
            self.connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM operations.import_mappings
                WHERE source_fingerprint_sha256=%s
                """,
                (self.fingerprint_sha256,),
            ).fetchone()["count"]
        )

    def fetch_by_id(
        self,
        schema: str,
        table: str,
        id_column: str,
        target_id: object,
    ) -> dict[str, object] | None:
        query = sql.SQL("SELECT * FROM {}.{} WHERE {}=%s").format(
            sql.Identifier(schema),
            sql.Identifier(table),
            sql.Identifier(id_column),
        )
        return self.connection.execute(query, (target_id,)).fetchone()

    def insert(
        self,
        schema: str,
        table: str,
        values: dict[str, object],
    ) -> None:
        columns = list(values)
        query = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
            sql.Identifier(schema),
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        )
        self.connection.execute(query, [values[column] for column in columns])

    def update_progress(
        self,
        *,
        source_store: str,
        source_table: str,
        status: str,
        last_source_key: str | None,
        source_rows: int,
        imported_rows: int,
        skipped_rows: int,
        conflict_rows: int,
        source_checksum: str,
        target_checksum: str,
    ) -> None:
        if self.mode != "apply":
            return
        assert self.run_id is not None
        self.connection.execute(
            """
            INSERT INTO operations.import_table_progress (
                import_run_id, source_store, source_table, status,
                last_source_key, source_rows, imported_rows, skipped_rows,
                conflict_rows, source_checksum, target_checksum
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (import_run_id, source_store, source_table)
            DO UPDATE SET
                status=EXCLUDED.status,
                last_source_key=EXCLUDED.last_source_key,
                source_rows=EXCLUDED.source_rows,
                imported_rows=EXCLUDED.imported_rows,
                skipped_rows=EXCLUDED.skipped_rows,
                conflict_rows=EXCLUDED.conflict_rows,
                source_checksum=EXCLUDED.source_checksum,
                target_checksum=EXCLUDED.target_checksum,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                self.run_id,
                source_store,
                source_table,
                status,
                last_source_key,
                source_rows,
                imported_rows,
                skipped_rows,
                conflict_rows,
                source_checksum,
                target_checksum,
            ),
        )
        self.connection.commit()

    def defer(
        self,
        *,
        source_store: str,
        source_table: str,
        source_key: str,
        source_row: dict[str, object],
        source_row_checksum: str,
        reason: str,
    ) -> None:
        if self.mode != "apply":
            return
        assert self.run_id is not None
        self.connection.execute(
            """
            INSERT INTO operations.import_deferred_records (
                id, import_run_id, source_fingerprint_sha256, source_store,
                source_table, source_key, reason, source_row,
                source_row_checksum, accepted_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (
                source_fingerprint_sha256, source_store,
                source_table, source_key
            ) DO NOTHING
            """,
            (
                str(uuid7()),
                self.run_id,
                self.fingerprint_sha256,
                source_store,
                source_table,
                source_key,
                reason,
                canonical_json(source_row),
                source_row_checksum,
                self.operator_id,
            ),
        )

    def commit(self) -> None:
        if self.mode == "apply":
            self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def finish(
        self,
        *,
        status: str,
        report: dict[str, object],
    ) -> None:
        if self.mode != "apply":
            return
        assert self.run_id is not None
        completed_at = (
            datetime.now(UTC) if status in {"failed", "reconciled"} else None
        )
        self.connection.execute(
            """
            UPDATE operations.import_runs
            SET status=%s, report_json=%s::jsonb,
                updated_at=CURRENT_TIMESTAMP, completed_at=%s
            WHERE id=%s
            """,
            (status, canonical_json(report), completed_at, self.run_id),
        )
        self.connection.commit()

    def table_columns(
        self,
        schema: str,
        table: str,
    ) -> dict[str, dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT column_name, data_type, udt_name, is_nullable,
                   column_default, numeric_scale
            FROM information_schema.columns
            WHERE table_schema=%s AND table_name=%s
            ORDER BY ordinal_position
            """,
            (schema, table),
        ).fetchall()
        return {str(row["column_name"]): dict(row) for row in rows}

    def foreign_keys(
        self,
        schema: str,
        table: str,
    ) -> dict[str, tuple[str, str, str]]:
        rows = self.connection.execute(
            """
            SELECT kcu.column_name, ccu.table_schema, ccu.table_name,
                   ccu.column_name AS referenced_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name=kcu.constraint_name
             AND tc.constraint_schema=kcu.constraint_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name=tc.constraint_name
             AND ccu.constraint_schema=tc.constraint_schema
            WHERE tc.constraint_type='FOREIGN KEY'
              AND tc.table_schema=%s AND tc.table_name=%s
            """,
            (schema, table),
        ).fetchall()
        return {
            str(row["column_name"]): (
                str(row["table_schema"]),
                str(row["table_name"]),
                str(row["referenced_column"]),
            )
            for row in rows
        }

    def mapped_target_ids(
        self,
        schema: str,
        table: str,
    ) -> set[str]:
        rows = self.connection.execute(
            """
            SELECT target_id FROM operations.import_mappings
            WHERE source_fingerprint_sha256=%s
              AND target_schema=%s AND target_table=%s
            """,
            (self.fingerprint_sha256, schema, table),
        ).fetchall()
        return {str(row["target_id"]) for row in rows}

    def lookup_mapping(
        self,
        source_store: str,
        source_table: str,
        source_value: object,
    ) -> str | None:
        key = (source_store, source_table, canonical_json(source_value))
        if key in self.mapping_cache:
            return self.mapping_cache[key]
        row = self.connection.execute(
            """
            SELECT target_id
            FROM operations.import_mappings
            WHERE source_fingerprint_sha256=%s
              AND source_store=%s AND source_table=%s AND source_key=%s
            """,
            (
                self.fingerprint_sha256,
                source_store,
                source_table,
                key[2],
            ),
        ).fetchone()
        if row is None:
            return None
        target_id = str(row["target_id"])
        self.mapping_cache[key] = target_id
        return target_id

    def rows_by_ids(
        self,
        schema: str,
        table: str,
        id_column: str,
        ids: Iterable[str],
    ) -> list[dict[str, object]]:
        values = list(ids)
        if not values:
            return []
        query = sql.SQL("SELECT * FROM {}.{} WHERE {} = ANY(%s)").format(
            sql.Identifier(schema),
            sql.Identifier(table),
            sql.Identifier(id_column),
        )
        return list(self.connection.execute(query, (values,)).fetchall())
