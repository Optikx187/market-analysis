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
    "restore",
    "upgrade",
    "verify",
)

FAKE_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >> "$MOCK_LOG"
if [ "$1" = "info" ] && [ "${MOCK_DOCKER_INFO_FAIL:-0}" = "1" ]; then
  exit 1
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
            "--no-start",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        env_contents = (self.temp_dir / ".env").read_text()
        self.assertIn("PUBLIC_BASE_URL=https://market.lab.example", env_contents)
        self.assertIn("HOST_BIND_ADDRESS=192.0.2.10", env_contents)
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

    def test_unknown_command_fails(self) -> None:
        result = self.run_manage("unknown")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown command", result.stderr)


if __name__ == "__main__":
    unittest.main()
