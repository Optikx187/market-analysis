import axios from "axios";

const api = axios.create({ baseURL: "/api" });

export const apiErrorMessage = (error: unknown, fallback: string): string => {
  if (axios.isAxiosError(error)) {
    const detail: unknown = error.response?.data?.detail;
    if (typeof detail === "string" && detail) return detail;
  }
  return fallback;
};

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

export interface DataQuality {
  ticker: string;
  asset_type: string;
  interval: string;
  status: "healthy" | "warning" | "stale" | "insufficient" | "invalid";
  is_eligible: boolean;
  candle_count: number;
  latest_timestamp: string | null;
  age_hours: number | null;
  stale: boolean;
  duplicate_timestamps: number;
  missing_periods: number;
  invalid_timestamps: number;
  invalid_ohlc: number;
  anomaly_count: number;
  issues: string[];
}

export interface MarketRegime {
  trend: string;
  volatility: string;
  breadth: string;
  risk: string;
  label: string;
  confidence: number;
  breadth_pct_above_50: number | null;
  asset_type: string;
  session_profile: string;
}

export interface TimeframeAgreement {
  score: number;
  available: boolean;
  available_timeframes: number;
  expected_trend: string;
  details: Record<string, {
    trend: string;
    available: boolean;
    agrees: boolean;
    weight_pct: number;
  }>;
  asset_type: string;
  session_profile: string;
}

export interface RegimeControls {
  allowed: boolean;
  fit_score: number;
  size_multiplier: number;
  reasons: string[];
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
  market_regime: MarketRegime;
  timeframe_agreement: TimeframeAgreement;
  regime_controls: RegimeControls;
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
  asset_type: string;
  sector: string;
  market_regime: string;
  volatility_regime: string;
  breadth_regime: string;
  risk_regime: string;
  regime_label: string;
  timeframe_agreement: number | null;
  strategy_name: string | null;
  strategy_version: string | null;
  timeframe: string | null;
  signal_confidence: number | null;
  planned_entry_price: number | null;
  planned_exit_price: number | null;
  planned_quantity: number | null;
  entry_fees: number;
  entry_slippage: number;
  exit_fees_total: number;
  exit_slippage_total: number;
  costs_total: number;
  realized_quantity: number;
  remaining_quantity: number;
  gross_pnl: number | null;
  mfe_usd: number | null;
  mae_usd: number | null;
  mfe_pct: number | null;
  mae_pct: number | null;
  excursion_status: string;
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

export interface RiskReason {
  code: string;
  message: string;
  actual?: number;
  limit?: number;
  requested?: number;
}

export interface RiskHeat {
  raw_risk_usd: number;
  correlation_penalty_usd: number;
  effective_risk_usd: number;
  raw_pct: number;
  effective_pct: number;
  limit_pct: number;
  utilization_pct: number;
}

export interface RiskExposure {
  ticker: Record<string, number>;
  sector: Record<string, number>;
  asset_class: Record<string, number>;
  direction: Record<string, number>;
  limits: {
    ticker_pct: number;
    sector_pct: number;
    asset_class_pct: number;
    direction_pct: number;
  };
  largest_concentration: { category: string; name: string; pct: number };
}

export interface RiskBreaker {
  active: boolean;
  daily_loss_active: boolean;
  weekly_loss_active: boolean;
  drawdown_active: boolean;
  daily_pnl: number;
  weekly_pnl: number;
  daily_loss_pct: number;
  weekly_loss_pct: number;
  current_drawdown_pct: number;
  daily_limit_pct: number;
  weekly_limit_pct: number;
  drawdown_limit_pct: number;
  allows_position_reduction: boolean;
  reasons: string[];
}

export interface PortfolioRiskSnapshot {
  positions: Array<{
    ticker: string;
    direction: string;
    asset_type: string;
    sector: string;
    notional_usd: number;
    risk_to_stop_usd: number;
    risk_to_stop_pct: number;
  }>;
  heat: RiskHeat;
  exposure: RiskExposure;
  correlation: {
    threshold: number;
    matrix: Record<string, Record<string, number | null>>;
    data_available: boolean;
    largest_cluster: string[];
    largest_cluster_pct: number;
    limit_pct: number;
    utilization_pct: number;
  };
  stress_tests: Array<{ name: string; description: string; estimated_pnl: number }>;
}

export interface PortfolioRisk extends PortfolioRiskSnapshot {
  breaker: RiskBreaker;
  equity: number;
}

export interface RiskDecision {
  approved: boolean;
  action: "approved" | "reduced" | "rejected" | "approve_reduction";
  requested_size_usd: number;
  recommended_size_usd: number;
  reasons: RiskReason[];
  breaker: RiskBreaker;
  before: PortfolioRiskSnapshot;
  after: PortfolioRiskSnapshot;
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
  approved: boolean;
  message: string | null;
  risk_decision_json: string | null;
  market_regime: string;
  volatility_regime: string;
  breadth_regime: string;
  risk_regime: string;
  regime_label: string;
  timeframe_agreement: number | null;
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
  risk_decision: RiskDecision;
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
export const fetchDataQuality = () =>
  api.get<Record<string, DataQuality>>("/data-quality").then((r) => r.data);
export const fetchTickerDataQuality = (ticker: string) =>
  api.get<DataQuality>(`/data-quality/${ticker}`).then((r) => r.data);
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
export const fetchPortfolioRisk = () => api.get<PortfolioRisk>("/portfolio/risk").then((r) => r.data);
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
  risk_decision: RiskDecision;
}

export const fetchTradeRecommendation = (
  ticker: string,
  currentPrice: number,
  direction: string = "BUY",
  assetType: string = "stock",
  sector: string = "Unclassified",
) =>
  api.get<TradeRecommendation>("/portfolio/recommendation", {
    params: { ticker, current_price: currentPrice, direction, asset_type: assetType, sector },
  }).then((r) => r.data);

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
  asset_type?: string;
  sector?: string;
  market_regime?: MarketRegime;
  timeframe_agreement?: TimeframeAgreement;
  strategy_name?: string;
  strategy_version?: string;
  timeframe?: string;
  signal_confidence?: number;
  signal_context?: Record<string, unknown>;
  execution_context?: Record<string, unknown>;
  planned_entry_price?: number;
  planned_exit_price?: number;
  planned_quantity?: number;
  entry_fees?: number;
  entry_slippage?: number;
}

export const fetchRegime = (ticker: string, assetType: string, direction: string) =>
  api.get<{
    ticker: string;
    direction: string;
    market_regime: MarketRegime;
    timeframe_agreement: TimeframeAgreement;
    regime_controls: RegimeControls;
  }>(`/regime/${encodeURIComponent(ticker)}`, {
    params: { asset_type: assetType, direction },
  }).then((r) => r.data);

export const logManualTrade = (trade: ManualTradeInput) =>
  api.post<Trade>("/trades/manual", trade).then((r) => r.data);

// Close an open trade, or part of it when a quantity is supplied
export interface CloseTradeInput {
  quantity?: number;
  fees?: number;
  slippage?: number;
  note?: string;
}

export const closeTrade = (tradeId: number, exitPrice: number, options: CloseTradeInput = {}) =>
  api
    .post<Trade>(`/trades/${tradeId}/close`, { exit_price: exitPrice, ...options })
    .then((r) => r.data);

// Performance attribution and automated trade journals
export const ATTRIBUTION_DIMENSIONS = [
  "strategy",
  "ticker",
  "asset_type",
  "sector",
  "timeframe",
  "regime",
] as const;

export type AttributionDimension = (typeof ATTRIBUTION_DIMENSIONS)[number];

export type AttributionFilters = Partial<Record<AttributionDimension, string>>;

export interface AttributionGroup {
  key: string;
  sample_size: number;
  gross_pnl: number;
  costs: number;
  net_pnl: number;
  wins: number;
  losses: number;
  win_rate: number;
  avg_net_pnl: number;
  avg_signal_confidence: number | null;
  sufficient_sample: boolean;
  sample_note: string;
  recommendation: string;
}

export interface AttributionSummary {
  sample_size: number;
  closed_sample_size: number;
  partially_realized_sample_size: number;
  gross_pnl: number;
  costs: number;
  net_pnl: number;
  wins: number;
  losses: number;
  win_rate: number;
  avg_net_pnl: number;
  sufficient_sample: boolean;
  sample_note: string;
}

export interface AttributionReconciliation {
  attributed_net_pnl: number;
  portfolio_total_pnl: number;
  delta: number;
  filtered_net_pnl: number;
  reconciles: boolean;
}

export interface ConfidenceBand {
  band: string;
  sample_size: number;
  observed_win_rate: number;
  avg_signal_confidence: number | null;
  net_pnl: number;
  sufficient_sample: boolean;
  sample_note: string;
  calibration_gap: number | null;
}

export interface TradeExecution {
  id: number;
  trade_id: number;
  kind: string;
  price: number;
  quantity: number;
  fees: number;
  slippage: number;
  entry_costs_allocated: number;
  gross_pnl: number | null;
  net_pnl: number | null;
  note: string | null;
  executed_at: string | null;
}

export interface AttributedTrade {
  id: number;
  ticker: string;
  direction: string;
  status: string;
  fully_closed: boolean;
  realized_quantity: number;
  remaining_quantity: number;
  strategy: string | null;
  strategy_version: string | null;
  asset_type: string | null;
  sector: string | null;
  timeframe: string | null;
  regime: string | null;
  signal_confidence: number | null;
  entry_price: number;
  planned_entry_price: number | null;
  average_exit_price: number | null;
  planned_exit_price: number | null;
  quantity: number;
  planned_quantity: number | null;
  gross_pnl: number | null;
  costs: number;
  net_pnl: number;
  net_pnl_pct: number | null;
  mfe_usd: number | null;
  mae_usd: number | null;
  mfe_pct: number | null;
  mae_pct: number | null;
  excursion_status: string;
  exit_count: number;
  executions: TradeExecution[];
  opened_at: string | null;
  closed_at: string | null;
}

export interface TradeJournalEntry {
  trade_id: number;
  ticker: string;
  strategy_name: string | null;
  outcome: string;
  net_pnl: number;
  summary: string;
  created_at: string | null;
  journal: Record<string, unknown> | null;
}

export interface AttributionResponse {
  min_sample_size: number;
  summary: AttributionSummary;
  reconciliation: AttributionReconciliation;
  dimensions: Record<AttributionDimension, AttributionGroup[]>;
  confidence_calibration: ConfidenceBand[];
  filters: AttributionFilters;
  filters_available: Record<AttributionDimension, string[]>;
  trades: AttributedTrade[];
  journals: TradeJournalEntry[];
}

export const fetchAttribution = (filters: AttributionFilters = {}) =>
  api.get<AttributionResponse>("/attribution", { params: filters }).then((r) => r.data);

export const fetchTradeJournal = (tradeId: number) =>
  api.get<TradeJournalEntry>(`/trades/${tradeId}/journal`).then((r) => r.data);

export const attributionExportUrl = (format: "json" | "csv", filters: AttributionFilters = {}) => {
  const params = new URLSearchParams({ format });
  Object.entries(filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  return `/api/attribution/export?${params.toString()}`;
};

// Deterministic paper orders — simulated fills only, no broker submission
export const PAPER_ORDER_STATUSES = [
  "pending",
  "submitted",
  "partially_filled",
  "filled",
  "canceled",
  "rejected",
  "expired",
] as const;

export type PaperOrderStatus = (typeof PAPER_ORDER_STATUSES)[number];

export const PAPER_ORDER_TYPES = [
  "market",
  "limit",
  "stop",
  "stop_limit",
  "bracket",
  "trailing_stop",
] as const;

export type PaperOrderType = (typeof PAPER_ORDER_TYPES)[number];

export interface PaperOrderFill {
  id: number;
  quantity: number;
  price: number;
  fees: number;
  slippage: number;
  notional: number;
  candle_timestamp: string | null;
  trade_id: number | null;
  created_at: string | null;
}

export interface PaperOrderEvent {
  id: number;
  event_type: string;
  from_status: string | null;
  to_status: string | null;
  message: string | null;
  detail: Record<string, unknown> | null;
  created_at: string | null;
}

export interface PaperOrder {
  id: number;
  idempotency_key: string;
  ticker: string;
  asset_type: string;
  side: string;
  order_type: PaperOrderType;
  role: string;
  status: PaperOrderStatus;
  quantity: number;
  filled_quantity: number;
  remaining_quantity: number;
  limit_price: number | null;
  stop_price: number | null;
  trail_percent: number | null;
  trail_amount: number | null;
  trail_reference_price: number | null;
  effective_stop_price: number | null;
  triggered: boolean;
  triggered_at: string | null;
  time_in_force: string;
  expires_at: string | null;
  reference_price: number | null;
  reserved_cash: number;
  reservation_price: number | null;
  average_fill_price: number | null;
  filled_notional: number;
  fees_total: number;
  slippage_total: number;
  costs_total: number;
  parent_id: number | null;
  oco_group: string | null;
  trade_id: number | null;
  last_candle_at: string | null;
  reject_reason: string | null;
  cancel_reason: string | null;
  created_at: string | null;
  updated_at: string | null;
  mode: string;
  children?: PaperOrder[];
  fills?: PaperOrderFill[];
  events?: PaperOrderEvent[];
}

export interface PaperMode {
  mode: string;
  live_trading_enabled: boolean;
  notice: string;
  spread_pct: number;
  slippage_pct: number;
  participation_pct: number;
  fee_pct: number;
  candle_interval: string;
}

export interface PaperOrderFilters {
  status?: string;
  ticker?: string;
  asset_type?: string;
  side?: string;
  order_type?: string;
}

export interface PaperOrderListResponse {
  mode: string;
  live_trading_enabled: boolean;
  notice: string;
  orders: PaperOrder[];
  filters_available: {
    status: string[];
    order_type: string[];
    side: string[];
    asset_type: string[];
  };
}

export interface PaperOrderInput {
  idempotency_key: string;
  ticker: string;
  side: string;
  order_type: PaperOrderType;
  quantity: number;
  asset_type?: string;
  limit_price?: number;
  stop_price?: number;
  trail_percent?: number;
  trail_amount?: number;
  reference_price?: number;
  take_profit_price?: number;
  stop_loss_price?: number;
  time_in_force?: string;
  expires_at?: string;
}

export interface PaperCandleInput {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface PaperProcessInput {
  ticker: string;
  candles?: PaperCandleInput[];
  interval?: string;
  order_id?: number;
}

export interface PaperProcessResponse {
  ticker: string;
  candle_source: string;
  processed_candles: number;
  orders: PaperOrder[];
  portfolio: { balance: number; equity: number; reserved_cash: number };
}

export interface PaperReconciliation {
  mode: string;
  live_trading_enabled: boolean;
  notice: string;
  orders: number;
  status_counts: Record<string, number>;
  fills: number;
  filled_quantity: number;
  order_filled_quantity: number;
  filled_notional: number;
  fees_total: number;
  slippage_total: number;
  reserved_cash: number;
  position_capital: number;
  balance: number;
  equity: number;
  expected_equity: number;
  fills_match_orders: boolean;
  equity_balanced: boolean;
}

export const fetchPaperMode = () =>
  api.get<PaperMode>("/paper-orders/mode").then((r) => r.data);

export const fetchPaperOrders = (filters: PaperOrderFilters = {}) =>
  api.get<PaperOrderListResponse>("/paper-orders", { params: filters }).then((r) => r.data);

export const fetchPaperOrder = (orderId: number) =>
  api.get<PaperOrder>(`/paper-orders/${orderId}`).then((r) => r.data);

export const createPaperOrder = (payload: PaperOrderInput) =>
  api.post<PaperOrder>("/paper-orders", payload).then((r) => r.data);

export const cancelPaperOrder = (orderId: number, reason?: string) =>
  api.post<PaperOrder>(`/paper-orders/${orderId}/cancel`, { reason }).then((r) => r.data);

export const processPaperOrders = (payload: PaperProcessInput) =>
  api.post<PaperProcessResponse>("/paper-orders/process", payload).then((r) => r.data);

export const fetchPaperReconciliation = () =>
  api.get<PaperReconciliation>("/paper-orders/reconcile").then((r) => r.data);

// Guarded live-broker execution. Separate endpoints, storage and UI from paper.
export interface LiveModeStatus {
  mode: string;
  armed: boolean;
  config_enabled: boolean;
  acknowledged: boolean;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  trading_disabled: boolean;
  disabled_reason: string | null;
  disabled_by: string | null;
  disabled_at: string | null;
  broker: string | null;
  broker_configured: boolean;
  broker_endpoint: string;
  sandbox: boolean;
  acknowledgement_phrase: string;
  max_order_notional: number;
  max_price_age_seconds: number;
  notice: string;
}

export interface LiveCheck {
  name: string;
  passed: boolean;
  blocking: boolean;
  detail: string;
}

export interface LiveOrderFill {
  id: number;
  broker_fill_id: string;
  quantity: number;
  price: number;
  filled_at: string | null;
  created_at: string | null;
}

export interface LiveAuditEntry {
  id: number;
  order_id: number | null;
  event_type: string;
  actor: string;
  message: string;
  previous_hash: string;
  entry_hash: string;
  created_at: string | null;
}

export interface LiveOrder {
  id: number;
  mode: string;
  idempotency_key: string;
  client_order_id: string;
  broker: string;
  broker_order_id: string | null;
  broker_endpoint: string;
  sandbox: boolean;
  ticker: string;
  asset_type: string;
  side: string;
  order_type: string;
  quantity: number;
  filled_quantity: number;
  limit_price: number | null;
  stop_price: number | null;
  time_in_force: string;
  reference_price: number | null;
  estimated_notional: number | null;
  average_fill_price: number | null;
  status: string;
  preflight: LiveCheck[];
  request_fingerprint: string;
  reject_reason: string | null;
  cancel_reason: string | null;
  broker_status_raw: string | null;
  submitted_at: string | null;
  reconciled_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  fills?: LiveOrderFill[];
  audit?: LiveAuditEntry[];
}

export interface LiveOrderInput {
  ticker: string;
  side: string;
  order_type: string;
  quantity: number;
  asset_type?: string;
  limit_price?: number;
  stop_price?: number;
  time_in_force?: string;
}

export interface LivePreview extends LiveModeStatus {
  request: Record<string, unknown>;
  approval_fingerprint: string;
  estimated_notional: number | null;
  reference_price: number | null;
  buying_power: number | null;
  checks: LiveCheck[];
  blockers: LiveCheck[];
  submittable: boolean;
}

export interface LiveOrderListResponse extends LiveModeStatus {
  orders: LiveOrder[];
}

export interface LiveCancelAllResponse extends LiveModeStatus {
  requested: number;
  canceled: number;
  failed: number;
  results: { order_id: number; canceled: boolean; error: string | null }[];
}

export const fetchLiveStatus = () =>
  api.get<LiveModeStatus>("/live-trading/status").then((r) => r.data);

export const acknowledgeLiveTrading = (phrase: string, note?: string) =>
  api.post<LiveModeStatus>("/live-trading/acknowledge", { phrase, note }).then((r) => r.data);

export const revokeLiveTrading = () =>
  api.post<LiveModeStatus>("/live-trading/revoke", {}).then((r) => r.data);

export const disableLiveTrading = (reason?: string) =>
  api.post<LiveModeStatus>("/live-trading/disable", { reason }).then((r) => r.data);

export const enableLiveTrading = () =>
  api.post<LiveModeStatus>("/live-trading/enable", {}).then((r) => r.data);

export const previewLiveOrder = (payload: LiveOrderInput) =>
  api.post<LivePreview>("/live-orders/preview", payload).then((r) => r.data);

export const submitLiveOrder = (
  payload: LiveOrderInput & { idempotency_key: string; approval_fingerprint: string },
) => api.post<LiveOrder>("/live-orders", payload).then((r) => r.data);

export const fetchLiveOrders = () =>
  api.get<LiveOrderListResponse>("/live-orders").then((r) => r.data);

export const fetchLiveOrder = (orderId: number) =>
  api.get<LiveOrder>(`/live-orders/${orderId}`).then((r) => r.data);

export const cancelLiveOrder = (orderId: number, reason?: string) =>
  api.post<LiveOrder>(`/live-orders/${orderId}/cancel`, { reason }).then((r) => r.data);

export const cancelAllLiveOrders = (reason?: string) =>
  api.post<LiveCancelAllResponse>("/live-orders/cancel-all", { reason }).then((r) => r.data);

export const reconcileLiveOrders = () =>
  api.post<{ checked: number; out_of_sync: number; errors: number }>(
    "/live-orders/reconcile",
    {},
  ).then((r) => r.data);

export const verifyLiveAudit = () =>
  api.get<{ entries: number; intact: boolean; broken_entry_id: number | null }>(
    "/live-orders/audit/verify",
  ).then((r) => r.data);

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
  data_quality: {
    total: number;
    healthy: number;
    warnings: number;
    blocked: number;
  };
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
export interface OpportunityComponent {
  name: string;
  label: string;
  score: number;
  weight_pct: number;
  contribution: number;
  available: boolean;
  explanation: string;
}

export interface OpportunityTradePlan {
  entry_zone: { low: number; high: number };
  stop_loss: number;
  targets: Array<{ price: number; exit_pct: number; label: string }>;
  position_size_usd: number;
  quantity: number;
  maximum_planned_loss_usd: number;
  estimated_cost_bps: number;
  estimated_costs_usd: number;
  net_reward_risk: number;
  scale_in: Array<{ entry_pct: number; instruction: string }>;
  scale_out: Array<{ exit_pct: number; instruction: string }>;
  time_stop: string;
  invalidation_reason: string;
}

export interface Opportunity {
  id: string;
  ticker: string;
  asset_type: string;
  direction: string;
  status: string;
  score: number;
  minimum_score: number;
  eligible: boolean;
  eligibility_reasons: string[];
  missing_inputs: string[];
  components: OpportunityComponent[];
  regime: MarketRegime | null;
  timeframe_agreement: TimeframeAgreement | null;
  regime_controls: RegimeControls | null;
  trade_plan: OpportunityTradePlan | null;
  event_warnings: string[];
  signal_reason: string;
  evaluated_at: string;
  user_decision: "pending" | "approved" | "rejected" | "snoozed" | "edited" | "blocked";
  snoozed_until: string | null;
}

export interface ScanSignal {
  ticker: string;
  direction: string;
  status: string;
  approved: boolean;
  suppressed: boolean;
  action: string;
  reason: string;
  recommended_size_usd: number;
  score: number;
  eligible: boolean;
  opportunity: Opportunity;
}

export interface ScanResult {
  scanned: number;
  signals_found: number;
  notifications_sent: number;
  errors: number;
  quality_rejected: number;
  quality_rejections: Array<{ ticker: string; reason: string }>;
  signals: ScanSignal[];
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

export interface OpportunityActionPayload {
  action: "approve" | "reject" | "snooze" | "edit";
  snooze_minutes?: number;
  edit?: {
    entry_zone_low?: number;
    entry_zone_high?: number;
    stop_loss?: number;
    targets?: number[];
    quantity?: number;
    time_stop?: string;
  };
}

export const updateOpportunityAction = (opportunityId: string, payload: OpportunityActionPayload) =>
  api.post<Opportunity>(`/opportunities/${encodeURIComponent(opportunityId)}/action`, payload).then((r) => r.data);

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
  risk: PortfolioRisk;
  cash?: {
    available: boolean;
    balance: number;
    reserved: number;
    free: number;
    peak_equity: number;
  };
  regime?: {
    available: boolean;
    reason?: string | null;
    label?: string;
    trend?: string;
    volatility?: string;
    breadth?: string;
    risk?: string;
    scanned_at?: string | null;
  };
  top_opportunities?: {
    available: boolean;
    reason?: string | null;
    items: Array<{
      id: string;
      ticker: string;
      direction: string;
      score: number;
      user_decision: string;
    }>;
  };
  provider_health?: {
    available: boolean;
    reason?: string | null;
    connectivity?: Record<string, { online: boolean; last_checked: string | null; last_online: string | null; last_offline: string | null }>;
    data_quality?: { total: number; healthy: number; warnings: number; blocked: number };
    current_time?: string;
  };
  action_counts?: ActionItemCounts;
}

export const fetchDashboardSummary = () =>
  api.get<DashboardSummary>("/dashboard-summary").then((r) => r.data);

// Issue #50: Action Required inbox
export type ActionItemStatus = "open" | "acknowledged" | "snoozed" | "resolved";
export type ActionItemSeverity = "critical" | "warning" | "info";
export type ActionItemCategory =
  | "opportunity"
  | "risk"
  | "data"
  | "event"
  | "execution"
  | "operations";

export interface ActionItemDeepLink {
  tab: string;
  ticker?: string;
  opportunity_id?: string;
  trade_id?: number;
  order_id?: number;
  section?: string;
}

export interface ActionItem {
  id: number;
  source_key: string;
  source_type: string;
  category: ActionItemCategory;
  severity: ActionItemSeverity;
  is_mandatory: boolean;
  title: string;
  message: string;
  ticker: string | null;
  trade_id: number | null;
  order_id: number | null;
  context_id: string | null;
  deep_link: ActionItemDeepLink;
  payload: Record<string, unknown>;
  payload_hash: string;
  status: ActionItemStatus;
  source_active: boolean;
  snoozed_until: string | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  updated_at: string | null;
  acknowledged_at: string | null;
  snoozed_at: string | null;
  resolved_at: string | null;
}

export interface ActionItemCounts {
  total: number;
  unresolved: number;
  open: number;
  mandatory: number;
  by_status: Record<ActionItemStatus, number>;
  by_severity: Record<ActionItemSeverity, number>;
  by_category: Record<ActionItemCategory, number>;
}

export interface ActionItemListResponse {
  user_key: string;
  items: ActionItem[];
  counts: ActionItemCounts;
  mandatory_note: string;
  refreshed?: { created: number; updated: number; cleared: number };
}

export interface ActionItemFilters {
  status?: string;
  category?: string;
  severity?: string;
  source_type?: string;
}

export const fetchActionItems = (filters: ActionItemFilters = {}) =>
  api.get<ActionItemListResponse>("/action-items", { params: filters }).then((r) => r.data);

export const refreshActionItems = (filters: ActionItemFilters = {}) =>
  api.post<ActionItemListResponse>("/action-items/refresh", null, { params: filters }).then((r) => r.data);

export const acknowledgeActionItem = (id: number) =>
  api.post<ActionItem>(`/action-items/${id}/acknowledge`).then((r) => r.data);

export const snoozeActionItem = (id: number, minutes: number) =>
  api.post<ActionItem>(`/action-items/${id}/snooze`, { minutes }).then((r) => r.data);

export const resolveActionItem = (id: number) =>
  api.post<ActionItem>(`/action-items/${id}/resolve`).then((r) => r.data);

export const reopenActionItem = (id: number) =>
  api.post<ActionItem>(`/action-items/${id}/reopen`).then((r) => r.data);

// Issue #50: per-user dashboard layout preferences
export type DashboardMode = "compact" | "detailed";

export interface DashboardWidgetPreference {
  id: string;
  enabled: boolean;
}

export interface DashboardLayout {
  widgets: DashboardWidgetPreference[];
  mode: DashboardMode;
}

export interface DashboardPreferences extends DashboardLayout {
  user_key: string;
  layouts: Record<string, DashboardLayout>;
  available_widgets: string[];
  updated_at: string | null;
}

export const fetchDashboardPreferences = () =>
  api.get<DashboardPreferences>("/dashboard-preferences").then((r) => r.data);

export const saveDashboardPreferences = (layout: DashboardLayout) =>
  api.put<DashboardPreferences>("/dashboard-preferences", layout).then((r) => r.data);

export const resetDashboardPreferences = () =>
  api.post<DashboardPreferences>("/dashboard-preferences/reset").then((r) => r.data);

export const saveDashboardLayout = (name: string, layout: DashboardLayout) =>
  api
    .put<DashboardPreferences>(`/dashboard-preferences/layouts/${encodeURIComponent(name)}`, layout)
    .then((r) => r.data);

export const deleteDashboardLayout = (name: string) =>
  api
    .delete<DashboardPreferences>(`/dashboard-preferences/layouts/${encodeURIComponent(name)}`)
    .then((r) => r.data);

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

// Phase 5: Walk-forward backtesting
export interface BacktestMetrics {
  total_trades: number;
  wins: number;
  losses: number;
  win_rate_pct: number;
  expectancy_pct: number;
  sharpe: number;
  sortino: number;
  profit_factor: number;
  max_drawdown_pct: number;
  recovery_bars: number;
  turnover_pct: number;
  exposure_pct: number;
  gross_return_pct: number;
  after_cost_return_pct: number;
  total_cost_pct: number;
  final_equity: number;
}

export interface BacktestResult {
  run_id: string;
  created_at: string;
  ticker: string;
  window_count: number;
  strategy: {
    version: string;
    parameter_grid: Array<Record<string, number>>;
  };
  configuration: {
    initial_capital: number;
    costs: Record<string, number>;
    windows: Record<string, number>;
    thresholds: Record<string, number>;
    candles: number;
  };
  aggregate: {
    in_sample: BacktestMetrics;
    validation: BacktestMetrics;
    out_of_sample: BacktestMetrics;
  };
  benchmarks: Record<string, {
    symbol: string;
    windows: number;
    gross_return_pct: number;
    after_cost_return_pct: number;
    max_drawdown_pct: number;
    final_equity: number;
  }>;
  regimes: Record<string, {
    trades: number;
    win_rate_pct: number;
    expectancy_pct: number;
    after_cost_return_pct: number;
  }>;
  parameter_sensitivity: Array<{
    parameter_index: number;
    parameters: Record<string, number>;
    validation_windows: number;
    mean_validation_return_pct: number;
    return_std_dev_pct: number;
    positive_window_pct: number;
    selected_windows: number;
  }>;
  alert_eligibility: {
    eligible: boolean;
    reasons: string[];
    evaluated_on: "out_of_sample";
  };
  trades: Array<{
    direction: string;
    entry: number;
    exit: number;
    entry_date: string;
    exit_date: string;
    gross_pnl_pct: number;
    net_pnl_pct: number;
    cost_pct: number;
    outcome: string;
    reason: string;
    market_regime: string;
    volatility_regime: string;
    breadth_regime: string;
    risk_regime: string;
    regime_label: string;
  }>;
  equity_curve: Array<{ date: string; equity: number }>;
}

export const runBacktest = (
  ticker: string,
  period: string = "max",
  capital: number = 10000,
  assetType: string = "stock",
) =>
  api.post<BacktestResult>("/backtest", {
    ticker,
    asset_type: assetType,
    period,
    available_capital: capital,
    benchmark_tickers: assetType === "crypto"
      ? [ticker.trim().toUpperCase() === "BTC" ? "ETH" : "BTC"]
      : ["SPY"],
  }).then((r) => r.data);

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
