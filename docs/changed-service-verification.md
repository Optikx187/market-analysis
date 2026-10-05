# Changed-service verification

`verify-changes` maps repository paths to the smallest safe deterministic check
set. The command is local and is also the contract for the path-filtered hosted
pipeline in issue #88.

## Entry points

Linux, macOS, or WSL:

```bash
./verify-changes changed
./verify-changes service data-ingestion
./verify-changes full
```

Windows PowerShell:

```powershell
.\verify-changes.ps1 changed
.\verify-changes.ps1 service data-ingestion
.\verify-changes.ps1 full
```

Modes:

- `changed` compares committed changes with `origin/master` and includes
  staged, unstaged, and untracked paths;
- `service NAME` runs the repository check plus one named service job;
- `full` runs every test, typecheck, build, Compose render, and documentation
  check.

Use `--base-ref REF` after the mode to compare against another ref. Use
`--dry-run` to show the selected commands without executing them:

```bash
./verify-changes changed --base-ref origin/master --dry-run
./verify-changes full --dry-run
```

Supported service names are `data-ingestion`, `quant-engine`,
`portfolio-engine`, `notification-gateway`, `frontend`, `management`,
`compose`, and `docs`.

## Path mapping

Every selected tier includes the repository whitespace check.

| Changed path | Selected jobs |
| --- | --- |
| `services/data-ingestion/**` | Data-ingestion tests |
| `services/quant-engine/**` | Quant-engine tests |
| `services/portfolio-engine/**` | Portfolio-engine tests |
| `services/notification-gateway/**` | Notification fake-delivery tests |
| `services/frontend/**` | TypeScript and production build |
| Service `requirements.txt`, `package*.json`, or `Dockerfile` | Service checks plus that container image build |
| `shared/**`, `schemas/**`, or `contracts/**` | All service checks plus Compose |
| Any migration directory or migration configuration | All backend checks plus Compose |
| `docker-compose*.yml`, `deploy/compose.*`, or `.env.example` | Compose profile rendering; `.env.example` also runs management tests |
| `manage`, `manage.ps1`, `verify-changes*`, or ordinary `scripts/**` | Management and verification-tool tests |
| `scripts/verify_changes.py` | Broader core checks because it controls job selection |
| `scripts/ci_sandbox_smoke.sh` | Management tests plus the Compose sandbox smoke |
| `README.md`, `docs/**`, `deploy/README.md`, or `.agents/**` | Local documentation-link validation |
| Hosted-pipeline configuration or an unrecognized path | The broader core check set |

Ambiguous and shared changes intentionally expand rather than risk a false
negative. Container image builds are selected for dependency and image-layout
changes, not for ordinary source-only edits.

## Machine-readable reports

JSON and JUnit reports are optional and use parent directories created by the
command:

```bash
./verify-changes changed \
  --json artifacts/verification/summary.json \
  --junit artifacts/verification/junit.xml
```

The JSON report includes schema version `1`, changed paths, selected jobs,
commands, durations, exit codes, bounded stdout/stderr tails, and a portable
`ci_plan`. The plan contains affected backend and image matrices, utility-job
flags, and the high-risk decision consumed by hosted CI. JUnit emits one test
case per selected job. Dry-run jobs are represented as planned in JSON and
skipped in JUnit.

## Hosted pipeline

`.github/workflows/verification.yml` runs on pull requests, manual dispatches,
and a nightly schedule.

- superseded runs for the same pull request or ref are canceled;
- backend services, frontend, management, documentation, changed images, and
  Compose smoke run as independent jobs;
- the PowerShell entry point is exercised on a hosted Windows runner when
  management tooling changes and during manual or nightly runs;
- Python and npm caches are keyed by each requirements file or lockfile;
- changed image builds use BuildKit's GitHub Actions cache by service;
- JSON, JUnit, image metadata, security results, rendered Compose, smoke
  results, and service logs are uploaded as artifacts;
- documentation-only pull requests avoid service and image builds;
- manual, nightly, and high-risk changes run full verification and dependency
  audits.

A change is high risk when the classifier selects Compose or an image build.
This includes workflow, shared contract, schema, migration, deployment,
dependency, Dockerfile, `.env.example`, and ambiguous root changes.

The Compose smoke job runs `scripts/ci_sandbox_smoke.sh`. It starts the real
five-service sandbox with isolated volumes and deterministic test-only
credentials, verifies service readiness, requests a fake crypto quote, captures
a fake notification delivery, and confirms live trading is disabled. Its trap
always removes containers and volumes and restores any pre-existing `.env`.

### GitLab portability

The path rules and job plan do not depend on GitHub Actions. A GitLab
classification job can run the same contract:

```bash
./verify-changes changed \
  --base-ref "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME" \
  --dry-run \
  --json artifacts/verification/classification.json \
  --junit artifacts/verification/classification.xml
```

Subsequent GitLab jobs can read `ci_plan` from the JSON report and call the same
`service`, `full`, and sandbox-smoke commands. Only cache, artifact, and matrix
syntax is provider-specific.

## Safety boundary

Every subprocess receives verification-safe settings:

- fake notification mode and disabled live-order paths;
- reserved `.invalid` market, notification, and broker endpoints;
- live execution and scheduled scans disabled;
- deterministic test-only encryption, JWT, and internal tokens.

Data-ingestion tests that exercise provider adapters use the normal provider code
path with mocked clients and reserved `.invalid` endpoints; this preserves their
coverage without allowing a production endpoint.

The service jobs do not contact production market or notification providers or
submit broker orders. The hosted Compose job starts only the sandbox boundary,
whose reserved `.invalid` endpoints, fake delivery adapters, disabled scanner,
and disabled live execution fail closed. Dependency installation, audit, and
image pulls can still contact their package or container registries.

## Rollback

The pipeline is additive and does not change application data. Disable it by
removing `.github/workflows/verification.yml`; local verification continues to
work. To remove the entire verification feature, also delete `verify-changes`,
`verify-changes.ps1`, `scripts/verify_changes.py`,
`scripts/ci_sandbox_smoke.sh`, `scripts/check_docs.py`, and their tests, then
remove the README references. No database or volume rollback is required.
