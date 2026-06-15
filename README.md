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
- Settings panel for credential management and environment configuration

## Quick Start

### Prerequisites
- Docker & Docker Compose installed
- (Optional) API keys for Binance, Alpaca, Telegram, Discord

### 1. Configure Environment

Run the interactive setup script to configure all API keys and credentials:

```bash
python3 scripts/setup.py
```

This will prompt for Binance, Alpaca, Telegram, and Discord credentials
and securely store them in a local `.env` file (gitignored, never committed).

Alternatively, copy and edit manually:

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 2. Build & Run

```bash
docker-compose up --build
```

### 3. Access the Dashboard

Open **http://localhost:3000** in your browser.

### 4. Stop Services

```bash
docker-compose down
```

### 5. Run Risk Model Tests

```bash
docker-compose run --rm quant-engine pytest tests/ -v
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

## Service Communication

| From → To | Protocol | Purpose |
|-----------|----------|---------|
| Frontend → All Services | HTTP (nginx proxy) | API gateway |
| Quant Engine → Data Ingestion | HTTP | Fetch candle data |
| Portfolio Engine → Quant Engine | HTTP | Request signal analysis |
| Notification Gateway → External | HTTPS | Discord/Telegram delivery |

## Boot Order

1. **Data Ingestion** starts first (health check: `/health`)
2. **Quant Engine** waits for Data Ingestion healthy
3. **Portfolio Engine** waits for Data Ingestion healthy
4. **Notification Gateway** waits for Data Ingestion healthy
5. **Frontend** waits for all backend services

## Configuration Reference

| Variable | Service | Description |
|----------|---------|-------------|
| `BINANCE_API_KEY` | A | Binance API key for crypto data |
| `BINANCE_API_SECRET` | A | Binance API secret |
| `ALPACA_API_KEY` | A | Alpaca API key for stock data |
| `ALPACA_API_SECRET` | A | Alpaca API secret |
| `TELEGRAM_BOT_TOKEN` | Gateway | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Gateway | Telegram chat ID |
| `DISCORD_WEBHOOK_URL` | Gateway | Discord webhook URL |
| `RISK_REWARD_RATIO` | B, C | Min risk:reward (default: 3.0) |
| `ATR_STOP_MULTIPLIER` | B, C | ATR multiplier for stop distance (default: 1.5) |
| `ATR_VOLATILITY_THRESHOLD` | B | ATR suppression multiplier (default: 2.0) |
| `TRAILING_STOP_PCT` | B, C | Base stop distance as decimal (default: 0.02 = 2%) |
| `INITIAL_BALANCE` | C | Paper trading starting capital (default: 10000) |
| `LOSS_TOLERANCE_PCT` | C | Max loss per trade as % of balance (default: 0.02 = 2%) |

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
