#!/usr/bin/env python3
import argparse
import hashlib
import json
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


BACKUP_FORMAT_VERSION = 1
ARCHIVES = (
    {
        "service": "data-ingestion",
        "target": "/app/data",
        "archive": "data-ingestion-db.tgz",
    },
    {
        "service": "quant-engine",
        "target": "/app/data",
        "archive": "quant-engine-db.tgz",
    },
    {
        "service": "portfolio-engine",
        "target": "/app/data",
        "archive": "portfolio-db.tgz",
    },
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    seen: set[str] = set()
    for member in members:
        member_path = PurePosixPath(member.name)
        normalized = member_path.as_posix()
        if (
            member_path.is_absolute()
            or ".." in member_path.parts
            or member.issym()
            or member.islnk()
            or member.isdev()
            or member.isfifo()
            or not (member.isfile() or member.isdir())
        ):
            raise ValueError(f"unsafe archive member: {member.name}")
        if normalized in seen:
            raise ValueError(f"duplicate archive member: {member.name}")
        seen.add(normalized)
    return members


def extract_archive(archive_path: Path, destination: Path) -> None:
    with tarfile.open(archive_path) as archive:
        members = safe_members(archive)
        archive.extractall(destination, members=members, filter="data")


def sqlite_inventory(path: Path) -> dict[str, object]:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise ValueError(f"SQLite integrity check failed for {path.name}")
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = connection.execute(
            """
            SELECT name, COALESCE(sql, '')
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        row_counts: dict[str, int] = {}
        schema_digest = hashlib.sha256()
        for table_name, schema_sql in tables:
            schema_digest.update(table_name.encode())
            schema_digest.update(b"\0")
            schema_digest.update(schema_sql.encode())
            schema_digest.update(b"\0")
            quoted_name = table_name.replace('"', '""')
            row_counts[table_name] = connection.execute(
                f'SELECT COUNT(*) FROM "{quoted_name}"'
            ).fetchone()[0]
        return {
            "integrity": "ok",
            "user_version": user_version,
            "schema_sha256": schema_digest.hexdigest(),
            "row_counts": row_counts,
        }
    finally:
        connection.close()


def volume_inventory(root: Path) -> dict[str, object]:
    files: list[dict[str, object]] = []
    databases: dict[str, object] = {}
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative_path = path.relative_to(root).as_posix()
        size = path.stat().st_size
        files.append(
            {
                "path": relative_path,
                "size_bytes": size,
                "sha256": sha256_file(path),
            }
        )
        total_bytes += size
        if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            databases[relative_path] = sqlite_inventory(path)
    return {
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
        "databases": databases,
    }


def archive_inventory(path: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="market-analysis-backup-") as temp_dir:
        root = Path(temp_dir)
        extract_archive(path, root)
        return volume_inventory(root)


def create_manifest(args: argparse.Namespace) -> dict[str, object]:
    backup_dir = Path(args.backup_dir)
    volumes = []
    for spec in ARCHIVES:
        archive_path = backup_dir / spec["archive"]
        if not archive_path.is_file():
            raise ValueError(f"backup is incomplete: missing {spec['archive']}")
        volumes.append(
            {
                **spec,
                "archive_size_bytes": archive_path.stat().st_size,
                "archive_sha256": sha256_file(archive_path),
                "inventory": archive_inventory(archive_path),
            }
        )
    return {
        "format_version": BACKUP_FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "application": {
            "git_commit": args.git_commit,
            "version": args.app_version,
            "compose_project": args.compose_project,
        },
        "credential_recovery": {
            "encryption_key_configured": args.encryption_key_configured,
            "encryption_keys_included": False,
            "warning": (
                "CREDENTIAL_ENCRYPTION_KEYS is not included. Retain every key "
                "needed to decrypt provider credentials for this backup."
            ),
        },
        "volumes": volumes,
    }


def load_manifest(backup_dir: Path) -> dict[str, object]:
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("backup is missing manifest.json")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("format_version") != BACKUP_FORMAT_VERSION:
        raise ValueError(
            f"unsupported backup format version: {manifest.get('format_version')}"
        )
    if not isinstance(manifest.get("volumes"), list):
        raise ValueError("manifest volumes must be a list")
    return manifest


def manifest_volume(
    manifest: dict[str, object], service: str
) -> dict[str, object]:
    matches = [
        volume
        for volume in manifest["volumes"]
        if isinstance(volume, dict) and volume.get("service") == service
    ]
    if len(matches) != 1:
        raise ValueError(f"manifest must contain one {service} volume")
    return matches[0]


def verify_backup(backup_dir: Path) -> tuple[dict[str, object], int, int]:
    manifest = load_manifest(backup_dir)
    database_count = 0
    row_count = 0
    expected_services = {spec["service"] for spec in ARCHIVES}
    if len(manifest["volumes"]) != len(ARCHIVES) or not all(
        isinstance(volume, dict) for volume in manifest["volumes"]
    ):
        raise ValueError("manifest volume count does not match this application")
    actual_services = {
        volume.get("service")
        for volume in manifest["volumes"]
        if isinstance(volume, dict)
    }
    if actual_services != expected_services:
        raise ValueError("manifest service set does not match this application")
    for spec in ARCHIVES:
        volume = manifest_volume(manifest, spec["service"])
        if volume.get("archive") != spec["archive"]:
            raise ValueError(f"unexpected archive for {spec['service']}")
        archive_path = backup_dir / spec["archive"]
        if not archive_path.is_file():
            raise ValueError(f"backup is incomplete: missing {spec['archive']}")
        if archive_path.stat().st_size != volume.get("archive_size_bytes"):
            raise ValueError(f"archive size mismatch: {spec['archive']}")
        if sha256_file(archive_path) != volume.get("archive_sha256"):
            raise ValueError(f"checksum mismatch: {spec['archive']}")
        inventory = archive_inventory(archive_path)
        if inventory != volume.get("inventory"):
            raise ValueError(f"inventory mismatch: {spec['archive']}")
        databases = inventory["databases"]
        database_count += len(databases)
        row_count += sum(
            sum(database["row_counts"].values())
            for database in databases.values()
        )
    return manifest, database_count, row_count


def command_create(args: argparse.Namespace) -> None:
    manifest = create_manifest(args)
    print(json.dumps(manifest, indent=2, sort_keys=True))


def command_verify(args: argparse.Namespace) -> None:
    manifest, database_count, row_count = verify_backup(Path(args.backup_dir))
    print(
        "Verified backup format "
        f"{manifest['format_version']}: {len(manifest['volumes'])} archives, "
        f"{database_count} SQLite databases, {row_count} rows."
    )


def command_verify_volume(args: argparse.Namespace) -> None:
    manifest = load_manifest(Path(args.backup_dir))
    expected = manifest_volume(manifest, args.service)["inventory"]
    actual = volume_inventory(Path(args.volume_root))
    if actual != expected:
        raise ValueError(f"restored volume inventory mismatch: {args.service}")
    print(f"Verified restored volume: {args.service}.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--backup-dir", required=True)
    create.add_argument("--git-commit", required=True)
    create.add_argument("--app-version", required=True)
    create.add_argument("--compose-project", required=True)
    create.add_argument(
        "--encryption-key-configured",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    create.set_defaults(handler=command_create)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--backup-dir", required=True)
    verify.set_defaults(handler=command_verify)

    verify_volume = subparsers.add_parser("verify-volume")
    verify_volume.add_argument("--backup-dir", required=True)
    verify_volume.add_argument("--volume-root", required=True)
    verify_volume.add_argument("--service", required=True)
    verify_volume.set_defaults(handler=command_verify_volume)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.handler(args)
    except (
        json.JSONDecodeError,
        KeyError,
        OSError,
        sqlite3.DatabaseError,
        TypeError,
        ValueError,
    ) as exc:
        raise SystemExit(f"Backup verification failed: {exc}") from exc


if __name__ == "__main__":
    main()
