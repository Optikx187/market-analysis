---
name: testing-microservices
description: Test the market-analysis microservices architecture end-to-end. Use when verifying service startup, inter-service communication, risk model math, or Docker builds.
---

# Testing the Microservices Architecture

## Architecture Overview

4 services on a shared Docker `trading_network` bridge:
- **Service A (data-ingestion)**: Port 8000 — price streaming, watchlist CRUD, OHLCV storage
- **Service B (quant-engine)**: Port 8001 — EMA/RSI/ATR analysis, Half-Kelly sizing, tanking detection
- **Service C (portfolio-engine)**: Port 8002 — paper trading ($10K), Robinhood guardrail, trade execution
- **Service D (frontend + notification-gateway)**: Port 3000 (nginx) + Port 8003 — React dashboard, Discord/Telegram alerts

## Quick Validation Steps

### 1. Pytest Suite (Risk Models)
```bash
cd services/quant-engine && PYTHONPATH=. pytest tests/test_risk_models.py -v
```
Expect: 19/19 passed. Key assertions:
- Crash scenario → `optimal_position_usd == 0.0`
- ATR at 2x average → volatility scalar == 0.0
- Tanking (price < 200 EMA or bearish 20/50 cross) → buy suppressed, liquidation recommended

### 2. Service Startup (Local, No Docker)

Start services in order (A first, then B/C/D):
```bash
# Service A
cd services/data-ingestion && PYTHONPATH=. uvicorn app.main:app --port 8000 &

# Service B (point to local Service A)
cd services/quant-engine && DATA_INGESTION_URL=http://localhost:8000 PYTHONPATH=. uvicorn app.main:app --port 8001 &

# Service C
cd services/portfolio-engine && PYTHONPATH=. uvicorn app.main:app --port 8002 &

# Service D (notification gateway)
cd services/notification-gateway && PYTHONPATH=. uvicorn main:app --port 8003 &
```

### 3. Health Checks
```bash
curl -s http://localhost:8000/health  # {"status":"healthy","service":"data-ingestion"}
curl -s http://localhost:8001/health  # {"status":"healthy","service":"quant-engine"}
curl -s http://localhost:8002/health  # {"status":"healthy","service":"portfolio-engine"}
curl -s http://localhost:8003/health  # {"status":"healthy","service":"notification-gateway"}
```

### 4. Watchlist (Starts Empty)
```bash
curl -s http://localhost:8000/api/assets
```
Expect: Empty JSON array `[]`. Users add tickers via UI or API.

### 5. Portfolio Initialization
```bash
curl -s http://localhost:8002/api/portfolio
```
Expect: `balance: 10000.0`, `equity: 10000.0`, `win_count: 0`, `loss_count: 0`.

### 6. Inter-Service Communication (B → A)
```bash
# First add an asset
curl -s -X POST http://localhost:8000/api/assets \
  -H "Content-Type: application/json" \
  -d '{"ticker": "BTC", "name": "Bitcoin", "asset_type": "crypto"}'

# Then analyze
curl -s -X POST http://localhost:8001/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"ticker": "BTC", "available_capital": 10000}'
```
Expect: Valid JSON with `ticker`, `direction`, `status`, `optimal_size_usd`, `kelly_pct`.

### 7. Docker Build
```bash
docker compose build
```
Expect: All 5 services build successfully.

### 8. Full Docker Compose (requires .env)
```bash
python3 scripts/setup.py  # Interactive credential setup
docker compose up --build
```
Dashboard at http://localhost:3000. Requires internet for live market data.

## Important Notes

- **No recording needed**: All testing is shell-based (pytest, curl, docker build).
- **External APIs blocked in sandbox**: Binance, Alpaca, yfinance are unreachable in CI/sandbox.
- **Service B env var**: When running locally, set `DATA_INGESTION_URL=http://localhost:8000`.
- **Robinhood**: Requires real credentials. Trades execute through Robinhood when connected.
- **Port conflicts**: Kill existing processes before starting (`pkill -f 'uvicorn.*800[0-3]'`).
