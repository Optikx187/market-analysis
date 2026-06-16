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
Expect: `balance: 10000.0`, `equity: 10000.0`, `open_positions: 0`, `win_count: 0`, `loss_count: 0`.

### 5b. Crypto Symbol Lookup
```bash
curl -s "http://localhost:8000/api/symbols/lookup/BTC?asset_type=crypto"
```
Expect: `{"ticker": "BTC", "name": "Bitcoin", "asset_type": "crypto", "recognized": true/false}`.
The `name` field should return a friendly name from the built-in CRYPTO_NAMES map even if Binance is unreachable (`recognized: false`).

### 5c. Test Notifications
```bash
curl -s -X POST http://localhost:8003/api/notify/test
```
Expect: `{"success": true, "results": {"telegram": {"configured": true, "sent": true}, "discord": {"configured": true, "sent": true}}}`.
If credentials are not set: `success: false` and unconfigured channels show `configured: false`.

### 5d. Balance Update
```bash
curl -s -X POST http://localhost:8002/api/portfolio/balance \
  -H "Content-Type: application/json" \
  -d '{"balance": 50000}'
```
Expect: `{"previous_balance": 10000.0, "new_balance": 50000.0, "equity": 50000.0, "locked_in_positions": 0.0, ...}`.
If open trades exist, `new_balance` will be less than the requested value (capital locked in positions is deducted).

### 5e. Trade Recommendation
```bash
curl -s "http://localhost:8002/api/portfolio/recommendation?ticker=ETH&current_price=3500"
```
Expect (with defaults TRAILING_STOP_PCT=0.02, ATR_STOP_MULTIPLIER=1.5, LOSS_TOLERANCE_PCT=0.02):
- `position_pct_of_balance: 66.67` (NOT 100%)
- `suggested_stop_loss: 3395.0` (3% below entry)
- `suggested_target: 3815.0`

Changing LOSS_TOLERANCE_PCT should affect position size: higher tolerance → larger position (capped at 100%).

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

### 9b. Docker Image Verification
When Dockerfiles are modified (e.g., new files added via COPY), verify the file is actually in the image:
```bash
# Build individual service image
docker build -t <service>-test services/<service>

# Verify module imports work inside the container
docker run --rm <service>-test python -c "import <module>; print('OK')"

# To confirm the old Dockerfile was broken, build without the fix and test:
# docker build -t <service>-broken -f - services/<service> <<'EOF'
# ... old Dockerfile contents ...
# EOF
# docker run --rm <service>-broken python -c "import <module>"  # should fail
```

### 10. Full Docker Compose (requires .env)
```bash
docker compose up --build
```
Dashboard at http://localhost:3000. Requires internet for live market data.

### 11. System Health / Connectivity Status
```bash
curl -s http://localhost:8000/api/status | python3 -m json.tool
```
Expect (after ~10s for health check loop to run):
- `connectivity.yahoo.online: true` — Yahoo uses `any_response_ok=True` so any HTTP response (including 403/429) counts as reachable
- `connectivity.binance.online: true/false` — depends on Binance API accessibility from your environment
- `last_api_calls` shows timestamps of last successful API calls
- `downtime_log` shows recent offline→online transitions

**Note**: Yahoo Finance endpoints often return 429 (rate limit) or 403 (geo-restriction). The health check treats any HTTP response as "reachable" — only network/DNS failures mark Yahoo as offline. This matches how `yfinance` works internally. To test this logic directly:
```python
from app.main import _check_provider_health
# any_response_ok=True + 429 response → True (reachable)
# any_response_ok=False + 429 response → False (old broken behavior)
# any_response_ok=True + unreachable host → False (correct)
```

## Frontend Testing (Browser)

### Onboarding Flow (First-Time User)
1. Clear localStorage: `localStorage.removeItem("onboarding_complete")`
2. Reload the page — Getting Started wizard should appear
3. Walk through: Welcome -> Market Data -> Notifications -> Watchlist -> Done
4. Each step has Skip and Save & Continue buttons
5. After completing, dashboard should load. Wizard should not appear again on reload.

### Watchlist Panel
- Typing a crypto ticker (e.g., "ETH") auto-fills the Name field with a friendly name (e.g., "Ethereum")
- Changing the ticker updates the name without needing to clear the field first
- Add button validates with Binance before adding (shows error if ticker unverified)
- Add button stays inside the panel boundary (no clipping at 1024px width)
- Enter key works in Ticker and Name fields to add
- Inputs wrap to a second row on narrow viewports

### Settings Tab — Credential Forms & Test Notifications
- Each service (Binance, Alpaca, Telegram, Discord) has a Configure/Update button
- Clicking opens an inline form with help text
- Saving shows success message and refreshes status indicators
- "Send Test Notification" button sends test message to configured Discord + Telegram
- Environment Settings section allows editing risk parameters (RISK_REWARD_RATIO, ATR_STOP_MULTIPLIER, etc.)
- "System Health" section shows live provider status (Binance/Yahoo online/offline)
- "Telegram Trade Replies" section shows trades submitted via bot (auto-refreshes every 30s)

### Portfolio Panel — Balance Update
- "Update Balance" button opens inline editor with current balance
- Enter new amount and click Save → green confirmation message
- Balance, equity, and equity curve all update immediately

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
- **Docker inter-service URLs**: In Docker Compose, services reference each other by service name (e.g., `http://portfolio-engine:8002`), not localhost. When adding new modules to a service, ensure the Dockerfile copies all required files.
