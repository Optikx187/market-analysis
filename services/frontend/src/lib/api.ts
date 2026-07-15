import axios from "axios";

const api = axios.create({ baseURL: "/api" });

export interface Asset {
  id: number;
  ticker: string;
  name: string;
  asset_type: string;
  is_active: boolean;
}

export interface Quote {
  ticker: string;
  name: string;
  asset_type: string;
  price: number | null;
  change_pct: number | null;
  volume: number | null;
  updated_at: string;
}

export interface SymbolLookup {
  ticker: string;
  name: string;
  asset_type: string;
  recognized: boolean;
}

export interface Signal {
  ticker: string;
  direction: string | null;
  status: string;
  trigger_price: number;
  stop_loss: number;
  target_price: number;
  reason: string;
  risk_reward: number;
  atr_value: number;
  rsi_value: number;
  suppressed: boolean;
  kelly_pct: number;
  optimal_size_usd: number;
  volatility_scalar: number;
}

export interface RiskProfile {
  ticker: string;
  is_tanking: boolean;
  tanking_reason: string | null;
  win_rate_30d: number;
  risk_reward_ratio: number;
  kelly_fraction: number;
  volatility_scalar: number;
  optimal_position_pct: number;
  optimal_position_usd: number;
  atr_current: number;
  atr_avg_30: number;
  ema_20: number;
  ema_50: number;
  ema_200: number;
  rsi: number;
  current_price: number;
  recommend_liquidate: boolean;
}

export interface Trade {
  id: number;
  ticker: string;
  direction: string;
  entry_price: number;
  exit_price: number | null;
  quantity: number;
  stop_loss: number;
  target_price: number;
  trailing_stop: number | null;
  status: string;
  pnl: number | null;
  pnl_pct: number | null;
  opened_at: string | null;
  closed_at: string | null;
}

export interface Portfolio {
  balance: number;
  equity: number;
  total_pnl: number;
  win_count: number;
  loss_count: number;
  win_rate: number;
  max_drawdown: number;
  profit_factor: number;
  peak_equity: number;
  open_positions?: number;
  equity_curve: Array<{ timestamp: string; equity: number }>;
}

export interface AlertLog {
  id: number;
  ticker: string;
  direction: string;
  status: string;
  trigger_price: number;
  stop_loss: number | null;
  target_price: number | null;
  optimal_size_usd: number | null;
  kelly_pct: number | null;
  capital_overspend: boolean;
  message: string | null;
  created_at: string | null;
}

export interface SignalDecision {
  ticker: string;
  direction: string;
  status: string;
  approved: boolean;
  trigger_price: number;
  stop_loss: number;
  target_price: number;
  optimal_size_usd: number;
  kelly_pct: number;
  capital_overspend: boolean;
  reason: string;
  paper_trade_executed: boolean;
}

// Service A — Data Ingestion
export const fetchAssets = () => api.get<Asset[]>("/assets").then((r) => r.data);
export const addAsset = (ticker: string, name: string, asset_type: string) =>
  api.post<Asset>("/assets", { ticker, name, asset_type }).then((r) => r.data);
export const removeAsset = (ticker: string) =>
  api.delete(`/assets/${ticker}`).then((r) => r.data);
export const fetchCandles = (ticker: string) =>
  api.get(`/candles/${ticker}`).then((r) => r.data);
export const refreshData = (ticker: string) =>
  api.post(`/assets/${ticker}/refresh`).then((r) => r.data);
export const refreshAllData = () =>
  api.post<{ refreshed: number; total: number; details: Record<string, { status: string; candles?: number; error?: string }> }>("/assets/refresh-all").then((r) => r.data);
export const fetchCandleCounts = () =>
  api.get<Record<string, number>>("/assets/candle-counts").then((r) => r.data);
export const exportAssets = () =>
  api.get<Array<{ ticker: string; name: string; asset_type: string }>>("/assets/export").then((r) => r.data);
export const importAssets = (items: Array<{ ticker: string; name: string; asset_type: string }>) =>
  api.post<{ added: string[]; reactivated: string[]; skipped: string[]; total_imported: number }>("/assets/import", items).then((r) => r.data);
export const fetchQuote = (ticker: string, asset_type: string = "stock") =>
  api.get<Quote>(`/quotes/${ticker}`, { params: { asset_type } }).then((r) => r.data);
export const lookupSymbol = (ticker: string, asset_type: string = "stock") =>
  api.get<SymbolLookup>(`/symbols/lookup/${ticker}`, { params: { asset_type } }).then((r) => r.data);

// Service B — Quant Engine
export const analyzeSignal = (ticker: string, capital: number = 10000, asset_type: string = "stock") =>
  api.post<Signal | null>("/analyze", { ticker, available_capital: capital, asset_type }).then((r) => r.data);
export const fetchRiskProfile = (ticker: string, capital: number = 10000) =>
  api.post<RiskProfile | null>("/risk-profile", { ticker, available_capital: capital }).then((r) => r.data);

// Service C — Portfolio Engine
export const fetchPortfolio = () => api.get<Portfolio>("/portfolio").then((r) => r.data);
export const fetchTrades = () => api.get<Trade[]>("/trades").then((r) => r.data);
export const fetchAlerts = () => api.get<AlertLog[]>("/alerts").then((r) => r.data);
export const processSignal = (signal: Signal) =>
  api.post<SignalDecision>("/process-signal", signal).then((r) => r.data);

// Settings — Credential Status
export interface CredentialStatus {
  [provider: string]: {
    configured: boolean;
    verified: boolean;
    configured_keys: string[];
    verified_keys: string[];
    masked: Record<string, string>;
    errors: Record<string, string>;
  };
}

export const fetchCredentialStatus = () =>
  api.get<CredentialStatus>("/settings/credentials/all").then((r) => r.data);

export const saveCredentials = (credentials: Record<string, string>) =>
  api.post<{ saved: string[]; skipped?: string[]; message: string }>("/settings/credentials/save", { credentials }).then((r) => r.data);

export const revealCredential = (key: string) =>
  api.post<{ key: string; value: string }>("/settings/credentials/reveal", { key }).then((r) => r.data);

export interface OnboardingStatus {
  completed: boolean;
  has_credentials: boolean;
  has_assets: boolean;
}

export const fetchOnboardingStatus = () =>
  api.get<OnboardingStatus>("/settings/onboarding").then((r) => r.data);

// Environment settings - viewable and adjustable
export interface EnvSetting {
  value: number;
  default: number;
  type: string;
  description: string;
}

export const fetchEnvSettings = () =>
  api.get<Record<string, EnvSetting>>("/settings/env").then((r) => r.data);

export const updateEnvSetting = (key: string, value: number) =>
  api.post<{ key: string; value: number; message: string }>("/settings/env", { key, value }).then((r) => r.data);

// Portfolio balance management
export const updateBalance = (balance: number) =>
  api.post<{ previous_balance: number; new_balance: number; message: string }>("/portfolio/balance", { balance }).then((r) => r.data);

export interface TradeRecommendation {
  ticker: string;
  account_balance: number;
  loss_tolerance_pct: number;
  max_loss_amount: number;
  current_price: number;
  suggested_stop_loss: number;
  suggested_target: number;
  suggested_quantity: number;
  suggested_position_usd: number;
  position_pct_of_balance: number;
  risk_reward_ratio: number;
}

export const fetchTradeRecommendation = (ticker: string, currentPrice: number) =>
  api.get<TradeRecommendation>("/portfolio/recommendation", { params: { ticker, current_price: currentPrice } }).then((r) => r.data);

// Notification testing
export interface TestNotificationResult {
  success: boolean;
  results: {
    telegram: { configured: boolean; sent: boolean };
    discord: { configured: boolean; sent: boolean };
  };
  message: string;
}

export const testNotifications = (message?: string) =>
  api.post<TestNotificationResult>("/notify/test", message ? { message } : {}).then((r) => r.data);

// Manual trade logging
export interface ManualTradeInput {
  ticker: string;
  direction: string;
  entry_price: number;
  quantity: number;
  stop_loss?: number;
  target_price?: number;
}

export const logManualTrade = (trade: ManualTradeInput) =>
  api.post<Trade>("/trades/manual", trade).then((r) => r.data);

// Close an open trade
export const closeTrade = (tradeId: number, exitPrice: number) =>
  api.post<Trade>(`/trades/${tradeId}/close`, { exit_price: exitPrice }).then((r) => r.data);

// Notification channel toggles
export interface ChannelStatus {
  configured: boolean;
  enabled: boolean;
}

export const fetchChannelStatus = () =>
  api.get<Record<string, ChannelStatus>>("/notify/channels").then((r) => r.data);

export const toggleChannel = (channel: string, enabled: boolean) =>
  api.post<{ channel: string; enabled: boolean; message: string }>("/notify/channels/toggle", { channel, enabled }).then((r) => r.data);

// System status
export interface ProviderConnectivity {
  online: boolean;
  last_checked: string | null;
  last_online: string | null;
  last_offline: string | null;
}

export interface DowntimeEntry {
  provider: string;
  went_offline: string;
  came_online: string;
}

export interface SystemStatus {
  service: string;
  started_at: string;
  current_time: string;
  last_api_calls: Record<string, string>;
  connectivity: Record<string, ProviderConnectivity>;
  downtime_log: DowntimeEntry[];
}

export const fetchSystemStatus = () =>
  api.get<SystemStatus>("/status").then((r) => r.data);

// Telegram bot reply trades
export interface ReplyTrade {
  timestamp: string;
  user: string;
  channel: string;
  ticker: string;
  direction: string;
  entry_price: number;
  quantity: number;
  result: Record<string, unknown>;
}

export interface ReplyTradesResponse {
  trades: ReplyTrade[];
  bot_active: boolean;
}

export const fetchReplyTrades = () =>
  api.get<ReplyTradesResponse>("/notify/reply-trades").then((r) => r.data);

// Phase 1: Scanner
export interface ScanResult {
  scanned: number;
  signals_found: number;
  notifications_sent: number;
  errors: number;
  signals: Array<{ ticker: string; direction: string; status: string; approved: boolean; suppressed: boolean }>;
  timestamp: string;
}

export interface ScannerStatus {
  enabled: boolean;
  interval_minutes: number;
  market_hours_only: boolean;
  last_scan_at: string | null;
  next_scan_at: string | null;
  last_scan_result: ScanResult | null;
  total_scans: number;
  total_signals_found: number;
}

export const triggerScan = () =>
  api.post<ScanResult>("/scan-all").then((r) => r.data);

export const fetchScannerStatus = () =>
  api.get<ScannerStatus>("/scanner/status").then((r) => r.data);

export const updateScannerConfig = (config: { enabled?: boolean; interval_minutes?: number; market_hours_only?: boolean }) =>
  api.post<{ message: string; enabled: boolean; interval_minutes: number; market_hours_only: boolean }>("/scanner/config", config).then((r) => r.data);

// Phase 2: Dashboard Summary
export interface DashboardSummary {
  balance: number;
  equity: number;
  total_pnl: number;
  total_pnl_pct: number;
  open_positions: number;
  todays_signals: number;
  todays_approved: number;
  win_rate: number;
  total_trades: number;
}

export const fetchDashboardSummary = () =>
  api.get<DashboardSummary>("/dashboard-summary").then((r) => r.data);

// Phase 3: Price Alerts
export interface PriceAlertItem {
  id: number;
  ticker: string;
  condition: string;
  threshold: number;
  triggered: boolean;
  created_at: string | null;
  triggered_at: string | null;
}

export const fetchPriceAlerts = () =>
  api.get<PriceAlertItem[]>("/price-alerts").then((r) => r.data);

export const createPriceAlert = (ticker: string, condition: string, threshold: number) =>
  api.post<PriceAlertItem>("/price-alerts", { ticker, condition, threshold }).then((r) => r.data);

export const deletePriceAlert = (id: number) =>
  api.delete(`/price-alerts/${id}`).then((r) => r.data);

export const checkPriceAlerts = () =>
  api.get<{ checked: number; triggered: Array<{ id: number; ticker: string; condition: string; threshold: number; current_price: number }> }>("/price-alerts/check").then((r) => r.data);

// Phase 5: Backtesting
export interface BacktestResult {
  ticker: string;
  period: string;
  total_signals: number;
  wins: number;
  losses: number;
  win_rate: number;
  avg_pnl_pct: number;
  max_drawdown_pct: number;
  final_equity: number;
  total_return_pct: number;
  trades: Array<{
    direction: string;
    entry: number;
    exit: number;
    entry_date: string;
    exit_date: string;
    pnl_pct: number;
    outcome: string;
    reason: string;
  }>;
  equity_curve: Array<{ date: string; equity: number }>;
}

export const runBacktest = (ticker: string, period: string = "6mo", capital: number = 10000) =>
  api.post<BacktestResult>("/backtest", { ticker, period, available_capital: capital }).then((r) => r.data);

// Phase 6: Earnings
export interface EarningsData {
  ticker: string;
  has_earnings: boolean;
  next_earnings_date: string | null;
  earnings: Record<string, unknown> | null;
}

export interface UpcomingEarnings {
  upcoming: Array<{ ticker: string; name: string; earnings_date: string; days_until: number }>;
  checked: number;
}

export const fetchEarnings = (ticker: string) =>
  api.get<EarningsData>(`/earnings/${ticker}`).then((r) => r.data);

export const fetchUpcomingEarnings = () =>
  api.get<UpcomingEarnings>("/earnings/upcoming/all").then((r) => r.data);

export default api;
