import axios from "axios";

const api = axios.create({ baseURL: "/api" });

export interface Asset {
  id: number;
  ticker: string;
  name: string;
  asset_type: string;
  is_active: boolean;
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
  robinhood_buying_power: number | null;
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
  robinhood_buying_power: number | null;
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

// Service B — Quant Engine
export const analyzeSignal = (ticker: string, capital: number = 10000) =>
  api.post<Signal | null>("/analyze", { ticker, available_capital: capital }).then((r) => r.data);
export const fetchRiskProfile = (ticker: string, capital: number = 10000) =>
  api.post<RiskProfile | null>("/risk-profile", { ticker, available_capital: capital }).then((r) => r.data);

// Service C — Portfolio Engine
export const fetchPortfolio = () => api.get<Portfolio>("/portfolio").then((r) => r.data);
export const fetchTrades = () => api.get<Trade[]>("/trades").then((r) => r.data);
export const fetchAlerts = () => api.get<AlertLog[]>("/alerts").then((r) => r.data);
export const processSignal = (signal: Signal) =>
  api.post<SignalDecision>("/process-signal", signal).then((r) => r.data);
export const fetchRobinhoodBalance = () =>
  api.get<{ buying_power: number | null; connected: boolean }>("/robinhood/balance").then((r) => r.data);

// Settings — Credential Status
export interface CredentialStatus {
  robinhood: boolean;
  binance: boolean;
  alpaca: boolean;
  telegram: boolean;
  discord: boolean;
}

export const fetchCredentialStatus = () =>
  api.get<CredentialStatus>("/settings/credentials/all").then((r) => r.data);

export const saveCredentials = (credentials: Record<string, string>) =>
  api.post<{ saved: string[]; message: string }>("/settings/credentials/save", { credentials }).then((r) => r.data);

export interface OnboardingStatus {
  completed: boolean;
  has_credentials: boolean;
  has_assets: boolean;
}

export const fetchOnboardingStatus = () =>
  api.get<OnboardingStatus>("/settings/onboarding").then((r) => r.data);

export default api;
