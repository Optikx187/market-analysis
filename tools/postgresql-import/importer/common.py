from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path


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


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    )


def checksum(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decimal_value(value: object, scale: int) -> Decimal:
    try:
        with localcontext() as context:
            context.prec = 60
            candidate = Decimal(str(value))
            if not candidate.is_finite():
                raise ValueError("value must be finite")
            return candidate.quantize(Decimal(1).scaleb(-scale))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"invalid decimal value {value!r}") from error


def utc_datetime(value: object) -> tuple[datetime, bool]:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            raise ValueError("timestamp is empty")
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as error:
            raise ValueError(f"invalid timestamp {value!r}") from error
    assumed_utc = parsed.tzinfo is None
    if assumed_utc:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC), assumed_utc


def source_key(value: object) -> str:
    return canonical_json(value)


class ReadOnlySQLite:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        if not self.path.is_file():
            raise ValueError(f"SQLite source does not exist: {self.path}")
        self.before_files = self._file_fingerprints()
        wal = self.before_files.get(f"{self.path.name}-wal")
        if wal and int(wal["byte_size"]) > 0:
            raise ValueError(
                f"SQLite source has an uncheckpointed WAL: {self.path}. "
                "Stop the service cleanly and checkpoint/backup before import."
            )
        self.before_sha256 = checksum(self.before_files)

    def _file_fingerprints(self) -> dict[str, dict[str, object]]:
        candidates = (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        )
        return {
            candidate.name: {
                "sha256": file_sha256(candidate),
                "byte_size": candidate.stat().st_size,
            }
            for candidate in candidates
            if candidate.is_file()
        }

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        uri = f"{self.path.as_uri()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        try:
            yield connection
        finally:
            connection.close()

    def tables(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND name != 'alembic_version' ORDER BY name"
            ).fetchall()
        return [str(row["name"]) for row in rows]

    def revision(self) -> str | None:
        with self.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='alembic_version'"
            ).fetchone()
            if exists is None:
                return None
            row = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
        return None if row is None else str(row["version_num"])

    def columns(self, table: str) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                f'PRAGMA table_info("{table}")'
            ).fetchall()
        return [str(row["name"]) for row in rows]

    def rows(self, table: str) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                f'SELECT * FROM "{table}" ORDER BY rowid'
            ).fetchall()
        return [dict(row) for row in rows]

    def count(self, table: str) -> int:
        with self.connect() as connection:
            return int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
            )

    def assert_unchanged(self) -> None:
        after = self._file_fingerprints()
        if after != self.before_files:
            raise RuntimeError(f"SQLite source changed during import: {self.path}")

    def fingerprint(self) -> dict[str, object]:
        return {
            "path_name": self.path.name,
            "sha256": self.before_sha256,
            "files": self.before_files,
            "tables": {
                table: self.count(table)
                for table in self.tables()
            },
        }


def remap_user(value: object, mapping: Mapping[str, str]) -> str:
    source = str(value or "default")
    return mapping.get(source, source)
