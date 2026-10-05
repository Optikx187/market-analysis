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
| `manage`, `manage.ps1`, `verify-changes*`, or `scripts/**` | Management and verification-tool tests |
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
commands, durations, exit codes, and bounded stdout/stderr tails. JUnit emits
one test case per selected job. Dry-run jobs are represented as planned in JSON
and skipped in JUnit.

## Safety boundary

Every subprocess receives verification-safe settings:

- fake notification mode and disabled live-order paths;
- reserved `.invalid` market, notification, and broker endpoints;
- live execution and scheduled scans disabled;
- deterministic test-only encryption, JWT, and internal tokens.

Data-ingestion tests that exercise provider adapters use the normal provider code
path with mocked clients and reserved `.invalid` endpoints; this preserves their
coverage without allowing a production endpoint.

The verification jobs do not start the application, contact production market
or notification providers, or submit broker orders. Dependency installation
and image pulls can still contact their package or container registries.

## Rollback

The command is additive and does not change application data. To remove it,
delete `verify-changes`, `verify-changes.ps1`,
`scripts/verify_changes.py`, `scripts/check_docs.py`, and their tests, then
remove the README references. No database or volume rollback is required.
