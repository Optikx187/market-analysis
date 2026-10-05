# Market Analysis — Microservices Architecture

A highly performant, decoupled microservices platform for algorithmic market analysis, quantitative signal generation, and paper trading with strict capital preservation guardrails.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    Docker Compose (trading_network)           │
├──────────────┬───────────────┬───────────────┬───────────────┤
│  Service A   │  Service B    │  Service C    │  Service D    │
│  Data        │  Quant Engine │  Portfolio    │  Frontend +   │
│  Ingestion   │  & Risk       │  Engine       │  Notifications│
│  :8000       │  :8001        │  :8002        │  :3000 / :8003│
└──────┬───────┴───────┬───────┴───────┬───────┴───────────────┘
       │               │               │
       ▼               ▼               ▼
   Binance/Yahoo   Half-Kelly     SQLite + Portfolio
   WebSocket/REST  ATR Scaling    Capital Guardrails
```

### Service A — Data Ingestion (`services/data-ingestion/`)
- **Port**: 8000
- Streams live pricing via WebSocket (Binance for crypto, Yahoo Finance for stocks)
- Stores 365 days of historical OHLCV data
- **Crypto Name Resolution**: Built-in mapping for 21 popular coins (BTC→Bitcoin, ETH→Ethereum, etc.)
- **System Health Monitoring**: Background loop pings Binance/Yahoo every 60s, tracks online/offline transitions, and auto-backfills missed data on reconnect
- Empty watchlist by default — users add any tickers via UI (examples: SPY, BTC, ETH)
- Broadcasts data to Service B

### Service B — Quant Engine & Risk (`services/quant-engine/`)
- **Port**: 8001
- EMA (20/50/200) trend filters, RSI (14), ATR (14)
- **Tanking Detection**: Price < 200 EMA or bearish 20/50 cross → suppress all buys, recommend liquidation
- **Half-Kelly Position Sizing**: `0.5 * (W - ((1-W) / R))` where W = 30-day win rate, R >= 1:3
- **Volatility Calibration**: Position size scales linearly to $0 as ATR rises above 30-day average

### Service C — Portfolio Engine (`services/portfolio-engine/`)
- **Port**: 8002
- SQLite + SQLAlchemy ORM for position tracking
- Virtual paper trading ($10K starting capital, configurable via `INITIAL_BALANCE`)
- **Capital Guardrail**: If trade > 5% of portfolio balance → CAPITAL OVERSPEND WARNING → cancel signal
- **Balance-Aware Recommendations**: Trade sizing accounts for current balance, loss tolerance, and ATR-based stop distances
- **Credential Management**: Secure storage and verification of API keys via UI

### Service D — Frontend & Notifications (`services/frontend/` + `services/notification-gateway/`)
- **Frontend Port**: 3000 (nginx reverse proxy)
- **Notification Port**: 8003
- React / Vite / Tailwind CSS dashboard
- Real-time charts, dynamic watchlist CRUD with crypto autofill, portfolio balance tracking
- Dual-broadcast Discord + Telegram notifications with test endpoint
- **Telegram Bot Listener**: Reply to alerts with `/bought BTC 65000 0.1` or `/sold ETH 3500 1` to log trades directly from Telegram
- Settings panel for credential management, environment configuration, system health, and trade reply history

## Quick Start

### Prerequisites
- Git and internet access
- Docker Engine with the Compose v2 plugin, or Docker Desktop using Linux containers
- (Optional) API keys for Binance, Alpaca, Telegram, Discord

### 1. Install and start

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

The installer securely generates required local secrets, validates Compose,
builds the images, starts the services, and waits for readiness. Provider and
notification credentials are optional and can be added later through
**Settings → Credentials**.

For remote-lab addresses, backups, restore, upgrades, logs, and rollback, see
[Operator management commands](docs/operator-management.md).

### 2. Access the dashboard

Open **http://localhost:3000** by default. Run `./manage status` or
`.\manage.ps1 status` to display the configured operator URL and service
status.

### 3. Stop services

```bash
./manage stop
```

```powershell
.\manage.ps1 stop
```

Named data volumes are preserved.

### Manual configuration

Run the interactive setup script to configure all API keys and credentials:

```bash
python3 scripts/setup.py
```

This prompts for Binance, Alpaca, Telegram, and Discord credentials and stages
them in a mode-`0600` local `.env` file. On first start, Portfolio Engine
migrates them into authenticated encrypted database storage and removes the
plaintext provider values from `.env`. Subsequent changes use **Settings →
Credentials**; the encrypted database is the authoritative credential store.

Alternatively, copy and edit manually:

```bash
cp .env.example .env
# Edit .env with your credentials
```

Then build and start with Compose:

```bash
docker compose up --build
```

### Run risk model tests

```bash
docker compose run --rm quant-engine pytest tests/ -v
```

## Local Development (Without Docker)

For rapid iteration, start each service directly:

```bash
# Terminal 1 — Data Ingestion
cd services/data-ingestion && pip install -r requirements.txt
PYTHONPATH=. uvicorn app.main:app --port 8000 --reload

# Terminal 2 — Quant Engine
cd services/quant-engine && pip install -r requirements.txt
DATA_INGESTION_URL=http://localhost:8000 PYTHONPATH=. uvicorn app.main:app --port 8001 --reload

# Terminal 3 — Portfolio Engine
cd services/portfolio-engine && pip install -r requirements.txt
PYTHONPATH=. uvicorn app.main:app --port 8002 --reload

# Terminal 4 — Notification Gateway
cd services/notification-gateway && pip install -r requirements.txt
PYTHONPATH=. uvicorn main:app --port 8003 --reload

# Terminal 5 — Frontend
cd services/frontend && npm install && npm run dev
```

The frontend dev server runs on **http://localhost:5173** with hot reload. API requests are proxied to the backend services via Vite config.

## API Reference

### Data Ingestion (`:8000`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/api/assets` | List watchlist assets |
| `POST` | `/api/assets` | Add asset `{ticker, name, asset_type}` |
| `DELETE` | `/api/assets/{ticker}` | Remove asset |
| `GET` | `/api/symbols/lookup/{ticker}?asset_type=crypto` | Lookup ticker → friendly name |
| `GET` | `/api/candles/{ticker}?timeframe=1d` | Historical OHLCV data |

### Quant Engine (`:8001`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/api/analyze` | Analyze signal `{ticker, available_capital}` |

### Portfolio Engine (`:8002`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/api/portfolio` | Get portfolio stats (balance, equity, PnL, open positions) |
| `POST` | `/api/portfolio/balance` | Update balance `{balance: float}` |
| `GET` | `/api/portfolio/recommendation?ticker=X&current_price=Y` | Balance-aware trade recommendation |
| `GET` | `/api/trades` | List all trades |
| `POST` | `/api/process-signal` | Process trading signal |
| `POST` | `/api/settings/credentials/save` | Save API credentials |
| `GET` | `/api/settings/credentials/status` | Get credential verification status |
| `GET` | `/api/settings/onboarding` | Onboarding completion status |
| `GET` | `/api/settings/env/{key}` | Read environment setting |
| `PUT` | `/api/settings/env` | Update environment setting `{key, value}` |

### Notification Gateway (`:8003`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/api/notify` | Send alert to configured channels |
| `POST` | `/api/notify/test` | Send test notification to verify Discord/Telegram |
| `GET` | `/api/notify/channels` | Channel toggle status |
| `POST` | `/api/notify/channels/toggle` | Enable/disable a channel `{channel, enabled}` |
| `GET` | `/api/notify/reply-trades` | Trades logged via Telegram bot replies |

#### Telegram Bot Commands

When the bot is active (requires `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`), users can reply to the bot in Telegram:

| Command | Example | Description |
|---------|---------|-------------|
| `/bought` | `/bought BTC 65000 0.1` | Log a BUY trade |
| `/sold` | `/sold ETH 3500 1 3400 3600` | Log a SELL trade (with optional stop/target) |
| `/trades` | `/trades` | List open trades |

## Service Communication

| From → To | Protocol | Purpose |
|-----------|----------|---------|
| Frontend → All Services | HTTP (nginx proxy) | API gateway |
| Quant Engine → Data Ingestion | HTTP | Fetch candle data |
| Portfolio Engine → Quant Engine | HTTP | Request signal analysis |
| Notification Gateway → External | HTTPS | Discord/Telegram delivery |

## Boot Order

1. **Portfolio Engine** starts first and unlocks encrypted credentials
2. **Data Ingestion** waits for Portfolio Engine healthy
3. **Notification Gateway** waits for Portfolio Engine and Data Ingestion
4. **Quant Engine** waits for the dependent services
5. **Frontend** waits for all backend services

## Configuration Reference

| Variable | Service | Description |
|----------|---------|-------------|
| `CREDENTIAL_ENCRYPTION_KEYS` | C | Comma-separated Fernet keys, newest first; supply through a deployment secret manager |
| `INTERNAL_SERVICE_TOKEN` | A, C, Gateway | Shared token used only to retrieve credentials from Portfolio Engine |
| `BINANCE_API_KEY` | migration only | One-time plaintext input; removed from `.env` after encrypted migration |
| `BINANCE_API_SECRET` | migration only | One-time plaintext input; removed from `.env` after encrypted migration |
| `ALPACA_API_KEY` | migration only | One-time plaintext input; removed from `.env` after encrypted migration |
| `ALPACA_API_SECRET` | migration only | One-time plaintext input; removed from `.env` after encrypted migration |
| `TELEGRAM_BOT_TOKEN` | migration only | One-time plaintext input; removed from `.env` after encrypted migration |
| `TELEGRAM_CHAT_ID` | migration only | One-time plaintext input; removed from `.env` after encrypted migration |
| `DISCORD_WEBHOOK_URL` | migration only | One-time plaintext input; removed from `.env` after encrypted migration |
| `SLACK_WEBHOOK_URL` | migration only | One-time plaintext input; removed from `.env` after encrypted migration |
| `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO`, `EMAIL_FROM` | migration only | Email provider values; removed from `.env` after encrypted migration |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `SMS_TO_NUMBER` | migration only | SMS provider values; removed from `.env` after encrypted migration |
| `SMTP_PORT` | Gateway | Non-secret SMTP transport port (default: 587) |
| `RISK_REWARD_RATIO` | B, C | Min risk:reward (default: 3.0) |
| `ATR_STOP_MULTIPLIER` | B, C | ATR multiplier for stop distance (default: 1.5) |
| `ATR_VOLATILITY_THRESHOLD` | B | ATR suppression multiplier (default: 2.0) |
| `TRAILING_STOP_PCT` | B, C | Base stop distance as decimal (default: 0.02 = 2%) |
| `INITIAL_BALANCE` | C | Paper trading starting capital (default: 10000) |
| `LOSS_TOLERANCE_PCT` | C | Max loss per trade as % of balance (default: 0.02 = 2%) |

Docker Compose mounts `.env` only as Portfolio Engine's local configuration and
migration source; it does not copy provider credentials or encryption keys into
the container environment. Migration commits ciphertext first, then removes the
provider values from the mounted file. Other services receive credentials only
through the authenticated internal endpoint.

### Credential key rotation

Keep encryption keys outside the database and its backups. To rotate without
downtime:

1. Generate a new Fernet key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
2. Set `CREDENTIAL_ENCRYPTION_KEYS=new_key,old_key` in the deployment secret manager.
3. Restart Portfolio Engine. Startup decrypts legacy/Base64 or old-key rows and re-encrypts every row with `new_key`.
4. Rotate replicas and create a new backup while retaining `old_key` for any older backup that may still require it.
5. Verify masked credential status and dependent-service health, then set `CREDENTIAL_ENCRYPTION_KEYS=new_key` and restart again.

Never remove the old key until every active row and replica has rotated and all
retained backups that depend on it have expired. A database backup contains only
`fernet$...` ciphertext and is not recoverable without an external key. Rotate
`INTERNAL_SERVICE_TOKEN` by updating Portfolio Engine, Data Ingestion, and
Notification Gateway together.

## Risk Model — Position Sizing

### Quant Engine (Signal Generation)

The system uses Fractional Half-Kelly with volatility calibration:

```
kelly = 0.5 * (win_rate - ((1 - win_rate) / risk_reward_ratio))
volatility_scalar = max(0, 1 - ((atr_current / atr_30d_avg) - 1))
position_pct = kelly * volatility_scalar
```

**When tanking is detected** (price < 200 EMA or bearish 20/50 cross):
- `position_pct = 0` (zero allocation)
- All buy signals suppressed
- Immediate liquidation recommended

### Recommendation Engine (Balance-Aware Sizing)

The `/api/portfolio/recommendation` endpoint computes position size from three independent parameters:

```
stop_distance   = TRAILING_STOP_PCT × ATR_STOP_MULTIPLIER    (e.g., 0.02 × 1.5 = 3%)
max_loss_amount = balance × LOSS_TOLERANCE_PCT                (risk budget)
risk_per_unit   = current_price × stop_distance               ($ risked per share)
quantity        = max_loss_amount / risk_per_unit              (position size)
position_pct    = min(quantity × price / balance, 100%)        (capped at 100%)
```

With defaults (`TRAILING_STOP_PCT=0.02`, `ATR_STOP_MULTIPLIER=1.5`, `LOSS_TOLERANCE_PCT=0.02`):
- Stop at 3% from entry → ~66.7% of balance allocated
- Higher loss tolerance → larger position (up to 100% cap)
- Higher ATR multiplier → wider stop → smaller position per unit of risk

## Directory Structure

```
market-analysis/
├── docker-compose.yml          # Orchestration
├── .env.example                # Configuration template
├── README.md                   # This file
├── scripts/
│   └── setup.py                # Interactive credential setup
└── services/
    ├── data-ingestion/         # Service A
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   └── app/
    ├── quant-engine/           # Service B
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   ├── app/
    │   └── tests/
    ├── portfolio-engine/       # Service C
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   └── app/
    ├── notification-gateway/   # Notification sidecar
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   └── main.py
    └── frontend/               # Service D
        ├── Dockerfile
        ├── nginx.conf
        ├── package.json
        └── src/
```
