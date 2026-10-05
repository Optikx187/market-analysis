import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("docker"), "Docker is required")
class DeploymentProfileTest(unittest.TestCase):
    def render(self, profile: str, *, enable_profile: bool = False) -> dict:
        command = [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            f"deploy/compose.{profile}.yml",
        ]
        if enable_profile:
            command.extend(["--profile", profile])
        command.extend(["config", "--format", "json"])
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def assert_edge_only(self, model: dict) -> None:
        published = {
            name: service.get("ports", [])
            for name, service in model["services"].items()
            if service.get("ports")
        }
        self.assertEqual(set(published), {"frontend"})
        self.assertEqual(published["frontend"][0]["target"], 80)

    def test_single_node_is_edge_only(self) -> None:
        model = self.render("single-node")
        self.assert_edge_only(model)
        for name in (
            "data-ingestion",
            "quant-engine",
            "portfolio-engine",
            "notification-gateway",
        ):
            self.assertEqual(
                model["services"][name]["environment"]["DEPLOYMENT_PROFILE"],
                "single-node",
            )

    def test_sandbox_is_isolated_and_offline_safe(self) -> None:
        model = self.render("sandbox")
        self.assert_edge_only(model)
        services = model["services"]
        self.assertEqual(services["data-ingestion"]["environment"]["MARKET_DATA_MODE"], "fake")
        self.assertEqual(
            services["notification-gateway"]["environment"]["NOTIFICATION_MODE"],
            "fake",
        )
        self.assertEqual(
            services["portfolio-engine"]["environment"]["LIVE_TRADING_ENABLED"],
            "false",
        )
        self.assertTrue(
            services["data-ingestion"]["environment"]["BINANCE_REST_URL"].startswith(
                "https://binance.invalid/"
            )
        )
        volume_sources = {
            volume["source"]
            for name in ("data-ingestion", "quant-engine", "portfolio-engine")
            for volume in services[name].get("volumes", [])
            if volume["type"] == "volume"
        }
        self.assertEqual(
            volume_sources,
            {
                "sandbox-data-ingestion-db",
                "sandbox-quant-engine-db",
                "sandbox-portfolio-db",
            },
        )

    def test_multi_worker_adds_private_stateless_workers(self) -> None:
        model = self.render("multi-worker", enable_profile=True)
        self.assert_edge_only(model)
        worker = model["services"]["quant-worker"]
        self.assertFalse(worker.get("ports"))
        self.assertEqual(worker["environment"]["SCAN_ENABLED"], "false")
        self.assertEqual(worker["environment"]["BACKTEST_DATABASE_PATH"], "/tmp/backtests.db")
        self.assertEqual(worker["deploy"]["replicas"], 2)


if __name__ == "__main__":
    unittest.main()
