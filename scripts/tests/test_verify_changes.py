import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts import verify_changes


ROOT = Path(__file__).resolve().parents[2]


class ClassifierTest(unittest.TestCase):
    def test_service_source_selects_only_its_test_tier(self) -> None:
        self.assertEqual(
            verify_changes.classify_paths(
                ["services/data-ingestion/app/main.py"]
            ),
            ["repository", "data-ingestion"],
        )

    def test_dependency_changes_add_the_matching_image_build(self) -> None:
        self.assertEqual(
            verify_changes.classify_paths(
                [
                    "services/frontend/package-lock.json",
                    "services/portfolio-engine/requirements.txt",
                ]
            ),
            [
                "repository",
                "portfolio-engine",
                "frontend",
                "image-portfolio-engine",
                "image-frontend",
            ],
        )

    def test_shared_schema_change_expands_to_all_services_and_compose(self) -> None:
        self.assertEqual(
            verify_changes.classify_paths(["schemas/events/v1.json"]),
            [
                "repository",
                "data-ingestion",
                "quant-engine",
                "portfolio-engine",
                "notification-gateway",
                "frontend",
                "compose",
            ],
        )

    def test_migration_change_expands_to_all_backends_and_compose(self) -> None:
        self.assertEqual(
            verify_changes.classify_paths(
                ["services/portfolio-engine/migrations/0001.sql"]
            ),
            [
                "repository",
                "data-ingestion",
                "quant-engine",
                "portfolio-engine",
                "notification-gateway",
                "compose",
            ],
        )

    def test_compose_docs_and_management_paths_map_to_documented_jobs(self) -> None:
        self.assertEqual(
            verify_changes.classify_paths(
                [
                    "deploy/compose.sandbox.yml",
                    "docs/operator-management.md",
                    "verify-changes.ps1",
                ]
            ),
            ["repository", "management", "compose", "docs"],
        )
        self.assertEqual(
            verify_changes.classify_paths(["scripts/ci_sandbox_smoke.sh"]),
            ["repository", "management", "compose"],
        )
        self.assertEqual(
            verify_changes.classify_paths(["scripts/verify_changes.py"]),
            list(verify_changes.CORE_JOBS),
        )

    def test_ambiguous_path_uses_broader_core_check_set(self) -> None:
        self.assertEqual(
            verify_changes.classify_paths(["unexpected-root-config.toml"]),
            list(verify_changes.CORE_JOBS),
        )

    def test_ci_plan_keeps_documentation_changes_lightweight(self) -> None:
        selected = verify_changes.classify_paths(["docs/operator-management.md"])

        self.assertEqual(
            verify_changes.ci_plan(selected),
            {
                "backend_services": [],
                "image_services": [],
                "run_frontend": False,
                "run_management": False,
                "run_compose": False,
                "run_docs": True,
                "high_risk": False,
            },
        )

    def test_ci_plan_marks_dependency_and_shared_changes_high_risk(self) -> None:
        dependency_plan = verify_changes.ci_plan(
            verify_changes.classify_paths(
                ["services/frontend/package-lock.json"]
            )
        )
        shared_plan = verify_changes.ci_plan(
            verify_changes.classify_paths(["contracts/events/v1.json"])
        )

        self.assertEqual(dependency_plan["image_services"], ["frontend"])
        self.assertTrue(dependency_plan["run_frontend"])
        self.assertTrue(dependency_plan["high_risk"])
        self.assertEqual(
            shared_plan["backend_services"],
            list(verify_changes.BACKEND_SERVICES),
        )
        self.assertTrue(shared_plan["run_compose"])
        self.assertTrue(shared_plan["high_risk"])

    def test_representative_hosted_ci_path_matrix(self) -> None:
        cases = {
            "docs/operations.md": {
                "backend_services": [],
                "image_services": [],
                "run_docs": True,
                "high_risk": False,
            },
            "services/quant-engine/app/main.py": {
                "backend_services": ["quant-engine"],
                "image_services": [],
                "run_docs": False,
                "high_risk": False,
            },
            "services/portfolio-engine/requirements.txt": {
                "backend_services": ["portfolio-engine"],
                "image_services": ["portfolio-engine"],
                "run_docs": False,
                "high_risk": True,
            },
            "services/frontend/package-lock.json": {
                "backend_services": [],
                "image_services": ["frontend"],
                "run_docs": False,
                "high_risk": True,
            },
            "schemas/events/v1.json": {
                "backend_services": list(verify_changes.BACKEND_SERVICES),
                "image_services": [],
                "run_docs": False,
                "high_risk": True,
            },
            ".github/workflows/verification.yml": {
                "backend_services": list(verify_changes.BACKEND_SERVICES),
                "image_services": [],
                "run_docs": False,
                "high_risk": True,
            },
        }

        for path, expected in cases.items():
            with self.subTest(path=path):
                plan = verify_changes.ci_plan(
                    verify_changes.classify_paths([path])
                )
                for key, value in expected.items():
                    self.assertEqual(plan[key], value)


class JobCatalogTest(unittest.TestCase):
    def test_each_named_service_has_a_portable_command(self) -> None:
        catalog = verify_changes.job_catalog("origin/master")
        for service in verify_changes.SERVICE_NAMES:
            with self.subTest(service=service):
                selected = verify_changes.service_jobs(service)
                self.assertEqual(selected[0], "repository")
                self.assertIn(service, selected)
                self.assertTrue(catalog[service].commands)
                for job_command in catalog[service].commands:
                    self.assertFalse(job_command.argv[0].startswith("/bin/"))

    def test_all_execution_commands_receive_sandbox_boundaries(self) -> None:
        job = verify_changes.Job(
            name="environment",
            description="Validate safe environment.",
            commands=(
                verify_changes.command(
                    verify_changes.PYTHON,
                    "-c",
                    (
                        "import os; "
                        "assert os.environ['MARKET_DATA_MODE'] == 'fake'; "
                        "assert os.environ['NOTIFICATION_MODE'] == 'fake'; "
                        "assert os.environ['LIVE_TRADING_ENABLED'] == 'false'; "
                        "assert os.environ['ALPACA_TRADING_URL'].endswith('.invalid')"
                    ),
                ),
            ),
        )

        results, success = verify_changes.execute_jobs([job], dry_run=False)

        self.assertTrue(success)
        self.assertEqual(results[0]["status"], "passed")

    def test_failed_command_is_reported_without_running_later_commands(self) -> None:
        job = verify_changes.Job(
            name="failure",
            description="Exercise failure reporting.",
            commands=(
                verify_changes.command(
                    verify_changes.PYTHON,
                    "-c",
                    "import sys; print('expected failure', file=sys.stderr); sys.exit(7)",
                ),
                verify_changes.command(
                    verify_changes.PYTHON,
                    "-c",
                    "raise AssertionError('must not run')",
                ),
            ),
        )

        with contextlib.redirect_stderr(io.StringIO()):
            results, success = verify_changes.execute_jobs([job], dry_run=False)

        self.assertFalse(success)
        self.assertEqual(results[0]["status"], "failed")
        commands = results[0]["commands"]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["exit_code"], 7)
        self.assertIn("expected failure", commands[0]["stderr_tail"])


class ReportAndEntryPointTest(unittest.TestCase):
    def test_full_dry_run_emits_valid_json_and_junit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            json_path = temporary_path / "summary.json"
            junit_path = temporary_path / "junit.xml"
            result = subprocess.run(
                [
                    str(ROOT / "verify-changes"),
                    "full",
                    "--dry-run",
                    "--json",
                    str(json_path),
                    "--junit",
                    str(junit_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(report["mode"], "full")
            self.assertTrue(report["dry_run"])
            self.assertEqual(report["selected_jobs"], list(verify_changes.FULL_JOBS))
            self.assertEqual(
                report["ci_plan"]["backend_services"],
                list(verify_changes.BACKEND_SERVICES),
            )
            self.assertTrue(report["ci_plan"]["high_risk"])
            self.assertTrue(report["success"])
            self.assertEqual(
                {job["status"] for job in report["jobs"]},
                {"planned"},
            )

            suite = ET.parse(junit_path).getroot()
            self.assertEqual(suite.tag, "testsuite")
            self.assertEqual(int(suite.attrib["tests"]), len(verify_changes.FULL_JOBS))
            self.assertEqual(
                int(suite.attrib["skipped"]),
                len(verify_changes.FULL_JOBS),
            )
            self.assertEqual(len(suite.findall("testcase")), len(verify_changes.FULL_JOBS))

    def test_changed_mode_reports_verification_files(self) -> None:
        result = subprocess.run(
            [str(ROOT / "verify-changes"), "changed", "--dry-run"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        selected_line = next(
            line for line in result.stdout.splitlines() if line.startswith("Selected jobs:")
        )
        self.assertIn("repository", selected_line)
        self.assertIn("management", selected_line)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is not installed")
    def test_powershell_entry_point_runs_full_dry_run(self) -> None:
        result = subprocess.run(
            [
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-File",
                str(ROOT / "verify-changes.ps1"),
                "full",
                "--dry-run",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Mode: full", result.stdout)


if __name__ == "__main__":
    unittest.main()
