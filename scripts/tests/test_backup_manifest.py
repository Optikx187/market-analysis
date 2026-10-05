import io
import json
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts" / "backup_manifest.py"
ARCHIVES = (
    ("data-ingestion", "data-ingestion-db.tgz", "data_ingestion.db"),
    ("quant-engine", "quant-engine-db.tgz", "backtests.db"),
    ("portfolio-engine", "portfolio-db.tgz", "portfolio.db"),
)


class BackupManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="backup-manifest-test-"))
        self.backup_dir = self.temp_dir / "backup"
        self.backup_dir.mkdir()
        for index, (_, archive_name, database_name) in enumerate(ARCHIVES, start=1):
            source_dir = self.temp_dir / f"source-{index}"
            source_dir.mkdir()
            database_path = source_dir / database_name
            connection = sqlite3.connect(database_path)
            connection.execute("PRAGMA user_version = 7")
            connection.execute(
                "CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO records (value) VALUES (?)",
                [(f"value-{row}",) for row in range(index)],
            )
            connection.commit()
            connection.close()
            with tarfile.open(self.backup_dir / archive_name, "w:gz") as archive:
                archive.add(source_dir, arcname=".")

    def tearDown(self) -> None:
        for path in sorted(self.temp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        self.temp_dir.rmdir()

    def run_tool(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(TOOL), *arguments],
            text=True,
            capture_output=True,
            check=False,
        )

    def create_manifest(self) -> dict[str, object]:
        result = self.run_tool(
            "create",
            "--backup-dir",
            str(self.backup_dir),
            "--git-commit",
            "abc123",
            "--app-version",
            "v1-test",
            "--compose-project",
            "market-test",
            "--encryption-key-configured",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)
        (self.backup_dir / "manifest.json").write_text(result.stdout)
        return manifest

    def test_manifest_records_checksums_schemas_and_row_counts(self) -> None:
        manifest = self.create_manifest()

        self.assertEqual(manifest["format_version"], 1)
        self.assertEqual(manifest["application"]["git_commit"], "abc123")
        self.assertTrue(
            manifest["credential_recovery"]["encryption_key_configured"]
        )
        self.assertFalse(
            manifest["credential_recovery"]["encryption_keys_included"]
        )
        self.assertEqual(len(manifest["volumes"]), 3)
        total_rows = 0
        for volume in manifest["volumes"]:
            self.assertEqual(len(volume["archive_sha256"]), 64)
            self.assertEqual(volume["inventory"]["file_count"], 1)
            database = next(iter(volume["inventory"]["databases"].values()))
            self.assertEqual(database["integrity"], "ok")
            self.assertEqual(database["user_version"], 7)
            self.assertEqual(len(database["schema_sha256"]), 64)
            total_rows += database["row_counts"]["records"]
        self.assertEqual(total_rows, 6)

        verification = self.run_tool(
            "verify", "--backup-dir", str(self.backup_dir)
        )
        self.assertEqual(verification.returncode, 0, verification.stderr)
        self.assertIn("3 SQLite databases, 6 rows", verification.stdout)

    def test_checksum_tampering_is_rejected(self) -> None:
        self.create_manifest()
        archive_path = self.backup_dir / "quant-engine-db.tgz"
        with archive_path.open("ab") as stream:
            stream.write(b"tampered")
        manifest_path = self.backup_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        quant_volume = next(
            volume
            for volume in manifest["volumes"]
            if volume["service"] == "quant-engine"
        )
        quant_volume["archive_size_bytes"] = archive_path.stat().st_size
        manifest_path.write_text(json.dumps(manifest))

        verification = self.run_tool(
            "verify", "--backup-dir", str(self.backup_dir)
        )

        self.assertNotEqual(verification.returncode, 0)
        self.assertIn("checksum mismatch", verification.stderr)

    def test_unsafe_archive_member_is_rejected(self) -> None:
        unsafe_archive = self.backup_dir / "data-ingestion-db.tgz"
        with tarfile.open(unsafe_archive, "w:gz") as archive:
            member = tarfile.TarInfo("../outside")
            payload = b"unsafe"
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))

        result = self.run_tool(
            "create",
            "--backup-dir",
            str(self.backup_dir),
            "--git-commit",
            "abc123",
            "--app-version",
            "v1-test",
            "--compose-project",
            "market-test",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe archive member", result.stderr)

    def test_corrupt_sqlite_database_is_rejected(self) -> None:
        source_dir = self.temp_dir / "corrupt-source"
        source_dir.mkdir()
        (source_dir / "data_ingestion.db").write_bytes(b"not a sqlite database")
        with tarfile.open(
            self.backup_dir / "data-ingestion-db.tgz", "w:gz"
        ) as archive:
            archive.add(source_dir, arcname=".")

        result = self.run_tool(
            "create",
            "--backup-dir",
            str(self.backup_dir),
            "--git-commit",
            "abc123",
            "--app-version",
            "v1-test",
            "--compose-project",
            "market-test",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("file is not a database", result.stderr)

    def test_restored_volume_reconciles_with_manifest(self) -> None:
        self.create_manifest()
        volume_root = self.temp_dir / "restored"
        volume_root.mkdir()
        with tarfile.open(self.backup_dir / "portfolio-db.tgz") as archive:
            archive.extractall(volume_root, filter="data")

        verification = self.run_tool(
            "verify-volume",
            "--backup-dir",
            str(self.backup_dir),
            "--volume-root",
            str(volume_root),
            "--service",
            "portfolio-engine",
        )

        self.assertEqual(verification.returncode, 0, verification.stderr)
        self.assertIn("portfolio-engine", verification.stdout)

        database_path = volume_root / "portfolio.db"
        connection = sqlite3.connect(database_path)
        connection.execute(
            "INSERT INTO records (value) VALUES ('unexpected-restored-row')"
        )
        connection.commit()
        connection.close()
        changed_volume = self.run_tool(
            "verify-volume",
            "--backup-dir",
            str(self.backup_dir),
            "--volume-root",
            str(volume_root),
            "--service",
            "portfolio-engine",
        )
        self.assertNotEqual(changed_volume.returncode, 0)
        self.assertIn("inventory mismatch", changed_volume.stderr)


if __name__ == "__main__":
    unittest.main()
