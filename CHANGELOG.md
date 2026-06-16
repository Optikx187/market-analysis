# Changelog

All notable changes to Market Analysis are documented here.

## [2.2.0] — 2026-06-16

### Added
- **Telegram bot trade replies** — Reply to alerts with `/bought` or `/sold` commands to log trades directly from Telegram (#11)
  - `/bought BTC 65000 0.1` → logs BUY 0.1 BTC @ $65,000
  - `/sold ETH 3500 1` → logs SELL 1 ETH @ $3,500 with SELL-specific defaults
  - `/trades` → lists open trades from portfolio
  - Reply trade history visible in Settings → "Telegram Trade Replies" section
- **System health monitoring** — Background health check loop pings Binance/Yahoo every 60s (#12)
  - Live connectivity indicators (online/offline per provider) in Settings → "System Health"
  - Downtime log tracking offline → online transitions
  - Auto-backfill of historical data when a provider reconnects after downtime
  - Uptime counter and service start time display

### Fixed
- **Direction validation** — Manual trade endpoint now rejects SUPPRESSED and invalid directions with HTTP 400

## [2.1.0] — 2026-06-08

### Added
- **Notification channel toggles** — Enable/disable Telegram and Discord independently via Settings UI (#13)
- **Alert timestamps** — Alerts now display generation time and last successful API call timestamps (#9)
- **App version display** — Version shown in UI footer and logged to browser console on startup (#14)
- **CHANGELOG.md** — This file (#14)
- **System status endpoint** — `/api/status` returns service uptime and last API success times

### Fixed
- **Recommendation math** — `LOSS_TOLERANCE_PCT` now correctly affects position sizing; stop distance uses `TRAILING_STOP_PCT` as independent base
- **Removed `robinhood_buying_power`** column from AlertLog model (#15)

## [2.0.0] — 2026-06-08

### Added
- **Crypto name resolution** — Built-in mapping for 21 popular coins (BTC→Bitcoin, ETH→Ethereum, etc.) (#4)
- **Test notification endpoint** — `POST /api/notify/test` with UI button to verify Discord/Telegram (#5)
- **Balance update** — Configurable portfolio balance via UI, accounts for locked capital in open positions (#6)
- **Trade recommendations** — Balance-aware sizing with configurable loss tolerance and ATR-based stops (#6)
- **Credential management** — Save, verify, and mask API keys via Settings UI
- **Onboarding wizard** — Getting Started flow for first-time users
- **Environment settings UI** — Edit risk parameters (RISK_REWARD_RATIO, ATR_STOP_MULTIPLIER, etc.) from dashboard
- **Full API documentation** in README with all endpoints

### Removed
- **Robinhood integration** — All references removed from code, UI, docs, and configuration (#7)

### Fixed
- **Portfolio AttributeError** — Changed `portfolio_row.total_equity` → `portfolio_row.equity`
- **Vacuous truth in test notifications** — Added `any_configured` guard
- **Missing `open_positions`** field in portfolio response
- **Watchlist autofill** — Now works when Binance is unreachable by checking `name !== ticker`

## [1.0.0] — Initial Release

### Features
- 4-service microservices architecture (Data Ingestion, Quant Engine, Portfolio Engine, Frontend + Notifications)
- Docker Compose orchestration
- Half-Kelly position sizing with volatility calibration
- Tanking detection (200 EMA, bearish 20/50 cross)
- Paper trading with $10K starting capital
- Discord + Telegram dual-broadcast notifications
- React/Vite/Tailwind dashboard
