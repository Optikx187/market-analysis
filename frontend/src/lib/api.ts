import axios from "axios";

const api = axios.create({
  baseURL: "/api",
});

export interface Asset {
  id: number;
  ticker: string;
  name: string;
  asset_type: string;
  is_active: boolean;
}

export interface Signal {
  id: number;
  ticker: string;
  direction: string;
  trigger_price: number;
  stop_loss: number;
  target_price: number;
  reason: string | null;
  risk_reward: number | null;
  atr_value: number | null;
  rsi_value: number | null;
  suppressed: boolean;
  created_at: string | null;
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

export interface BacktestResult {
  ticker: string;
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
  max_drawdown: number;
  profit_factor: number;
  initial_balance: number;
  final_balance: number;
  equity_curve: number[];
  trades: Array<{
    direction: string;
    entry_price: number;
    exit_price: number;
    pnl: number;
    pnl_pct: number;
  }>;
}

export interface SystemLog {
  id: number;
  level: string;
  message: string;
  created_at: string | null;
}

export const fetchAssets = () => api.get<Asset[]>("/assets").then((r) => r.data);

export const addAsset = (ticker: string, name: string, asset_type: string) =>
  api.post<Asset>("/assets", { ticker, name, asset_type }).then((r) => r.data);

export const removeAsset = (ticker: string) =>
  api.delete(`/assets/${ticker}`).then((r) => r.data);

export const fetchCandles = (ticker: string) =>
  api.get(`/candles/${ticker}`).then((r) => r.data);

export const refreshData = (ticker: string) =>
  api.post(`/data/refresh/${ticker}`).then((r) => r.data);

export const analyzeSignal = (ticker: string) =>
  api.post<Signal | null>(`/signals/analyze/${ticker}`).then((r) => r.data);

export const fetchSignals = () =>
  api.get<Signal[]>("/signals").then((r) => r.data);

export const fetchPortfolio = () =>
  api.get<Portfolio>("/portfolio").then((r) => r.data);

export const fetchTrades = () =>
  api.get<Trade[]>("/trades").then((r) => r.data);

export const fetchOpenTrades = () =>
  api.get<Trade[]>("/trades/open").then((r) => r.data);

export const runBacktest = (ticker: string) =>
  api.post<BacktestResult>("/backtest", { ticker }).then((r) => r.data);

export const fetchLogs = () =>
  api.get<SystemLog[]>("/logs").then((r) => r.data);

export const searchTickers = (q: string) =>
  api.get<Array<{ ticker: string; name: string; asset_type: string }>>(
    `/assets/search?q=${q}`
  ).then((r) => r.data);

export default api;
