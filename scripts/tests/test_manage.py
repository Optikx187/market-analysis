import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMMANDS = (
    "install",
    "start",
    "stop",
    "restart",
    "status",
    "logs",
    "backup",
    "verify-backup",
    "restore",
    "upgrade",
    "verify",
)

FAKE_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >> "$MOCK_LOG"
if [ "$1" = "compose" ]; then
  printf 'compose-project=%s\\n' "${COMPOSE_PROJECT_NAME:-default}" >> "$MOCK_LOG"
fi
if [ "$1" = "info" ] && [ "${MOCK_DOCKER_INFO_FAIL:-0}" = "1" ]; then
  exit 1
fi
if [ "$1" = "compose" ] && [ "$2" = "ps" ] && [ "$3" = "--services" ]; then
  printf 'data-ingestion\\nportfolio-engine\\n'
fi
if [ "$1" = "compose" ] && [ "$2" = "ps" ] && [ "$3" = "-aq" ]; then
  printf '%s-container\\n' "$4"
fi
if [ "$1" = "inspect" ]; then
  printf '%s\\n' "${2%-container}-volume"
fi
if [ "$1" = "run" ] && [ "${MOCK_ARCHIVE_FAIL:-0}" = "1" ] &&
   printf '%s\\n' "$*" | grep -q 'quant-engine-db.tgz'; then
  exit 1
fi
if [ "$1" = "run" ] && printf '%s\\n' "$*" | grep -q '/tool.py create'; then
  printf '%s\\n' '{"format_version":1,"volumes":[]}'
  exit 0
fi
if [ "$1" = "run" ] && [ "$2" = "--rm" ] && [ "$3" = "python:3.12-slim" ]; then
  cat <<'EOF'
CREDENTIAL_ENCRYPTION_KEYS=mock-fernet-secret
INTERNAL_SERVICE_TOKEN=mock-internal-secret
SETTINGS_OPERATOR_TOKEN=mock-settings-secret
JWT_SECRET=mock-jwt-secret
LIVE_OPERATOR_TOKEN=mock-live-secret
EOF
fi
exit 0
"""

FAKE_GIT = """#!/bin/sh
printf 'git %s\\n' "$*" >> "$MOCK_LOG"
if [ "$1" = "rev-parse" ]; then
  printf 'mock-commit\\n'
fi
exit 0
"""


class ManageCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="manage-test-"))
        self.bin_dir = self.temp_dir / "bin"
        self.bin_dir.mkdir()
        for filename in ("manage", ".env.example", "docker-compose.yml"):
            shutil.copy2(ROOT / filename, self.temp_dir / filename)
        (self.temp_dir / "scripts").mkdir()
        shutil.copy2(
            ROOT / "scripts" / "backup_manifest.py",
            self.temp_dir / "scripts" / "backup_manifest.py",
        )
        (self.temp_dir / "manage").chmod(0o755)
        self._write_executable("docker", FAKE_DOCKER)
        self._write_executable("git", FAKE_GIT)
        self.log_file = self.temp_dir / "commands.log"
        self.environment = {
            **os.environ,
            "PATH": f"{self.bin_dir}{os.pathsep}{os.environ['PATH']}",
            "MOCK_LOG": str(self.log_file),
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir)

    def _write_executable(self, name: str, content: str) -> None:
        path = self.bin_dir / name
        path.write_text(content)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def run_manage(
        self, *arguments: str, extra_env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        environment = {**self.environment, **(extra_env or {})}
        return subprocess.run(
            [str(self.temp_dir / "manage"), *arguments],
            cwd=self.temp_dir,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_every_command_has_help(self) -> None:
        for command in COMMANDS:
            with self.subTest(command=command):
                result = self.run_manage(command, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Usage:", result.stdout)

    def test_install_generates_secrets_without_printing_them(self) -> None:
        result = self.run_manage(
            "install",
            "--base-url",
            "https://market.lab.example",
            "--bind-address",
            "192.0.2.10",
            "--enable-auth",
            "--no-start",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        env_contents = (self.temp_dir / ".env").read_text()
        self.assertIn("PUBLIC_BASE_URL=https://market.lab.example", env_contents)
        self.assertIn("HOST_BIND_ADDRESS=192.0.2.10", env_contents)
        self.assertIn("AUTH_ENABLED=true", env_contents)
        self.assertIn("CREDENTIAL_ENCRYPTION_KEYS=mock-fernet-secret", env_contents)
        self.assertIn("INTERNAL_SERVICE_TOKEN=mock-internal-secret", env_contents)
        self.assertNotIn("mock-fernet-secret", result.stdout)
        self.assertNotIn("mock-internal-secret", result.stdout)
        self.assertEqual(stat.S_IMODE((self.temp_dir / ".env").stat().st_mode), 0o600)
        log = self.log_file.read_text()
        self.assertIn("info", log)
        self.assertIn("compose version", log)
        self.assertIn("compose config --quiet", log)
        self.assertNotIn("compose up", log)

    def test_existing_secrets_are_not_replaced(self) -> None:
        shutil.copy2(self.temp_dir / ".env.example", self.temp_dir / ".env")
        contents = (self.temp_dir / ".env").read_text().replace(
            "INTERNAL_SERVICE_TOKEN=", "INTERNAL_SERVICE_TOKEN=keep-this-token"
        )
        (self.temp_dir / ".env").write_text(contents)

        result = self.run_manage("install", "--no-start")

        self.assertEqual(result.returncode, 0, result.stderr)
        env_contents = (self.temp_dir / ".env").read_text()
        self.assertIn("INTERNAL_SERVICE_TOKEN=keep-this-token", env_contents)
        self.assertNotIn("INTERNAL_SERVICE_TOKEN=mock-internal-secret", env_contents)

    def test_local_url_is_derived_from_frontend_port(self) -> None:
        result = self.run_manage("install", "--no-start")
        self.assertEqual(result.returncode, 0, result.stderr)
        env_path = self.temp_dir / ".env"
        contents = env_path.read_text().replace(
            "FRONTEND_PORT=3000",
            "FRONTEND_PORT=3100",
        )
        env_path.write_text(contents)

        status = self.run_manage("status")

        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("Dashboard: http://localhost:3100", status.stdout)

    def test_non_loopback_bind_requires_auth(self) -> None:
        result = self.run_manage(
            "install",
            "--bind-address",
            "192.0.2.10",
            "--no-start",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AUTH_ENABLED=true is required", result.stderr)

    def test_preflight_failure_is_actionable(self) -> None:
        result = self.run_manage(
            "install",
            "--no-start",
            extra_env={"MOCK_DOCKER_INFO_FAIL": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Start Docker Desktop or the Docker service", result.stderr)

    def test_logs_forwards_service_and_options(self) -> None:
        shutil.copy2(self.temp_dir / ".env.example", self.temp_dir / ".env")

        result = self.run_manage(
            "logs", "portfolio-engine", "--tail", "25", "--follow"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.log_file.read_text()
        self.assertIn(
            "compose logs --tail 25 --follow portfolio-engine",
            log,
        )

    def test_backup_restarts_only_services_that_were_running(self) -> None:
        shutil.copy2(self.temp_dir / ".env.example", self.temp_dir / ".env")
        backup_dir = self.temp_dir / "backup"

        result = self.run_manage("backup", str(backup_dir))

        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.log_file.read_text()
        self.assertIn(
            "compose stop data-ingestion portfolio-engine",
            log,
        )
        self.assertIn(
            "compose start data-ingestion portfolio-engine",
            log,
        )
        self.assertNotIn("compose start quant-engine", log)
        self.assertEqual(stat.S_IMODE(backup_dir.stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((backup_dir / "manifest.json").stat().st_mode),
            0o600,
        )
        self.assertIn("Encryption keys are not included", result.stdout)

    def test_isolated_restore_uses_separate_compose_project(self) -> None:
        shutil.copy2(self.temp_dir / ".env.example", self.temp_dir / ".env")
        backup_dir = self.temp_dir / "backup"
        backup_dir.mkdir()
        for filename in (
            "data-ingestion-db.tgz",
            "quant-engine-db.tgz",
            "portfolio-db.tgz",
            "manifest.json",
        ):
            (backup_dir / filename).touch()

        result = self.run_manage(
            "restore",
            str(backup_dir),
            "--project-name",
            "restore-check",
            "--no-start",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("isolated Compose project 'restore-check'", result.stdout)
        self.assertIn("compose-project=restore-check", self.log_file.read_text())

    def test_restore_requires_explicit_legacy_override(self) -> None:
        shutil.copy2(self.temp_dir / ".env.example", self.temp_dir / ".env")
        backup_dir = self.temp_dir / "legacy-backup"
        backup_dir.mkdir()
        for archive in (
            "data-ingestion-db.tgz",
            "quant-engine-db.tgz",
            "portfolio-db.tgz",
        ):
            (backup_dir / archive).touch()

        refused = self.run_manage("restore", str(backup_dir), "--no-start")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("missing manifest.json", refused.stderr)

        allowed = self.run_manage(
            "restore",
            str(backup_dir),
            "--no-start",
            "--allow-legacy",
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn("legacy backup", allowed.stdout)

    def test_isolated_restore_rejects_active_project_name(self) -> None:
        shutil.copy2(self.temp_dir / ".env.example", self.temp_dir / ".env")
        backup_dir = self.temp_dir / "backup"
        backup_dir.mkdir()
        for archive in (
            "data-ingestion-db.tgz",
            "quant-engine-db.tgz",
            "portfolio-db.tgz",
            "manifest.json",
        ):
            (backup_dir / archive).touch()

        result = self.run_manage(
            "restore",
            str(backup_dir),
            "--project-name",
            self.temp_dir.name,
            "--no-start",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must differ from the active Compose project", result.stderr)

    def test_upgrade_stops_after_failed_backup_without_override(self) -> None:
        shutil.copy2(self.temp_dir / ".env.example", self.temp_dir / ".env")

        result = self.run_manage(
            "upgrade",
            extra_env={"MOCK_ARCHIVE_FAIL": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pre-upgrade backup failed", result.stderr)
        self.assertNotIn("git pull --ff-only", self.log_file.read_text())

    def test_upgrade_can_explicitly_override_failed_backup(self) -> None:
        shutil.copy2(self.temp_dir / ".env.example", self.temp_dir / ".env")

        result = self.run_manage(
            "upgrade",
            "--allow-backup-failure",
            extra_env={"MOCK_ARCHIVE_FAIL": "1"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("explicit operator override", result.stdout)
        self.assertIn("git pull --ff-only", self.log_file.read_text())

    def test_failed_backup_restarts_previous_services(self) -> None:
        shutil.copy2(self.temp_dir / ".env.example", self.temp_dir / ".env")

        result = self.run_manage(
            "backup",
            str(self.temp_dir / "failed-backup"),
            extra_env={"MOCK_ARCHIVE_FAIL": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Previously running services were restarted", result.stderr)
        log = self.log_file.read_text()
        self.assertIn(
            "compose start data-ingestion portfolio-engine",
            log,
        )
        self.assertNotIn("compose start quant-engine", log)

    def test_unknown_command_fails(self) -> None:
        result = self.run_manage("unknown")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown command", result.stderr)


if __name__ == "__main__":
    unittest.main()
