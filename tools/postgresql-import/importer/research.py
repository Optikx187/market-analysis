from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from importer.common import (
    ReadOnlySQLite,
    canonical_json,
    checksum,
    source_key,
    utc_datetime,
)
from importer.report import ImportReport
from importer.target import Target


def _add_years(value: datetime, years: int) -> datetime:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(month=2, day=28, year=value.year + years)


def _json_object(value: object, label: str) -> dict[str, object]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    if not isinstance(parsed, dict):
        raise TypeError(f"{label} must contain a JSON object")
    return parsed


def _json_array(value: object, label: str) -> list[object]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    if not isinstance(parsed, list):
        raise TypeError(f"{label} must contain a JSON array")
    return parsed


def _preserved_uuid(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


def _source_artifact(
    reference: dict[str, object],
    source_root: Path,
) -> Path:
    uri = str(reference.get("uri", ""))
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"research artifact URI must use file://: {uri}")
    filename = Path(unquote(parsed.path)).name
    if not filename:
        raise ValueError(f"research artifact URI has no filename: {uri}")
    expected_sha256 = str(reference.get("sha256", ""))
    expected_size = int(reference.get("byte_size", -1))
    matches = [
        candidate.resolve()
        for candidate in sorted(source_root.resolve().rglob(filename))
        if candidate.is_file()
        and candidate.stat().st_size == expected_size
        and hashlib.sha256(candidate.read_bytes()).hexdigest()
        == expected_sha256
    ]
    if len(matches) != 1:
        raise ValueError(
            f"research artifact must resolve to exactly one verified "
            f"source-root file: {uri}"
        )
    return matches[0]


def _copy_artifact(
    source_path: Path,
    target_root: Path,
    target_name: str,
    expected_sha256: str,
    expected_size: int,
) -> tuple[str, int]:
    payload = source_path.read_bytes()
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"research artifact checksum mismatch: {source_path.name}"
        )
    if len(payload) != expected_size:
        raise ValueError(f"research artifact size mismatch: {source_path.name}")
    target_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = target_root / target_name
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, destination)
    destination.chmod(0o600)
    return destination.name, len(payload)


def import_research(
    source: ReadOnlySQLite,
    target: Target,
    report: ImportReport,
    *,
    source_artifact_root: Path,
    target_artifact_root: Path,
    runtime_artifact_root: str,
    user_map: dict[str, str],
) -> None:
    store = "research"
    if "backtest_runs" not in source.tables():
        report.errors.append("Research source is missing backtest_runs")
        return
    rows = source.rows("backtest_runs")
    artifact_rows = (
        source.rows("backtest_artifacts")
        if "backtest_artifacts" in source.tables()
        else []
    )
    artifact_by_key = {
        (str(row["run_id"]), str(row["artifact_key"])): row
        for row in artifact_rows
    }
    used_artifact_keys: set[tuple[str, str]] = set()
    table_report = report.table(store, "backtest_runs")
    table_report.source_rows = len(rows)
    table_report.source_checksum = checksum(rows)
    target_rows: list[dict[str, object]] = []
    artifact_report = report.table(store, "backtest_artifacts")
    artifact_report.source_rows = len(artifact_rows)
    artifact_report.source_checksum = checksum(artifact_rows)
    target_artifact_rows: list[dict[str, object]] = []
    for row in rows:
        artifact_destinations: list[Path] = []
        try:
            source_id = str(row["run_id"])
            created_at, assumed_utc = utc_datetime(row["created_at"])
            if assumed_utc:
                report.warnings.append(
                    f"research.backtest_runs {source_id} timestamp assumed UTC"
                )
            request_json = _json_object(row["request_json"], "request_json")
            result_json = _json_object(row["result_json"], "result_json")
            lineage_json = _json_object(
                row.get("input_lineage_json", "{}"),
                "input_lineage_json",
            )
            manifest = _json_array(
                row.get("artifact_manifest_json", "[]"),
                "artifact_manifest_json",
            )
            row_checksum = checksum(row)
            target_id = target.mapping(
                source_store=store,
                source_table="backtest_runs",
                source_key=source_key(source_id),
                source_row_checksum=row_checksum,
                target_schema="research",
                target_table="backtest_runs",
                preserve_uuid=_preserved_uuid(source_id),
            )
            result_json["run_id"] = target_id
            result_json["created_at"] = created_at.isoformat()
            rewritten_manifest: list[dict[str, object]] = []
            artifact_target_ids: list[str] = []
            for index, item in enumerate(manifest):
                if not isinstance(item, dict):
                    raise TypeError("artifact manifest entry must be an object")
                source_path = _source_artifact(item, source_artifact_root)
                expected_sha256 = str(item.get("sha256", ""))
                expected_size = int(item.get("byte_size", -1))
                artifact_key = str(item.get("artifact_key", ""))
                source_artifact_row = artifact_by_key.get(
                    (source_id, artifact_key)
                )
                if artifact_rows and source_artifact_row is None:
                    raise ValueError(
                        f"artifact table is missing {source_id}/{artifact_key}"
                    )
                if source_artifact_row is not None:
                    used_artifact_keys.add((source_id, artifact_key))
                    if (
                        str(source_artifact_row["sha256"]) != expected_sha256
                        or int(source_artifact_row["byte_size"])
                        != expected_size
                    ):
                        raise ValueError(
                            f"artifact table conflicts with manifest: "
                            f"{source_id}/{artifact_key}"
                        )
                if (
                    hashlib.sha256(source_path.read_bytes()).hexdigest()
                    != expected_sha256
                    or source_path.stat().st_size != expected_size
                ):
                    raise ValueError(
                        f"artifact evidence does not match: {source_path.name}"
                    )
                suffix = source_path.suffix or ".json"
                target_name = f"{target_id}-{index}{suffix}"
                runtime_uri = (
                    f"file://{runtime_artifact_root.rstrip('/')}/{target_name}"
                )
                rewritten = {
                    **item,
                    "uri": runtime_uri,
                }
                rewritten_manifest.append(rewritten)
                artifact_source_key = (
                    source_artifact_row["id"]
                    if source_artifact_row is not None
                    else {"run_id": source_id, "artifact_key": artifact_key}
                )
                artifact_target_ids.append(
                    target.mapping(
                        source_store=store,
                        source_table="backtest_artifacts",
                        source_key=source_key(artifact_source_key),
                        source_row_checksum=checksum(
                            source_artifact_row or item
                        ),
                        target_schema="research",
                        target_table="backtest_artifacts",
                    )
                )
                if target.mode == "apply":
                    _copy_artifact(
                        source_path,
                        target_artifact_root,
                        target_name,
                        expected_sha256,
                        expected_size,
                    )
                    artifact_destinations.append(
                        target_artifact_root / target_name
                    )
            eligible = bool(row["alert_eligible"])
            retention_class = str(
                row.get("retention_class")
                or (
                    "recommendation-evidence"
                    if eligible
                    else "ad-hoc-research"
                )
            )
            retain_value = row.get("retain_until")
            retain_until = (
                utc_datetime(retain_value)[0]
                if retain_value
                else _add_years(created_at, 7 if eligible else 2)
            )
            user_key = user_map.get(
                str(row.get("user_key") or "default"),
                str(row.get("user_key") or "default"),
            )
            target_row = {
                "run_id": target_id,
                "tenant_id": target.tenant_id,
                "user_key": user_key,
                "ticker": str(row["ticker"]).strip().upper(),
                "module_key": str(
                    row.get("module_key") or "walk-forward-backtest"
                ),
                "module_version": str(
                    row.get("module_version") or "legacy-v1"
                ),
                "strategy_version": str(row["strategy_version"]),
                "created_at": created_at,
                "status": str(row.get("status") or "completed"),
                "request_json": canonical_json(request_json),
                "result_json": canonical_json(result_json),
                "input_lineage_json": canonical_json(lineage_json),
                "artifact_manifest_json": canonical_json(rewritten_manifest),
                "alert_eligible": eligible,
                "retention_class": retention_class,
                "retain_until": retain_until,
            }
            target_rows.append(target_row)
            existing = (
                target.fetch_by_id(
                    "research", "backtest_runs", "run_id", target_id
                )
                if target.mode == "apply"
                else None
            )
            if existing is not None:
                if checksum(existing["result_json"]) != checksum(result_json):
                    raise ValueError(
                        f"target result conflicts for run {source_id}"
                    )
                table_report.skipped_rows += 1
                for item, artifact_id in zip(
                    rewritten_manifest,
                    artifact_target_ids,
                    strict=True,
                ):
                    existing_artifact = target.fetch_by_id(
                        "research",
                        "backtest_artifacts",
                        "id",
                        artifact_id,
                    )
                    target_artifact_rows.append(
                        {
                            "id": artifact_id,
                            "run_id": target_id,
                            "artifact_key": item["artifact_key"],
                            "sha256": item["sha256"],
                            "byte_size": item["byte_size"],
                        }
                    )
                    if existing_artifact is None:
                        raise ValueError(
                            f"target artifact is missing for {artifact_id}"
                        )
                    if (
                        str(existing_artifact["sha256"])
                        != str(item["sha256"])
                        or int(existing_artifact["byte_size"])
                        != int(item["byte_size"])
                    ):
                        raise ValueError(
                            f"target artifact conflicts for {artifact_id}"
                        )
                    artifact_report.skipped_rows += 1
            elif target.mode == "apply":
                target.connection.execute(
                    """
                    INSERT INTO research.backtest_runs (
                        run_id, tenant_id, user_key, ticker, module_key,
                        module_version, strategy_version, created_at, status,
                        request_json, result_json, input_lineage_json,
                        artifact_manifest_json, alert_eligible,
                        retention_class, retain_until
                    ) VALUES (
                        %(run_id)s, %(tenant_id)s, %(user_key)s, %(ticker)s,
                        %(module_key)s, %(module_version)s,
                        %(strategy_version)s, %(created_at)s, %(status)s,
                        %(request_json)s::jsonb, %(result_json)s::jsonb,
                        %(input_lineage_json)s::jsonb,
                        %(artifact_manifest_json)s::jsonb,
                        %(alert_eligible)s, %(retention_class)s,
                        %(retain_until)s
                    )
                    """,
                    target_row,
                )
                persisted_run = target.fetch_by_id(
                    "research",
                    "backtest_runs",
                    "run_id",
                    target_id,
                )
                if (
                    persisted_run is None
                    or checksum(persisted_run["result_json"])
                    != checksum(result_json)
                ):
                    raise ValueError(
                        f"persisted result differs for run {source_id}"
                    )
                for item, artifact_id in zip(
                    rewritten_manifest,
                    artifact_target_ids,
                    strict=True,
                ):
                    existing_artifact = target.fetch_by_id(
                        "research",
                        "backtest_artifacts",
                        "id",
                        artifact_id,
                    )
                    target_artifact_rows.append(
                        {
                            "id": artifact_id,
                            "run_id": target_id,
                            "artifact_key": item["artifact_key"],
                            "sha256": item["sha256"],
                            "byte_size": item["byte_size"],
                        }
                    )
                    if existing_artifact is not None:
                        if (
                            str(existing_artifact["sha256"])
                            != str(item["sha256"])
                            or int(existing_artifact["byte_size"])
                            != int(item["byte_size"])
                        ):
                            raise ValueError(
                                f"target artifact conflicts for {artifact_id}"
                            )
                        artifact_report.skipped_rows += 1
                        continue
                    target.connection.execute(
                        """
                        INSERT INTO research.backtest_artifacts (
                            id, tenant_id, user_key, run_id, artifact_key,
                            media_type, uri, sha256, byte_size, created_at,
                            retain_until
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            artifact_id,
                            target.tenant_id,
                            user_key,
                            target_id,
                            str(item["artifact_key"]),
                            str(item["media_type"]),
                            str(item["uri"]),
                            str(item["sha256"]),
                            int(item["byte_size"]),
                            created_at,
                            retain_until,
                        ),
                    )
                    persisted_artifact = target.fetch_by_id(
                        "research",
                        "backtest_artifacts",
                        "id",
                        artifact_id,
                    )
                    if (
                        persisted_artifact is None
                        or str(persisted_artifact["sha256"])
                        != str(item["sha256"])
                        or int(persisted_artifact["byte_size"])
                        != int(item["byte_size"])
                    ):
                        raise ValueError(
                            f"persisted artifact differs for {artifact_id}"
                        )
                    artifact_report.imported_rows += 1
                table_report.imported_rows += 1
            else:
                table_report.imported_rows += 1
                artifact_report.imported_rows += len(rewritten_manifest)
                target_artifact_rows.extend(
                    {
                        "id": artifact_id,
                        "run_id": target_id,
                        "artifact_key": item["artifact_key"],
                        "sha256": item["sha256"],
                        "byte_size": item["byte_size"],
                    }
                    for item, artifact_id in zip(
                        rewritten_manifest,
                        artifact_target_ids,
                        strict=True,
                    )
                )
            target.commit()
        except (KeyError, TypeError, ValueError) as error:
            for path in artifact_destinations:
                path.unlink(missing_ok=True)
            target.rollback()
            table_report.conflict_rows += 1
            report.errors.append(
                f"research.backtest_runs {row.get('run_id')}: {error}"
            )
    table_report.target_checksum = checksum(target_rows)
    unused_artifacts = set(artifact_by_key) - used_artifact_keys
    if unused_artifacts:
        artifact_report.conflict_rows += len(unused_artifacts)
        report.errors.append(
            "research.backtest_artifacts has entries absent from run manifests: "
            + ", ".join(
                f"{run_id}/{artifact_key}"
                for run_id, artifact_key in sorted(unused_artifacts)
            )
        )
    artifact_report.target_checksum = checksum(target_artifact_rows)
    if target.mode == "apply":
        run_count = target.count("research", "backtest_runs")
        artifact_count = target.count("research", "backtest_artifacts")
        if run_count != len(rows):
            report.errors.append(
                "research.backtest_runs target count mismatch: "
                f"source={len(rows)}, target={run_count}"
            )
        if artifact_count != len(artifact_rows):
            report.errors.append(
                "research.backtest_artifacts target count mismatch: "
                f"source={len(artifact_rows)}, target={artifact_count}"
            )
    target.update_progress(
        source_store=store,
        source_table="backtest_runs",
        status="failed" if table_report.conflict_rows else "completed",
        last_source_key=(
            source_key(rows[-1]["run_id"]) if rows else None
        ),
        source_rows=table_report.source_rows,
        imported_rows=table_report.imported_rows,
        skipped_rows=table_report.skipped_rows,
        conflict_rows=table_report.conflict_rows,
        source_checksum=table_report.source_checksum,
        target_checksum=table_report.target_checksum,
    )
    target.update_progress(
        source_store=store,
        source_table="backtest_artifacts",
        status="failed" if artifact_report.conflict_rows else "completed",
        last_source_key=(
            source_key(artifact_rows[-1]["id"]) if artifact_rows else None
        ),
        source_rows=artifact_report.source_rows,
        imported_rows=artifact_report.imported_rows,
        skipped_rows=artifact_report.skipped_rows,
        conflict_rows=artifact_report.conflict_rows,
        source_checksum=artifact_report.source_checksum,
        target_checksum=artifact_report.target_checksum,
    )
    target.commit()
