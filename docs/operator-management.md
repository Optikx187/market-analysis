# Operator management commands

The supported local and remote-lab workflow is exposed through:

- Linux and macOS: `./manage`
- Windows Docker Desktop: `.\manage.ps1`

The commands use Docker Compose v2, preserve project-scoped named volumes, and
do not require host Python, Node.js, or provider credentials. Docker may pull
`python:3.12-slim` to generate secrets and create volume archives.

## Prerequisites

- Git
- Docker Engine with the Compose v2 plugin, or Docker Desktop using Linux
  containers
- Internet access for the initial image build and optional market providers
- A checkout of this repository

Windows operators can run `manage.ps1` from the built-in Windows PowerShell or
PowerShell 7. The script limits `.env` access to the current Windows user.
Linux and macOS use mode `0600`.

## Fresh installation

Linux or macOS:

```bash
git clone https://github.com/Optikx187/market-analysis.git
cd market-analysis
./manage install
```

Windows PowerShell:

```powershell
git clone https://github.com/Optikx187/market-analysis.git
Set-Location market-analysis
.\manage.ps1 install
```

Installation performs Docker and Compose preflight checks, copies
`.env.example` when needed, generates required encryption and operator secrets
without displaying them, validates Compose, builds the images, starts the
services, and waits for internal readiness.

Provider and notification credentials are optional for installation. Add them
later through **Settings → Credentials**. Live trading remains disabled.

## Remote-lab address

Configure the URL shown to operators and the host interface used by published
ports:

```bash
./manage install \
  --base-url https://market.lab.example \
  --bind-address 192.0.2.10 \
  --enable-auth
```

```powershell
.\manage.ps1 install `
  --base-url https://market.lab.example `
  --bind-address 192.0.2.10 `
  --enable-auth
```

`PUBLIC_BASE_URL` is the operator-facing URL. When blank, management commands
derive `http://localhost:<FRONTEND_PORT>`. `HOST_BIND_ADDRESS` defaults to
`127.0.0.1`. Non-loopback bindings require authentication; `--enable-auth`
enables registration/login and uses the generated JWT secret. Supplying
`--base-url` also sets `ALLOWED_ORIGINS` to that URL. Only the dashboard edge is
published; backend APIs remain private to the Compose network and are reached
through the frontend proxy. Firewall and TLS configuration remain the
operator's responsibility.

## Portable deployment profiles

The management commands operate the default single-node profile. Use the
additive Compose overlays when an explicit topology is required:

| Profile | Overlay | Storage | External providers |
| --- | --- | --- | --- |
| Single node | `deploy/compose.single-node.yml` | Existing base volumes | Configured providers |
| Sandbox | `deploy/compose.sandbox.yml` | Isolated `sandbox-*` volumes | Deterministic fake boundaries only |
| Multi-worker | `deploy/compose.multi-worker.yml` | Isolated `multi-worker-*` volumes | Configured providers |

Sandbox startup:

```bash
./manage install --base-url http://sandbox.market.test --no-start
docker compose -p market-analysis-sandbox \
  -f docker-compose.yml -f deploy/compose.sandbox.yml up --build -d
```

```powershell
.\manage.ps1 install --base-url http://sandbox.market.test --no-start
docker compose -p market-analysis-sandbox `
  -f docker-compose.yml -f deploy/compose.sandbox.yml up --build -d
```

Multi-worker topology validation:

```bash
docker compose -p market-analysis-workers \
  -f docker-compose.yml -f deploy/compose.multi-worker.yml \
  --profile multi-worker up --build -d
```

The additional quant workers are private and have scheduling disabled. They do
not replace the future durable job broker, worker leases, or PostgreSQL
migration.

To roll back, use the same files and project name with `down` but never add
`--volumes`. Start the previous profile afterward. Existing single-node data is
unchanged because the sandbox and multi-worker overlays use different logical
volumes.

## Lifecycle and diagnostics

| Operation | Linux/macOS | Windows PowerShell |
| --- | --- | --- |
| Start | `./manage start` | `.\manage.ps1 start` |
| Rebuild and start | `./manage start --build` | `.\manage.ps1 start --build` |
| Stop, preserve data | `./manage stop` | `.\manage.ps1 stop` |
| Restart | `./manage restart` | `.\manage.ps1 restart` |
| Status and URL | `./manage status` | `.\manage.ps1 status` |
| All logs | `./manage logs` | `.\manage.ps1 logs` |
| One service | `./manage logs quant-engine --follow` | `.\manage.ps1 logs quant-engine --follow` |
| Verify a backup | `./manage verify-backup /path/to/backup` | `.\manage.ps1 verify-backup C:\path\to\backup` |
| Validate configuration | `./manage verify --config-only` | `.\manage.ps1 verify --config-only` |
| Validate running services | `./manage verify` | `.\manage.ps1 verify` |

Each subcommand supports `--help`. Failures return a nonzero exit code and name
the corrective action or follow-up command.

## Backup and restore

Create a consistent backup:

```bash
./manage backup
```

```powershell
.\manage.ps1 backup
```

Services stop briefly while the three data volumes are archived. A deployment
that was running before backup is restarted and verified afterward. The default
destination is `backups/<UTC timestamp>` and contains:

- `data-ingestion-db.tgz`
- `quant-engine-db.tgz`
- `portfolio-db.tgz`
- `manifest.json`

`manifest.json` records:

- the UTC creation time, Git commit, application version, and Compose project;
- each archive's SHA-256 checksum and byte size;
- a file inventory with per-file checksums;
- SQLite integrity results, `PRAGMA user_version`, schema fingerprints, and
  table record counts;
- an explicit warning that encryption keys are not included.

The backup command reopens every archive, verifies safe extraction, runs
SQLite integrity checks, and reconciles the archive contents with the manifest
before reporting success. A verification failure restarts the services that
were running and returns a nonzero status.

Use an explicit destination when required:

```bash
./manage backup /secure/path/market-analysis-before-upgrade
```

Verify a retained recovery point without touching the running deployment:

```bash
./manage verify-backup /secure/path/market-analysis-before-upgrade
```

```powershell
.\manage.ps1 verify-backup C:\secure\market-analysis-before-upgrade
```

Restore only from a trusted backup created by the same command:

```bash
./manage restore /secure/path/market-analysis-before-upgrade
```

```powershell
.\manage.ps1 restore C:\secure\market-analysis-before-upgrade
```

Restore stops the deployment, replaces all three persistent volume contents,
starts the application, and verifies readiness. Before downtime, it verifies
archive checksums, inventories, schemas, integrity, and record counts. It then
reconciles each staged volume against the same manifest before replacing live
data. Add `--no-start` to inspect the restored volumes before startup.

Test recovery without overwriting the source deployment by restoring into an
isolated, stopped Compose project:

```bash
./manage restore /secure/path/market-analysis-before-upgrade \
  --project-name market-analysis-restore-check \
  --no-start
```

```powershell
.\manage.ps1 restore C:\secure\market-analysis-before-upgrade `
  --project-name market-analysis-restore-check `
  --no-start
```

The alternate project name gives the restored containers and named volumes a
separate namespace. Remove that isolated project after inspection with
`COMPOSE_PROJECT_NAME=market-analysis-restore-check docker compose down
--volumes` or the PowerShell equivalent. Never use `--volumes` against the
active project.

Backups created before `manifest.json` can be restored only with the explicit
`--allow-legacy` option. That bypasses checksum and record-count reconciliation;
use it only for a trusted older archive after retaining a separate copy.

## Upgrade and rollback

The supported upgrade command requires a clean Git worktree:

```bash
./manage upgrade
```

```powershell
.\manage.ps1 upgrade
```

It creates a backup, runs `git pull --ff-only`, rebuilds with refreshed base
images, starts the services, and verifies readiness. It never deletes named
volumes. A failed or unreconciled backup stops the upgrade before `git pull`.
The emergency `--allow-backup-failure` override is available only for cases
where another tested recovery point already exists.

If verification fails:

1. Run `manage logs` or `manage.ps1 logs` and retain the upgrade backup path.
2. Check out the prior tested Git revision.
3. Run the matching restore command with the pre-upgrade backup.
4. Verify the restored deployment before reopening remote access.

The previous manual workflow remains available: copy `.env.example` to `.env`,
generate the required secrets, run `docker compose config`, and then run
`docker compose up --build -d`. The management commands are additive and can be
removed without changing the persistent volume layout.

## Security boundaries

- Generated secrets are written only to `.env`; they are not printed.
- `.env` is ignored by Git and restricted to the current user.
- Provider credentials are not required for tests or startup.
- `LIVE_TRADING_ENABLED` remains `false` unless an operator deliberately
  changes it and completes the separate acknowledgement flow.
- `manage stop` and upgrades never use `docker compose down --volumes`.
- Backups contain portfolio and configuration data. Store them as sensitive
  files and keep encryption keys available for the corresponding retention
  period.
- `CREDENTIAL_ENCRYPTION_KEYS` is deliberately excluded from backup archives.
  Preserve every active and retained key in a separate secret manager; encrypted
  provider credentials cannot be recovered without the matching key.
