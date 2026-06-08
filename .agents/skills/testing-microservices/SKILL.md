---
name: testing-microservices
description: Test the market-analysis microservices architecture end-to-end. Use when verifying service startup, inter-service communication, risk model math, UI features, onboarding, or Docker builds.
---

# Testing the Microservices Architecture

## Architecture Overview

4 services on a shared Docker `trading_network` bridge:
- **Service A (data-ingestion)**: Port 8000 — price streaming, watchlist CRUD, OHLCV storage
- **Service B (quant-engine)**: Port 8001 — EMA/RSI/ATR analysis, Half-Kelly sizing, tanking detection
- **Service C (portfolio-engine)**: Port 8002 — paper trading ($10K), capital guardrail, trade execution, credential management
- **Service D (frontend + notification-gateway)**: Port 3000 (nginx) + Port 8003 — React dashboard, Discord/Telegram alerts

## Quick Validation Steps

### 1. Pytest Suite (Risk Models)
```bash
cd services/quant-engine && PYTHONPATH=. pytest tests/test_risk_models.py -v
```
Expect: 19/19 passed. Key assertions:
- Crash scenario -> `optimal_position_usd == 0.0`
- ATR at 2x average -> volatility scalar == 0.0
- Tanking (price < 200 EMA or bearish 20/50 cross) -> buy suppressed, liquidation recommended

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

### 6. Inter-Service Communication (B -> A)
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

### 7. Credential Save (UI-Based Setup)
```bash
curl -s -X POST http://localhost:8002/api/settings/credentials/save \
  -H "Content-Type: application/json" \
  -d '{"credentials": {"BINANCE_API_KEY": "test_key_123"}}'
```
Expect: `{"saved": ["BINANCE_API_KEY"], "message": "Credentials saved..."}`.

### 8. Onboarding Status
```bash
curl -s http://localhost:8002/api/settings/onboarding
```
Expect: `{"completed": false/true, "has_credentials": ..., "has_assets": ...}`.

### 9. Docker Build
```bash
docker compose build
```
Expect: All 5 services build successfully.

### 10. Full Docker Compose (requires .env)
```bash
docker compose up --build
```
Dashboard at http://localhost:3000. Requires internet for live market data.

## Frontend Testing (Browser)

### Onboarding Flow (First-Time User)
1. Clear localStorage: `localStorage.removeItem("onboarding_complete")`
2. Reload the page — Getting Started wizard should appear
3. Walk through: Welcome -> Market Data -> Notifications -> Watchlist -> Done
4. Each step has Skip and Save & Continue buttons
5. After completing, dashboard should load. Wizard should not appear again on reload.

### Watchlist Panel
- Add button stays inside the panel boundary (no clipping at 1024px width)
- Enter key works in Ticker and Name fields to add
- Inputs wrap to a second row on narrow viewports

### Settings Tab — Credential Forms
- Each service has a Configure/Update button
- Clicking opens an inline form with help text
- Saving shows success message and refreshes status indicators

### Help & Docs Tab
- 7 sections: How It Works, Watchlist, Trading Signals, Paper Trading, Risk Management, Configuration, Glossary
- All content is beginner-friendly (no assumed trading knowledge)

### Setup Wizard Re-Entry
- Click "Setup Wizard" in the header to re-run the onboarding at any time

## Important Notes

- **External APIs blocked in sandbox**: Binance, Alpaca, yfinance are unreachable in CI/sandbox.
- **Service B env var**: When running locally, set `DATA_INGESTION_URL=http://localhost:8000`.
- **Manual Trading**: Users execute trades manually based on system recommendations.
- **Port conflicts**: Kill existing processes before starting (`pkill -f 'uvicorn.*800[0-3]'`).
- **Credential security**: Credentials are written to .env (chmod 600) and never committed to git.
- **Vite proxy**: The dev server proxies `/api/settings` to portfolio-engine:8002. When testing locally, override Docker hostnames with localhost in vite.config.ts.
