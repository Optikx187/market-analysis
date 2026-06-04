# Market Analysis Platform

A production-ready, local-first web application for real-time cryptocurrency and stock market monitoring, quantitative signal generation, virtual paper trading, and dual-channel notifications (Discord + Telegram).

**Capital preservation is the absolute priority.** The system is heavily risk-averse, focusing on maximizing win-rate and minimizing drawdown through strict mathematical guardrails. It always prioritizes "no trade" over a risky or uncertain trade.

## Features

- **Real-Time Price Monitoring** — WebSocket connections to Binance for crypto, yfinance for stocks
- **Quantitative Signal Generation** — EMA 20/50/200, RSI 14, ATR 14 trend-following + momentum strategy
- **Capital Preservation Guardrails**:
  - Minimum 1:3 risk-to-reward ratio on every signal
  - Hard stop-loss: Entry Price − (1.5 × ATR)
  - Trailing stop at +2% profit
  - Volatility filter: suppresses BUY signals when ATR > 2× 30-day average
- **Paper Trading Simulator** — Virtual $100,000 portfolio with position sizing, PnL tracking, equity curve
- **Dual Notifications** — Discord Webhook + Telegram Bot alerts on every signal
- **Backtesting Engine** — Validate strategy against historical data with drawdown metrics
- **Dashboard** — React + Tailwind CSS + Shadcn UI with watchlist, portfolio, signals, trades, logs

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19, TypeScript, Tailwind CSS v4, Shadcn UI, Recharts, Vite |
| Backend | Python 3.12, FastAPI, SQLAlchemy (async), aiosqlite |
| Database | SQLite |
| Data | Binance API (crypto), yfinance (stocks), WebSockets |
| Notifications | Telegram Bot API, Discord Webhooks |
| Testing | pytest |

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 20+
- npm

### 1. Clone the Repository

```bash
git clone https://github.com/Optikx187/market-analysis.git
cd market-analysis
```

### 2. Backend Setup

```bash
cd backend

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment (copy and edit .env)
cp .env.example .env
# Edit .env with your API keys (see Configuration section below)

# Run the backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Run development server (proxies /api to backend)
npm run dev
```

The frontend runs at `http://localhost:5173` and proxies API calls to the backend at `http://localhost:8000`.

### 4. Run Tests

```bash
cd backend
source venv/bin/activate
python -m pytest tests/ -v
```

## Configuration

### Environment Variables

Create a `.env` file in the `backend/` directory:

#### Telegram Bot Setup

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the **Bot Token** → `TELEGRAM_BOT_TOKEN`
4. Start a chat with your bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates`
5. Find your **Chat ID** → `TELEGRAM_CHAT_ID`

#### Discord Webhook Setup

1. Open your Discord server
2. Go to **Channel Settings** → **Integrations** → **Webhooks**
3. Click **New Webhook**, name it, and copy the **Webhook URL** → `DISCORD_WEBHOOK_URL`

#### Market API Keys (Optional)

- **Binance**: Free tier API key from [binance.com/en/my/settings/api-management](https://www.binance.com/en/my/settings/api-management)
- **Alpaca**: Free paper trading API from [alpaca.markets](https://alpaca.markets/)

> **Note:** The app works without API keys using public endpoints. Keys enable enhanced rate limits and features.

### Strategy Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `RISK_REWARD_RATIO` | 3.0 | Minimum risk-to-reward ratio |
| `ATR_STOP_MULTIPLIER` | 1.5 | Stop-loss = Entry − (multiplier × ATR) |
| `TRAILING_STOP_PCT` | 0.02 | Trailing stop triggers at 2% profit |
| `ATR_VOLATILITY_THRESHOLD` | 2.0 | Suppress BUY signals when ATR > threshold × 30d avg |
| `INITIAL_BALANCE` | 100000 | Starting virtual portfolio balance |

## Signal Logic

### BUY Signal (all conditions must be true)

1. Price is **above** the 200 EMA (bullish macro trend)
2. EMA 20 **crosses above** EMA 50 (golden cross)
3. RSI is **between 45–55** (stable, not overbought)
4. ATR is **not** > 2× its 30-day average (volatility filter)

### SELL Signal (any condition triggers)

1. Price **crosses below** EMA 50, OR
2. RSI **exceeds 75** (overbought)

### Risk Guardrails

- Every signal includes a **hard stop-loss**: `Entry − 1.5 × ATR`
- Minimum **1:3 risk-to-reward** ratio enforced
- **Trailing stop** locks profits at +2%
- **Volatility filter** suppresses all BUY signals in erratic markets

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/assets` | List watchlist assets |
| POST | `/api/assets` | Add asset to watchlist |
| DELETE | `/api/assets/{ticker}` | Remove asset from watchlist |
| GET | `/api/assets/search?q=` | Search for tickers |
| GET | `/api/candles/{ticker}` | Get OHLCV candle data |
| POST | `/api/data/refresh/{ticker}` | Refresh historical data |
| POST | `/api/signals/analyze/{ticker}` | Run analysis and generate signal |
| GET | `/api/signals` | List generated signals |
| GET | `/api/portfolio` | Get portfolio metrics |
| GET | `/api/trades` | List all trades |
| GET | `/api/trades/open` | List open trades |
| POST | `/api/backtest` | Run backtest on historical data |
| GET | `/api/logs` | System logs |

## Project Structure

```
market-analysis/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py          # FastAPI app, routes, lifespan
│   │   ├── config.py         # Settings from .env
│   │   ├── database.py       # SQLAlchemy async engine
│   │   ├── models.py         # ORM models (Asset, Candle, Signal, Trade, Portfolio)
│   │   ├── schemas.py        # Pydantic request/response schemas
│   │   ├── quant_engine.py   # EMA, RSI, ATR, signal evaluation
│   │   ├── data_ingestion.py # Binance + yfinance data fetching, WebSocket manager
│   │   ├── paper_trading.py  # Virtual portfolio, trade execution, PnL
│   │   ├── notifications.py  # Telegram + Discord dual-channel alerts
│   │   └── backtester.py     # Historical strategy simulation
│   ├── tests/
│   │   └── test_backtest.py  # pytest: indicators, signals, backtest, risk guardrails
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── ui/            # Shadcn UI components
│   │   │   ├── WatchlistPanel.tsx
│   │   │   ├── PortfolioPanel.tsx
│   │   │   ├── SignalsPanel.tsx
│   │   │   ├── TradesPanel.tsx
│   │   │   ├── BacktestPanel.tsx
│   │   │   ├── LogsPanel.tsx
│   │   │   └── SettingsPanel.tsx
│   │   ├── lib/
│   │   │   ├── api.ts         # API client
│   │   │   └── utils.ts       # Utility functions
│   │   ├── App.tsx            # Main dashboard layout
│   │   ├── main.tsx           # Entry point
│   │   └── index.css          # Tailwind + theme
│   ├── package.json
│   ├── vite.config.ts
│   └── tsconfig.json
└── README.md
```

## Default Watchlist

The app seeds these assets on first run:

- **SPY** — S&P 500 ETF (Stock)
- **BTC** — Bitcoin (Crypto)
- **ETH** — Ethereum (Crypto)

You can add any crypto or stock ticker through the dashboard.

## Notification Format

```
🚨 [SIGNAL] BTC
Direction: 🟢 BUY
Trigger Price: $67,432.50
Reason: EMA 20/50 Golden Cross + Price above EMA 200 + RSI in stable zone
Strict Stop-Loss: $65,891.23 (Exit immediately if hit)
Target Profit: $72,065.31
[Paper Trading] Executed simulated position for virtual portfolio.
```

## License

MIT
