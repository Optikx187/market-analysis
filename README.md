# Market Analysis

Market Analysis is a containerized research, risk-management, notification, paper-trading, and guarded broker-execution platform for stocks and cryptocurrency.

It combines:

- market-data ingestion and data-quality checks;
- EMA, RSI, ATR, market-regime, and multi-timeframe analysis;
- walk-forward backtesting with costs and out-of-sample validation;
- portfolio-wide risk controls;
- ranked, explainable opportunities and trade plans;
- Discord, Telegram, Slack, email, and SMS notifications;
- deterministic paper orders;
- optional multi-user authentication;
- guarded live broker execution that is disabled by default.

> **Important:** This software is decision-support and research software. It cannot guarantee profit or identify the best future outcome with certainty. Backtests are not live results. Market data can be delayed or wrong, and all trading can lose money.

## Current deployment scope

The repository supports portable Docker Compose profiles for a private
workstation, deterministic sandbox, single remote-lab host, and a
multi-worker-ready topology.

The current release is **not yet horizontally scalable**:

- market data, portfolio state, and backtests use separate SQLite databases;
- scanner scheduling, deduplication, and some notification state are held in process memory;
- durable scheduled-work distribution still requires leases and a broker;
- watchlists, market data, and price alerts are shared rather than tenant-scoped;
- there is no durable notification queue or multi-destination routing model;
- only the frontend/nginx edge is published on the host; backend services stay
  private to the Compose network.

Do not run multiple active application clusters against copies of the same SQLite files. A PostgreSQL-backed, job-leased architecture is required before multi-host tracker workers are safe.

## Architecture

| Service | Network port | Responsibility | Durable state |
| --- | ---: | --- | --- |
| Frontend | `3000` | React dashboard and nginx API proxy | Browser session state |
| Data ingestion | `8000` | Assets, OHLCV candles, quotes, data quality, price alerts, earnings | SQLite named volume |
| Quant engine | `8001` | Signals, regimes, scanner, opportunity ranking, backtests | SQLite backtest named volume plus in-memory scanner state |
| Portfolio engine | `8002` | Portfolio, risk, trades, paper/live orders, users, settings, encrypted credentials | SQLite named volume |
| Notification gateway | `8003` | Discord, Telegram, Slack, email, SMS, Telegram commands | Credentials from portfolio engine; some runtime state is in memory |

```text
Browser
  |
  v
Frontend / nginx :3000 (host edge)
  |             |              |                  |
  v             v              v                  v
Data :8000   Quant :8001   Portfolio :8002   Notifications :8003
(private Compose network only)
                    |             |
                    +-------------+
```

Docker Compose starts the services in this order:

1. portfolio engine initializes its database, encrypted credential store, and live-trading control;
2. data ingestion waits for portfolio health;
3. notification gateway and quant engine start after their dependencies;
4. frontend starts after backend services are available.

## Prerequisites

Required:

- Git;
- Docker Engine or Docker Desktop;
- Docker Compose v2 (`docker compose`);
- internet access for container images and configured market/notification providers.

Recommended for a small local installation:

- 4 CPU cores;
- 8 GB RAM;
- 20 GB free disk;
- a stable system clock and timezone configuration.

Optional:

- Python 3.11+ for the interactive setup wizard;
- OpenSSL or Python for generating additional secrets.

### Windows

Use Docker Desktop with Linux containers. WSL2 is recommended on supported Windows systems.

Run Docker commands in PowerShell, Windows Terminal, or WSL. Git line-ending conversion should not modify shell scripts that are later run in Linux containers.

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

Installation creates and protects `.env`, generates the required encryption
and operator secrets without displaying them, validates Compose, builds the
images, starts the services, and waits for internal readiness. Provider and
notification credentials are optional and can be added later through
**Settings → Credentials**.

For a remote lab, specify both the operator-facing URL and host interface:

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

Run `./manage status` or `.\manage.ps1 status` to display the configured
dashboard URL and service status. The default dashboard is
`http://localhost:3000`.

See [Operator management commands](docs/operator-management.md) for complete
remote-lab, backup, restore, upgrade, log, and rollback instructions.

## Deployment profiles

The base Compose file remains the supported single-node default used by
`manage` and `manage.ps1`. Add one profile overlay for explicit deployments.
The commands are identical in Linux shells and Windows PowerShell.

### Deterministic sandbox

Initialize local secrets without starting the default profile:

```bash
./manage install --base-url http://sandbox.market.test --no-start
```

```powershell
.\manage.ps1 install --base-url http://sandbox.market.test --no-start
```

Start the isolated sandbox:

```bash
docker compose -p market-analysis-sandbox \
  -f docker-compose.yml \
  -f deploy/compose.sandbox.yml \
  up --build -d
```

```powershell
docker compose -p market-analysis-sandbox `
  -f docker-compose.yml `
  -f deploy/compose.sandbox.yml `
  up --build -d
```

The sandbox:

- uses deterministic fake market candles and quotes;
- captures fake Telegram, Discord, Slack, email, and SMS deliveries internally;
- uses the deterministic paper-order engine as its broker boundary;
- disables scheduled scans and hard-disables live execution;
- rejects external market, notification, and broker endpoints at startup;
- uses separate `sandbox-*` volumes;
- requires no third-party credentials or provider calls after images exist.

Add `sandbox.market.test` to the test machine's hosts file, or verify it without
changing DNS:

```bash
curl --resolve sandbox.market.test:3000:127.0.0.1 \
  http://sandbox.market.test:3000/api/status
```

### Explicit single-node profile

The following is equivalent to the default topology and preserves the existing
single-node volume paths:

```text
docker compose -f docker-compose.yml -f deploy/compose.single-node.yml up --build -d
```

### Multi-worker-ready profile

```bash
docker compose -p market-analysis-workers \
  -f docker-compose.yml \
  -f deploy/compose.multi-worker.yml \
  --profile multi-worker \
  up --build -d
```

```powershell
docker compose -p market-analysis-workers `
  -f docker-compose.yml `
  -f deploy/compose.multi-worker.yml `
  --profile multi-worker `
  up --build -d
```

This profile adds two private, stateless quant worker containers by default and
keeps the primary quant-engine scheduler as a singleton. Worker scanners are
disabled so the profile cannot duplicate scheduled signals. Set
`QUANT_WORKER_REPLICAS` to change the worker count. Durable job distribution,
leases, and notification outboxes remain separate roadmap work; do not treat
this profile as multi-host scheduling.

### Profile rollback

Stop the selected profile without `--volumes`, then start the prior profile.
The single-node profile retains the original volume names. Sandbox and
multi-worker profile data remain isolated in their own named volumes.

## Existing installation lifecycle

| Operation | Linux/macOS | Windows PowerShell |
| --- | --- | --- |
| Start | `./manage start` | `.\manage.ps1 start` |
| Rebuild and start | `./manage start --build` | `.\manage.ps1 start --build` |
| Stop and preserve data | `./manage stop` | `.\manage.ps1 stop` |
| Restart | `./manage restart` | `.\manage.ps1 restart` |
| Status | `./manage status` | `.\manage.ps1 status` |
| Logs | `./manage logs` | `.\manage.ps1 logs` |
| Verify | `./manage verify` | `.\manage.ps1 verify` |

The stop, restart, backup, and upgrade paths preserve named data volumes. Do
not run `docker compose down --volumes` unless you intentionally want to
delete the databases.

## Backup, restore, and upgrade

Create a consistent backup of all three database volumes:

```bash
./manage backup
```

```powershell
.\manage.ps1 backup
```

Each backup includes `manifest.json` with archive SHA-256 checksums, application
revision, Compose project, SQLite schema fingerprints, integrity results, file
inventories, and per-table record counts. Backup succeeds only after the
archives reconcile with that manifest.

Verify a retained backup without changing application data:

```bash
./manage verify-backup /secure/path/to/backup
```

```powershell
.\manage.ps1 verify-backup C:\secure\path\to\backup
```

Restore a trusted backup:

```bash
./manage restore /secure/path/to/backup
```

```powershell
.\manage.ps1 restore C:\secure\path\to\backup
```

To verify a restore without overwriting the active installation, target a
separate stopped Compose project:

```bash
./manage restore /secure/path/to/backup \
  --project-name market-analysis-restore-check \
  --no-start
```

```powershell
.\manage.ps1 restore C:\secure\path\to\backup `
  --project-name market-analysis-restore-check `
  --no-start
```

Upgrade a clean checkout with a pre-upgrade backup, fast-forward pull, image
rebuild, startup, and readiness verification:

```bash
./manage upgrade
```

```powershell
.\manage.ps1 upgrade
```

A failed or unreconciled backup stops the upgrade before Git or containers are
changed. `--allow-backup-failure` is an explicit emergency override and should
only be used when another tested recovery point is already available.

The logical Compose volumes are:

- `data-ingestion-db`;
- `quant-engine-db`;
- `portfolio-db`.

Docker prefixes their physical names with the Compose project name. Inside the
containers, the database locations are:

- data ingestion: `/app/data/data_ingestion.db`;
- quant backtests: `/app/data/backtests.db`;
- portfolio: `/app/data/portfolio.db`.

A complete recovery set includes the backup archives, `.env` or equivalent
secret-manager configuration, every active and retained
`CREDENTIAL_ENCRYPTION_KEYS` key, and the corresponding application revision.
Without the encryption key, encrypted provider credentials cannot be recovered
from the portfolio database.

## Manual installation

The management commands are the supported path. For troubleshooting or custom
automation, the equivalent manual flow is:

```bash
cp .env.example .env
python3 scripts/setup.py
docker compose config
docker compose up --build -d
docker compose ps
```

On Windows, use `Copy-Item .env.example .env` and `py scripts/setup.py`.
Provider credentials placed in `.env` are migration inputs: portfolio-engine
encrypts them into its database and removes the plaintext provider values from
`.env`. Keep `.env` private and never commit it.

Run risk-model tests with:

```bash
docker compose run --rm quant-engine pytest tests/ -v
```

## First-run onboarding

The dashboard opens a Getting Started flow:

1. review the product and safety model;
2. configure market-data credentials;
3. configure optional notification channels;
4. add the first watchlist ticker;
5. finish or skip the wizard.

The wizard can be opened again from the application header.

Saving credentials through the UI requires the `SETTINGS_OPERATOR_TOKEN`. The browser holds that operator token in memory rather than persistent browser storage.

## Typical research workflow

1. Add stocks or crypto assets to the watchlist.
2. Wait for historical ingestion or select **Refresh Data**.
3. Confirm that the asset is data-quality eligible.
4. Analyze a single asset or run the ranked scanner.
5. Review the signal reason, market regime, timeframe agreement, portfolio-risk decision, and trade plan.
6. Run walk-forward backtesting with enough history.
7. Use out-of-sample results—not in-sample results—to judge alert eligibility.
8. Create price alerts and review historical charts.
9. Log actual trades manually or through an authorized Telegram command.
10. Review portfolio heat, concentration, attribution, journals, and Action Required items.
11. Exercise the full order lifecycle with paper orders before considering live execution.

Signals are recommendations, not proof that a trade occurred. The user must log an actual trade or create an order.

## Watchlist and market data

The watchlist supports:

- stocks and crypto;
- single and bulk additions;
- refresh and refresh-all;
- import/export;
- quotes and candle counts;
- historical charting;
- data-quality status;
- earnings information for supported stocks.

Data quality checks detect:

- stale data;
- missing periods;
- duplicate timestamps;
- malformed OHLCV values;
- anomalous candles;
- insufficient history.

Analyze and Backtest actions are disabled when required data is ineligible. A refresh remains available so the user can attempt recovery.

Current frontend minimums:

- analysis: at least 201 daily candles;
- walk-forward backtesting: at least 301 daily candles.

## Scanner and opportunity ranking

The scanner:

- evaluates active assets on a schedule or on demand;
- applies data-quality gates;
- analyzes daily, 4-hour, and 1-hour timeframes;
- classifies trend, volatility, breadth, and risk regime;
- requests a portfolio-wide risk decision;
- combines historical expectancy, confidence calibration, reward/risk, liquidity, data quality, regime fit, timeframe agreement, and portfolio fit;
- produces a complete trade plan;
- suppresses or marks ineligible opportunities that fail evidence or risk controls.

Missing evidence receives no optimistic score. An opportunity must pass its out-of-sample, data-quality, regime, score, and portfolio-risk requirements before it is eligible.

Current scheduled-scan limitation: when `MARKET_HOURS_ONLY=true`, the global loop pauses outside US equity hours, including crypto scans. Run crypto scans manually outside equity hours or set the flag to false until stock and crypto schedules are separated.

## Trade plans

An eligible plan can include:

- entry zone;
- stop-loss;
- two profit targets;
- scale-in and scale-out instructions;
- position size and quantity;
- maximum planned loss;
- estimated costs;
- net reward/risk;
- time-based exit;
- invalidation reason;
- earnings and economic-event warnings.

Users can approve, edit, reject, or snooze opportunities. Approval does not automatically submit a broker order.

## Walk-forward backtesting

Backtesting separates:

- training;
- validation;
- out-of-sample test windows.

It models:

- commissions;
- spread;
- slippage;
- delayed fills;
- separate stock and crypto sessions;
- parameter stability;
- benchmarks and buy-and-hold comparisons;
- regime-labeled trades.

Reported metrics include:

- trade count and win rate;
- expectancy;
- Sharpe and Sortino ratios;
- profit factor;
- maximum drawdown;
- recovery time;
- turnover and exposure;
- gross and after-cost returns.

Alert eligibility is based on configured out-of-sample thresholds. A historically eligible strategy can still lose money in the future.

## Portfolio risk

Portfolio controls include:

- total portfolio heat;
- ticker, sector, asset-class, and directional exposure;
- correlated exposure;
- daily and weekly loss breakers;
- maximum drawdown breaker;
- volatility targeting;
- fractional Kelly cap;
- equity and crypto stress scenarios.

Circuit breakers block additional risk but should still allow risk-reducing actions such as closing or reducing a position.

Key defaults live in `.env.example`:

```dotenv
MAX_PORTFOLIO_HEAT_PCT=0.08
MAX_TICKER_EXPOSURE_PCT=0.20
MAX_SECTOR_EXPOSURE_PCT=0.35
MAX_ASSET_CLASS_EXPOSURE_PCT=0.70
MAX_DIRECTIONAL_EXPOSURE_PCT=0.80
MAX_CORRELATED_EXPOSURE_PCT=0.40
CORRELATION_THRESHOLD=0.75
DAILY_LOSS_LIMIT_PCT=0.03
WEEKLY_LOSS_LIMIT_PCT=0.06
MAX_DRAWDOWN_PCT=0.12
VOLATILITY_TARGET_PCT=0.15
FRACTIONAL_KELLY_CAP=0.10
```

Review these values for your account and risk policy. Defaults are not personalized financial advice.

## Manual trades, attribution, and journals

The Trades view records user-reported positions. It does not assume every signal was executed.

Performance tools provide:

- realized and unrealized P&L;
- strategy, ticker, asset, sector, timeframe, and regime attribution;
- fees and slippage;
- planned versus actual execution;
- maximum favorable/adverse excursion;
- confidence calibration;
- automated post-trade journals;
- filtered JSON and CSV exports.

Attribution becomes more useful as the sample size grows. Small samples should remain descriptive rather than being treated as reliable performance evidence.

## Paper orders

The Orders view is deterministic paper trading:

> **Simulated fills only. No broker order is submitted.**

Supported behaviors include:

- market;
- limit;
- stop;
- stop-limit;
- bracket/OCO exits;
- trailing stop;
- DAY and GTC time-in-force;
- idempotency keys;
- candle-based processing;
- partial fills and participation limits;
- cancellation;
- fees, spread, and slippage;
- reconciliation and audit history.

Use paper orders to verify order assumptions, lifecycle behavior, and portfolio accounting before enabling any live capability.

## Live trading

> **Warning: Live execution can submit broker orders and put real capital at risk.**

Live trading is disabled by default:

```dotenv
LIVE_TRADING_ENABLED=false
LIVE_BROKER=alpaca
LIVE_BROKER_BASE_URL=https://paper-api.alpaca.markets
LIVE_ACK_PHRASE=ENABLE LIVE TRADING
LIVE_OPERATOR_TOKEN=
LIVE_MAX_ORDER_NOTIONAL_USD=1000.0
LIVE_MAX_PRICE_AGE_SECONDS=300.0
```

The default Alpaca URL is the paper/sandbox endpoint. Do not point it at a production endpoint unless real trading is intentional and independently reviewed.

Live execution requires independent gates:

1. `LIVE_TRADING_ENABLED=true`;
2. a configured `LIVE_OPERATOR_TOKEN`;
3. operator authorization in the browser;
4. the exact configured acknowledgement phrase;
5. no active kill switch;
6. valid broker/sandbox configuration;
7. an order preview whose blocking checks pass;
8. the preview fingerprint echoed during submission;
9. idempotency and durable audit behavior.

The Live Trading view supports:

- authorize/revoke operator;
- acknowledge;
- disable or re-enable trading;
- preview;
- approve and submit;
- cancel an order or all orders;
- broker reconciliation;
- audit-chain verification.

Never use production credentials or real-money orders for development, CI, demonstrations, or automated tests.

## Authentication

Authentication is optional:

```dotenv
AUTH_ENABLED=false
JWT_SECRET=
JWT_ALGORITHM=HS256
JWT_EXPIRY_HOURS=24
```

When disabled, portfolio APIs use the default user identity. Do not expose that mode directly to the public internet.

When enabled:

- `JWT_SECRET` must contain at least 32 bytes of non-default material;
- `JWT_ALGORITHM` must be `HS256`;
- users register or sign in through the UI or auth API;
- protected portfolio APIs require a bearer token;
- portfolio/trade/order records are scoped to the authenticated user;
- signing out or closing the tab clears the frontend session.

Generate a JWT secret:

```bash
docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_hex(32))"
```

For any remote deployment, terminate TLS at a trusted reverse proxy or load balancer, restrict network access, and enable authentication.

The current user model is not yet a complete commercial tenancy system. It does not provide organizations, roles, subscription plans, quotas, or per-tenant notification destinations.

## Credentials and key rotation

Provider credentials are encrypted in the portfolio database with Fernet.

Required external settings:

```dotenv
CREDENTIAL_ENCRYPTION_KEYS=
INTERNAL_SERVICE_TOKEN=
SETTINGS_OPERATOR_TOKEN=
```

For key rotation, place the new key first and retain the old key:

```dotenv
CREDENTIAL_ENCRYPTION_KEYS=new_key,old_key
```

The application encrypts new or rewritten values with the first key and can decrypt older rows with retained keys. Do not remove an old key until active rows and retained backups have been rotated or expired.

Production deployments should provide secrets through a deployment secret manager rather than a bind-mounted `.env`.

## Notifications

Supported transports:

- Telegram bot/chat;
- Discord webhook;
- Slack webhook;
- SMTP email;
- Twilio SMS.

Configure credentials through Settings or the setup wizard. Use **Send Test Notification** before relying on a channel.

Current routing behavior broadcasts eligible events to every configured and enabled channel. Each provider currently has one global destination, not multiple per-user or per-strategy routes.

Current reliability limitations:

- delivery is synchronous;
- failed sends are logged but not durably queued for retry;
- channel toggles reset to environment defaults after gateway restart;
- Telegram reply history is in memory;
- multi-channel Discord routing is not yet implemented.

These limitations should be addressed before operating multiple tracker clusters or selling notification service tiers.

### Telegram commands

An authorized configured Telegram chat supports:

```text
/bought TICKER PRICE QTY [STOP_LOSS TARGET_PRICE]
/sold   TICKER PRICE QTY [STOP_LOSS TARGET_PRICE]
/trades
```

These commands log manual portfolio trades. They do not submit broker orders.

## Dashboard and usability

The dashboard includes:

- Action Required inbox;
- portfolio and risk snapshot;
- configurable widgets;
- named saved layouts;
- watchlist;
- market/data health;
- top opportunities;
- workspace navigation;
- in-context terminology tooltips;
- Help & Docs glossary.

Keyboard shortcuts:

| Key | Action |
| --- | --- |
| `a` | Focus Action Required |
| `d` | Jump to dashboard |
| `[` / `]` | Previous / next workspace |
| `?` | Toggle shortcut help |

Primary workspaces:

- Alerts;
- Orders (Paper);
- Live Trading;
- Trades;
- Performance;
- Scanner;
- Price Alerts;
- Chart;
- Settings;
- Help & Docs.

## Configuration reference

Use `.env.example` as the source of available configuration.

Important groups:

- provider migration credentials;
- encrypted credential and internal service keys;
- notification credentials and toggles;
- signal and ATR/risk parameters;
- portfolio limits and stress scenarios;
- optional JWT authentication;
- guarded live execution.

After changing `.env`, restart affected containers:

```bash
docker compose up -d --force-recreate
```

Some settings edited through the UI update the environment file. Use the Settings operator token and protect the file from unauthorized access.

## API documentation

Each FastAPI service exposes interactive OpenAPI documentation when run
directly for development:

- data ingestion: `http://localhost:8000/docs`;
- quant engine: `http://localhost:8001/docs`;
- portfolio engine: `http://localhost:8002/docs`;
- notification gateway: `http://localhost:8003/docs`.

Use these pages for current request and response schemas.

Major API groups:

- `/api/assets`, `/api/candles`, `/api/data-quality`, `/api/quotes`, `/api/price-alerts`, `/api/earnings`;
- `/api/analyze`, `/api/regime`, `/api/scan-all`, `/api/scanner`, `/api/opportunities`, `/api/backtest`;
- `/api/portfolio`, `/api/trades`, `/api/attribution`, `/api/paper-orders`, `/api/action-items`, `/api/dashboard-preferences`;
- `/api/auth`, `/api/settings`, `/api/live-trading`, `/api/live-orders`;
- `/api/notify`.

Containerized deployments intentionally do not publish backend ports. Use the
nginx `/api/...` routes for application traffic.

## Local development

### Backend

Each Python service can run from its own directory:

```bash
cd services/data-ingestion
PYTHONPATH=. uvicorn app.main:app --host 0.0.0.0 --port 8000
```

For quant-engine outside Compose, set local service URLs:

```bash
cd services/quant-engine
DATA_INGESTION_URL=http://localhost:8000 \
PORTFOLIO_ENGINE_URL=http://localhost:8002 \
NOTIFICATION_GATEWAY_URL=http://localhost:8003 \
PYTHONPATH=. uvicorn app.main:app --host 0.0.0.0 --port 8001
```

Start portfolio engine on `8002` and notification gateway on `8003`.

### Frontend

```bash
cd services/frontend
npm ci
npm run dev
```

The Vite development server runs on:

```text
http://localhost:5173
```

Its proxy expects backend services on local ports `8000`-`8003`.

## Tests

Use changed-service verification for the smallest safe local check set:

```bash
./verify-changes changed
```

```powershell
.\verify-changes.ps1 changed
```

Run one explicit service with `service <name>`, or use `full` before a broad
release. JSON/JUnit reports and the complete path-to-job mapping are documented
in [Changed-service verification](docs/changed-service-verification.md).

Backend:

```bash
cd services/data-ingestion
PYTHONPATH=. pytest tests -q

cd ../quant-engine
PYTHONPATH=. pytest tests -q

cd ../portfolio-engine
PYTHONPATH=. pytest tests -q

cd ../notification-gateway
pytest test_credentials.py -q
```

Frontend:

```bash
cd services/frontend
npx tsc --noEmit
npm run build
```

Compose validation:

```bash
docker compose config
docker compose -f docker-compose.yml -f deploy/compose.sandbox.yml config
docker compose -f docker-compose.yml -f deploy/compose.single-node.yml config
docker compose -f docker-compose.yml -f deploy/compose.multi-worker.yml \
  --profile multi-worker config
docker compose build
```

GitHub pull requests use the path-filtered
[verification pipeline](docs/changed-service-verification.md#hosted-pipeline).
Affected services run independently with dependency and image-layer caches.
Manual, nightly, and high-risk changes also run the full-system and dependency
security tiers. All Compose smoke checks use fake providers, fake
notifications, and disabled live trading; production credentials are never
required.

## Troubleshooting

### `.env` is missing or mounted as a directory

Create a regular file before starting:

```bash
cp .env.example .env
```

Then populate the required encryption and internal tokens.

### Portfolio engine will not start

Check:

```bash
docker compose logs portfolio-engine
```

Common causes:

- missing or invalid `CREDENTIAL_ENCRYPTION_KEYS`;
- authentication enabled with a weak/missing `JWT_SECRET`;
- database file permissions;
- malformed `.env`.

### Settings cannot save

Enter the configured `SETTINGS_OPERATOR_TOKEN` in the Settings authorization control. It is independent of an ordinary user login.

### No market data

Check:

```bash
curl http://localhost:3000/api/status
docker compose logs data-ingestion
```

Possible causes:

- provider outage or rate limiting;
- blocked internet/DNS access;
- invalid provider credentials;
- unsupported ticker format;
- the current data-ingestion volume retaining older application files after an upgrade.

Yahoo may return HTTP `403` or `429`; reachability status does not guarantee a successful quote request.

### Asset is blocked

Open the data-quality details and inspect:

- candle age;
- missing periods;
- duplicates;
- invalid OHLCV rows;
- anomalies;
- history count.

Use **Refresh Data** and inspect data-ingestion logs.

### Scanner is not running

Check:

```bash
curl http://localhost:3000/api/scanner/status
docker compose logs quant-engine
```

Review:

- `SCAN_ENABLED`;
- `SCAN_INTERVAL_MINUTES`;
- `MARKET_HOURS_ONLY`;
- data eligibility;
- stock-market hours.

### Notification test fails

Check channel status and gateway logs:

```bash
curl http://localhost:3000/api/notify/channels
docker compose logs notification-gateway
```

Verify credentials, provider network access, and channel enablement.

### Port conflict

The current stack binds only the frontend edge port, `3000` by default.

Identify the conflicting process or change the host-side Compose port mapping.

### Reset all application data

This is destructive:

```bash
docker compose down -v
```

Do not run it unless backups exist and deletion is intentional.

## Financial glossary

| Term | Meaning |
| --- | --- |
| EMA | Exponential moving average; emphasizes recent prices. |
| RSI | Relative Strength Index; a momentum measure, usually from 0 to 100. |
| ATR | Average True Range; a measure of recent price movement/volatility. |
| OHLCV | Open, high, low, close, and volume candle data. |
| Stop-loss | A planned exit intended to limit loss; actual fills can be worse. |
| Target | A planned profit-taking price, not a guaranteed exit. |
| Reward/risk | Planned upside divided by planned downside. |
| Kelly fraction | A sizing formula based on estimated edge; the application caps and reduces it. |
| Portfolio heat | Total planned loss across open positions relative to equity. |
| Drawdown | Decline from a prior equity peak. |
| MFE / MAE | Maximum favorable/adverse movement while a trade is open. |
| Regime | A classification such as bull/bear, normal/high volatility, or risk-on/risk-off. |
| Timeframe agreement | Degree to which daily, 4-hour, and 1-hour evidence agrees. |
| Walk-forward test | Repeated train/validate/test windows that preserve time order. |
| Out of sample | Data not used to select the tested parameters. |
| Profit factor | Gross profit divided by gross loss. |
| Sharpe / Sortino | Risk-adjusted return measures; Sortino focuses on downside variability. |
| Slippage | Difference between an expected and simulated/actual fill price. |
| Spread | Difference between bid and ask prices. |
| Idempotency | Retrying the same request without creating a duplicate order/event. |
| Paper trading | Simulated trading with no broker order. |
| Live trading | Broker-connected execution where real capital may be at risk. |

The in-application Help & Docs workspace contains additional beginner-oriented explanations and contextual tooltips.

## Repository layout

```text
market-analysis/
├── docker-compose.yml
├── .env.example
├── scripts/
│   └── setup.py
└── services/
    ├── data-ingestion/
    ├── quant-engine/
    ├── portfolio-engine/
    ├── notification-gateway/
    └── frontend/
```

## Production-readiness roadmap

Before multi-cluster or commercial deployment:

1. fix the data-ingestion volume layout;
2. add cross-platform install/backup/upgrade commands;
3. introduce versioned database migrations;
4. move durable state to PostgreSQL-compatible schemas;
5. use exact decimal types for financial records;
6. move scheduler and notification state into durable leased jobs;
7. add retryable notification delivery and multiple owned destinations;
8. tenant-scope watchlists, alerts, routes, and quotas;
9. expose only an authenticated edge and harden service-to-service trust;
10. add path-filtered CI plus deterministic Compose tests;
11. prove multiple workers locally before cloud deployment;
12. prefer ECS Fargate and Aurora PostgreSQL for the initial small cloud service, with EKS reserved for a demonstrated need.
