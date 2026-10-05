# Data-ingestion volume upgrade

The data-ingestion image stores application code under `/app/app` and mutable
SQLite data under `/app/data`. Docker Compose mounts the `data-ingestion-db`
volume only at `/app/data`, so an image rebuild cannot be hidden by stale source
files in the volume.

## Fresh installation

```bash
cp .env.example .env
python scripts/setup.py
docker compose config
docker compose up --build -d
docker compose ps
```

The first start creates `data_ingestion.db` in the project-scoped
`data-ingestion-db` volume. Compose also uses project-scoped container names.
Set `COMPOSE_PROJECT_NAME` and the `*_PORT` variables when running multiple
deployments on one host.

## Existing installation

The prior layout mounted the same volume at `/app` and stored
`data_ingestion.db` at its root. The new layout mounts that volume root at
`/app/data`, so the existing database becomes
`/app/data/data_ingestion.db` without copying or rewriting it. Old source files
may remain in the volume, but they are not on Python's application path.

Back up the volume before updating:

```bash
mkdir -p backups
docker compose up -d data-ingestion
DATA_CONTAINER="$(docker compose ps -q data-ingestion)"
DATA_VOLUME="$(docker inspect "$DATA_CONTAINER" \
  --format '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{end}}{{end}}')"
docker run --rm \
  --volume "$DATA_VOLUME:/source:ro" \
  --volume "$PWD/backups:/backup" \
  python:3.12-slim \
  python -c "import tarfile; archive=tarfile.open('/backup/data-ingestion-volume.tgz','w:gz'); archive.add('/source', arcname='.'); archive.close()"
docker compose down
```

After updating the repository:

```bash
docker compose config
docker compose up --build -d
docker compose ps
docker compose exec data-ingestion \
  python -c "from pathlib import Path; path=Path('/app/data/data_ingestion.db'); print(path, path.exists(), path.stat().st_size if path.exists() else 0)"
curl --fail http://localhost:${DATA_INGESTION_PORT:-8000}/health
```

Do not use `docker compose down --volumes` during an upgrade.

## Rollback

Stop the new deployment, restore the prior tested release and Compose file, and
start it with the original named volume. If the volume must be restored, empty
only the identified data-ingestion volume and extract the backup:

```bash
docker compose down
docker run --rm \
  --volume "$DATA_VOLUME:/restore" \
  --volume "$PWD/backups:/backup:ro" \
  python:3.12-slim \
  python -c "import pathlib, shutil, tarfile; root=pathlib.Path('/restore'); [shutil.rmtree(p) if p.is_dir() else p.unlink() for p in root.iterdir()]; archive=tarfile.open('/backup/data-ingestion-volume.tgz'); archive.extractall('/restore'); archive.close()"
```

Verify the archive and database on a disposable copy before using this restore
procedure on production data.

## Automated regression

```bash
python scripts/verify_volume_layout.py
```

The check validates the rendered Compose model, seeds an old-layout volume with
stale source and persistent data, rebuilds two distinguishable images, and
proves that code changes while data survives.
