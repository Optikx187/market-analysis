#!/usr/bin/env python3

import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def run(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=capture,
    )
    return result.stdout.strip() if capture else ""


def verify_compose_config() -> None:
    config = json.loads(run("docker", "compose", "config", "--format", "json", capture=True))
    services = config["services"]
    if any("container_name" in service for service in services.values()):
        raise RuntimeError("Compose services must use project-scoped container names")

    mounts = services["data-ingestion"]["volumes"]
    data_mount = next(
        (mount for mount in mounts if mount["source"].endswith("data-ingestion-db")),
        None,
    )
    if not data_mount or data_mount["target"] != "/app/data":
        raise RuntimeError("Data ingestion volume must mount at /app/data")

    environment = services["data-ingestion"]["environment"]
    expected_url = "sqlite+aiosqlite:////app/data/data_ingestion.db"
    if environment.get("DATABASE_URL") != expected_url:
        raise RuntimeError("Data ingestion DATABASE_URL does not use /app/data")


def verify_rebuild_and_persistence() -> None:
    suffix = uuid.uuid4().hex[:12]
    image = f"market-analysis-volume-check:{suffix}"
    volume = f"market-analysis-volume-check-{suffix}"

    with tempfile.TemporaryDirectory(prefix="market-analysis-volume-") as temp_dir:
        context = Path(temp_dir) / "data-ingestion"
        shutil.copytree(ROOT / "services" / "data-ingestion", context)
        marker = context / "app" / "build_marker.txt"

        try:
            run("docker", "volume", "create", volume)
            marker.write_text("image-v1\n")
            run("docker", "build", "--quiet", "--tag", image, str(context))
            run(
                "docker",
                "run",
                "--rm",
                "--mount",
                f"source={volume},target=/legacy",
                image,
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "Path('/legacy/data_ingestion.db').write_text('persisted-state'); "
                    "Path('/legacy/app').mkdir(exist_ok=True); "
                    "Path('/legacy/app/build_marker.txt').write_text('stale-volume-code')"
                ),
            )

            first = run(
                "docker",
                "run",
                "--rm",
                "--mount",
                f"source={volume},target=/app/data",
                image,
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "print(Path('/app/app/build_marker.txt').read_text().strip()); "
                    "print(Path('/app/data/data_ingestion.db').read_text())"
                ),
                capture=True,
            )
            if first.splitlines() != ["image-v1", "persisted-state"]:
                raise RuntimeError(f"Initial image or legacy data verification failed: {first}")

            marker.write_text("image-v2\n")
            run("docker", "build", "--quiet", "--tag", image, str(context))
            second = run(
                "docker",
                "run",
                "--rm",
                "--mount",
                f"source={volume},target=/app/data",
                image,
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "print(Path('/app/app/build_marker.txt').read_text().strip()); "
                    "print(Path('/app/data/data_ingestion.db').read_text())"
                ),
                capture=True,
            )
            if second.splitlines() != ["image-v2", "persisted-state"]:
                raise RuntimeError(f"Rebuild or persistence verification failed: {second}")
        finally:
            subprocess.run(
                ["docker", "image", "rm", "--force", image],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["docker", "volume", "rm", "--force", volume],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )


def main() -> None:
    verify_compose_config()
    verify_rebuild_and_persistence()
    print("Volume layout verification passed")


if __name__ == "__main__":
    main()
