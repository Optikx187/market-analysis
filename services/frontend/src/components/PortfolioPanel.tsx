import { useEffect, useState } from "react";
import {
  fetchPortfolio,
  fetchPortfolioRisk,
  fetchTrades,
  updateBalance,
  logManualTrade,
  closeTrade,
  fetchReplyTrades,
  type Portfolio,
  type PortfolioRisk,
  type Trade,
  type ReplyTradesResponse,
} from "@/lib/api";

export default function PortfolioPanel() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [risk, setRisk] = useState<PortfolioRisk | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [replyTrades, setReplyTrades] = useState<ReplyTradesResponse | null>(null);

  // Balance editing
  const [editingBalance, setEditingBalance] = useState(false);
  const [newBalance, setNewBalance] = useState("");
  const [balanceMsg, setBalanceMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Manual trade form
  const [showTradeForm, setShowTradeForm] = useState(false);
  const [tradeForm, setTradeForm] = useState({
    ticker: "",
    direction: "BUY",
    entry_price: "",
    quantity: "",
    stop_loss: "",
    target_price: "",
    asset_type: "stock",
    sector: "Unclassified",
  });
  const [tradeFormMsg, setTradeFormMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [tradeSubmitting, setTradeSubmitting] = useState(false);

  // Close trade
  const [closingTradeId, setClosingTradeId] = useState<number | null>(null);
  const [exitPrice, setExitPrice] = useState("");
  const [exitQuantity, setExitQuantity] = useState("");
  const [exitFees, setExitFees] = useState("");

  // Tab
  const [activeTab, setActiveTab] = useState<"open" | "closed" | "telegram">("open");

  const loadAll = () => {
    fetchPortfolio().then(setPortfolio).catch(() => {});
    fetchPortfolioRisk().then(setRisk).catch(() => {});
    fetchTrades().then(setTrades).catch(() => {});
    fetchReplyTrades().then(setReplyTrades).catch(() => {});
  };

  useEffect(() => {
    loadAll();
    const interval = setInterval(loadAll, 30000);
    return () => clearInterval(interval);
  }, []);

  const openTrades = trades.filter((t) => t.status === "OPEN");
  const closedTrades = trades.filter((t) => t.status === "CLOSED");

  const handleUpdateBalance = async () => {
    const val = parseFloat(newBalance);
    if (isNaN(val) || val < 0) return;
    try {
      const res = await updateBalance(val);
      setBalanceMsg({ type: "success", text: res.message });
      setEditingBalance(false);
      fetchPortfolio().then(setPortfolio);
    } catch {
      setBalanceMsg({ type: "error", text: "Failed to update balance." });
    }
  };

  const handleLogTrade = async () => {
    setTradeFormMsg(null);
    setTradeSubmitting(true);
    try {
      await logManualTrade({
        ticker: tradeForm.ticker.trim().toUpperCase(),
        direction: tradeForm.direction,
        entry_price: parseFloat(tradeForm.entry_price),
        quantity: parseFloat(tradeForm.quantity),
        stop_loss: tradeForm.stop_loss ? parseFloat(tradeForm.stop_loss) : undefined,
        target_price: tradeForm.target_price ? parseFloat(tradeForm.target_price) : undefined,
        asset_type: tradeForm.asset_type,
        sector: tradeForm.sector.trim() || "Unclassified",
      });
      setTradeFormMsg({ type: "success", text: `${tradeForm.direction} logged for ${tradeForm.ticker.trim().toUpperCase()}` });
      setTradeForm({
        ticker: "",
        direction: "BUY",
        entry_price: "",
        quantity: "",
        stop_loss: "",
        target_price: "",
        asset_type: "stock",
        sector: "Unclassified",
      });
      loadAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to log trade";
      setTradeFormMsg({ type: "error", text: msg });
    } finally {
      setTradeSubmitting(false);
    }
  };

  const resetCloseForm = () => {
    setClosingTradeId(null);
    setExitPrice("");
    setExitQuantity("");
    setExitFees("");
  };

  const handleCloseTrade = async (tradeId: number) => {
    const price = parseFloat(exitPrice);
    if (isNaN(price) || price <= 0) return;
    const quantity = parseFloat(exitQuantity);
    const fees = parseFloat(exitFees);
    try {
      await closeTrade(tradeId, price, {
        quantity: isNaN(quantity) || quantity <= 0 ? undefined : quantity,
        fees: isNaN(fees) || fees < 0 ? undefined : fees,
      });
      resetCloseForm();
      loadAll();
    } catch {
      // error handled visually
    }
  };

  if (!portfolio) return <div className="rounded-lg border bg-[var(--card)] p-4">Loading portfolio...</div>;

  const totalPnlColor = portfolio.total_pnl >= 0 ? "text-green-400" : "text-red-400";

  return (
    <div className="rounded-lg border bg-[var(--card)] p-4 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Portfolio</h2>
        <button
          onClick={() => setShowTradeForm(!showTradeForm)}
          className="rounded bg-blue-600 text-white px-3 py-1.5 text-xs hover:bg-blue-700"
        >
          {showTradeForm ? "Cancel" : "Log Trade"}
        </button>
      </div>

      {/* Balance Section */}
      <div className="p-3 rounded border border-[var(--border)] bg-[var(--background)]">
        <div className="flex items-center justify-between">
          <div>
            <span className="text-xs text-[var(--muted-foreground)]">Available Balance</span>
            <div className="text-xl font-semibold">${portfolio.balance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
          </div>
          {!editingBalance ? (
            <button
              onClick={() => { setEditingBalance(true); setNewBalance(portfolio.balance.toString()); setBalanceMsg(null); }}
              className="rounded bg-[var(--secondary)] px-3 py-1.5 text-xs hover:bg-[var(--accent)]"
            >
              Update
            </button>
          ) : (
            <div className="flex items-center gap-2">
              <span className="text-xs">$</span>
              <input
                type="number"
                value={newBalance}
                onChange={(e) => setNewBalance(e.target.value)}
                className="w-28 rounded border bg-[var(--input)] px-2 py-1 text-sm"
                min="0"
                step="100"
              />
              <button onClick={handleUpdateBalance} className="rounded bg-green-600 text-white px-2 py-1 text-xs">Save</button>
              <button onClick={() => setEditingBalance(false)} className="text-xs text-[var(--muted-foreground)]">Cancel</button>
            </div>
          )}
        </div>
        {balanceMsg && (
          <div className={`text-xs mt-1 ${balanceMsg.type === "success" ? "text-green-400" : "text-red-400"}`}>{balanceMsg.text}</div>
        )}
        <p className="text-xs text-[var(--muted-foreground)] mt-1">
          Your actual available trading capital. Trade recommendations are sized based on this.
        </p>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Total P&L" value={`$${portfolio.total_pnl.toLocaleString(undefined, { minimumFractionDigits: 2 })}`} className={totalPnlColor} />
        <StatCard label="Win Rate" value={portfolio.win_count + portfolio.loss_count > 0 ? `${portfolio.win_rate}%` : "—"} />
        <StatCard label="Wins / Losses" value={`${portfolio.win_count} / ${portfolio.loss_count}`} />
        <StatCard label="Open Positions" value={`${openTrades.length}`} />
      </div>

      {risk && (
        <div className={`p-3 rounded border space-y-3 ${risk.breaker.active ? "border-red-600/70 bg-red-600/5" : "border-[var(--border)] bg-[var(--background)]"}`}>
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-medium">Portfolio Risk Controls</h3>
              <p className="text-xs text-[var(--muted-foreground)]">Risk-to-stop, concentration, correlation, and loss breakers.</p>
            </div>
            <span className={`rounded px-2 py-1 text-xs font-medium ${risk.breaker.active ? "bg-red-600/20 text-red-400" : "bg-green-600/20 text-green-400"}`}>
              {risk.breaker.active ? "New Risk Blocked" : "Risk Available"}
            </span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <RiskMetric
              label="Effective Heat"
              value={`${risk.heat.effective_pct}% / ${risk.heat.limit_pct}%`}
              detail={`${risk.heat.utilization_pct}% utilized`}
              warning={risk.heat.utilization_pct >= 80}
            />
            <RiskMetric
              label="Correlation Penalty"
              value={`$${risk.heat.correlation_penalty_usd.toLocaleString()}`}
              detail={risk.correlation.data_available ? `${risk.correlation.largest_cluster_pct}% largest cluster` : "Awaiting aligned history"}
            />
            <RiskMetric
              label="Largest Concentration"
              value={`${risk.exposure.largest_concentration.name} ${risk.exposure.largest_concentration.pct}%`}
              detail={risk.exposure.largest_concentration.category.replace("_", " ")}
            />
            <RiskMetric
              label="Current Drawdown"
              value={`${risk.breaker.current_drawdown_pct}% / ${risk.breaker.drawdown_limit_pct}%`}
              detail={`Daily ${risk.breaker.daily_loss_pct}% · Weekly ${risk.breaker.weekly_loss_pct}%`}
              warning={risk.breaker.active}
            />
          </div>
          {risk.breaker.reasons.length > 0 && (
            <div className="rounded border border-red-600/40 bg-red-600/10 p-2 text-xs text-red-300">
              {risk.breaker.reasons.join(" · ")} Position reductions and closures remain allowed.
            </div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3 text-xs">
            <ExposureList title="Ticker Exposure" values={risk.exposure.ticker} limit={risk.exposure.limits.ticker_pct} />
            <ExposureList title="Sector Exposure" values={risk.exposure.sector} limit={risk.exposure.limits.sector_pct} empty="Classify trades to track sectors" />
            <ExposureList title="Asset-Class Exposure" values={risk.exposure.asset_class} limit={risk.exposure.limits.asset_class_pct} />
            <ExposureList title="Directional Exposure" values={risk.exposure.direction} limit={risk.exposure.limits.direction_pct} />
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
            {risk.stress_tests.map((scenario) => (
              <div key={scenario.name} className="rounded border border-[var(--border)] p-2 text-xs">
                <div className="text-[var(--muted-foreground)]">{scenario.name}</div>
                <div className={`font-medium ${scenario.estimated_pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                  {scenario.estimated_pnl >= 0 ? "+" : ""}${scenario.estimated_pnl.toLocaleString()}
                </div>
                <div className="text-[10px] text-[var(--muted-foreground)]">{scenario.description}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Manual Trade Form */}
      {showTradeForm && (
        <div className="p-3 rounded border border-blue-500/30 bg-blue-500/5 space-y-2">
          <h3 className="text-sm font-medium">Log a Trade</h3>
          <p className="text-xs text-[var(--muted-foreground)]">Record a buy or sell you executed outside the system.</p>
          <div className="grid grid-cols-2 gap-2">
            <input
              placeholder="Ticker (e.g. AAPL)"
              value={tradeForm.ticker}
              onChange={(e) => setTradeForm({ ...tradeForm, ticker: e.target.value })}
              className="rounded border bg-[var(--input)] px-2 py-1.5 text-sm"
            />
            <select
              value={tradeForm.direction}
              onChange={(e) => setTradeForm({ ...tradeForm, direction: e.target.value })}
              className="rounded border bg-[var(--input)] px-2 py-1.5 text-sm"
            >
              <option value="BUY">BUY</option>
              <option value="SELL">SELL (Short)</option>
            </select>
            <select
              value={tradeForm.asset_type}
              onChange={(e) => setTradeForm({ ...tradeForm, asset_type: e.target.value })}
              className="rounded border bg-[var(--input)] px-2 py-1.5 text-sm"
            >
              <option value="stock">Stock / ETF</option>
              <option value="crypto">Crypto</option>
            </select>
            <input
              placeholder="Sector (e.g. Technology)"
              value={tradeForm.sector}
              onChange={(e) => setTradeForm({ ...tradeForm, sector: e.target.value })}
              className="rounded border bg-[var(--input)] px-2 py-1.5 text-sm"
            />
            <input
              placeholder="Entry Price"
              type="number"
              value={tradeForm.entry_price}
              onChange={(e) => setTradeForm({ ...tradeForm, entry_price: e.target.value })}
              className="rounded border bg-[var(--input)] px-2 py-1.5 text-sm"
              min="0"
              step="0.01"
            />
            <input
              placeholder="Quantity"
              type="number"
              value={tradeForm.quantity}
              onChange={(e) => setTradeForm({ ...tradeForm, quantity: e.target.value })}
              className="rounded border bg-[var(--input)] px-2 py-1.5 text-sm"
              min="0"
              step="0.01"
            />
            <input
              placeholder={tradeForm.direction === "BUY" ? "Stop Loss (below entry)" : "Stop Loss (above entry)"}
              type="number"
              value={tradeForm.stop_loss}
              onChange={(e) => setTradeForm({ ...tradeForm, stop_loss: e.target.value })}
              className="rounded border bg-[var(--input)] px-2 py-1.5 text-sm"
              min="0"
              step="0.01"
            />
            <input
              placeholder="Target Price"
              type="number"
              value={tradeForm.target_price}
              onChange={(e) => setTradeForm({ ...tradeForm, target_price: e.target.value })}
              className="rounded border bg-[var(--input)] px-2 py-1.5 text-sm"
              min="0"
              step="0.01"
            />
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleLogTrade}
              disabled={tradeSubmitting || !tradeForm.ticker || !tradeForm.entry_price || !tradeForm.quantity}
              className="rounded bg-green-600 text-white px-3 py-1.5 text-xs hover:bg-green-700 disabled:opacity-50"
            >
              {tradeSubmitting ? "Logging..." : "Log Trade"}
            </button>
            {tradeFormMsg && (
              <span className={`text-xs ${tradeFormMsg.type === "success" ? "text-green-400" : "text-red-400"}`}>{tradeFormMsg.text}</span>
            )}
          </div>
        </div>
      )}

      {/* Trade Tabs */}
      <div className="border-b border-[var(--border)] flex gap-4">
        <TabButton active={activeTab === "open"} onClick={() => setActiveTab("open")}>
          Open ({openTrades.length})
        </TabButton>
        <TabButton active={activeTab === "closed"} onClick={() => setActiveTab("closed")}>
          Closed ({closedTrades.length})
        </TabButton>
        <TabButton active={activeTab === "telegram"} onClick={() => setActiveTab("telegram")}>
          Telegram {replyTrades?.bot_active ? <span className="ml-1 text-green-400 text-[10px]">&#9679;</span> : null}
        </TabButton>
      </div>

      {/* Open Positions Tab */}
      {activeTab === "open" && (
        <div className="space-y-1">
          {openTrades.length === 0 ? (
            <p className="text-xs text-[var(--muted-foreground)] py-2">No open positions. Log a trade to start tracking.</p>
          ) : (
            openTrades.map((t) => (
              <div key={t.id} className="rounded border px-3 py-2 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className="font-medium text-sm">{t.ticker}</span>
                  <span className={`text-xs ${t.direction === "BUY" ? "text-green-400" : "text-red-400"}`}>{t.direction}</span>
                  <span className="text-xs text-[var(--muted-foreground)]">
                    {t.remaining_quantity < t.quantity
                      ? `${t.remaining_quantity} of ${t.quantity} left`
                      : t.quantity}{" "}
                    @ ${t.entry_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </span>
                  <span className="text-xs text-[var(--muted-foreground)]">
                    = ${(t.quantity * t.entry_price).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </span>
                  <span className="rounded bg-[var(--muted)] px-1.5 py-0.5 text-[10px] text-[var(--muted-foreground)]">
                    {t.asset_type} · {t.sector}
                  </span>
                  <PositionRisk trade={t} />
                </div>
                {closingTradeId === t.id ? (
                  <div className="flex items-center gap-1">
                    <input
                      type="number"
                      placeholder="Exit price"
                      value={exitPrice}
                      onChange={(e) => setExitPrice(e.target.value)}
                      className="w-24 rounded border bg-[var(--input)] px-2 py-1 text-xs"
                      min="0"
                      step="0.01"
                    />
                    <input
                      type="number"
                      placeholder={`Qty (max ${t.remaining_quantity})`}
                      value={exitQuantity}
                      onChange={(e) => setExitQuantity(e.target.value)}
                      className="w-28 rounded border bg-[var(--input)] px-2 py-1 text-xs"
                      min="0"
                      max={t.remaining_quantity}
                      step="0.01"
                      title="Leave empty to close the whole remaining position"
                    />
                    <input
                      type="number"
                      placeholder="Fees"
                      value={exitFees}
                      onChange={(e) => setExitFees(e.target.value)}
                      className="w-20 rounded border bg-[var(--input)] px-2 py-1 text-xs"
                      min="0"
                      step="0.01"
                    />
                    <button onClick={() => handleCloseTrade(t.id)} className="rounded bg-red-600 text-white px-2 py-1 text-xs">Close</button>
                    <button onClick={resetCloseForm} className="text-xs text-[var(--muted-foreground)]">X</button>
                  </div>
                ) : (
                  <button
                    onClick={() => setClosingTradeId(t.id)}
                    className="rounded bg-[var(--secondary)] px-2 py-1 text-xs hover:bg-red-600/20"
                  >
                    Close Position
                  </button>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* Closed Trades Tab */}
      {activeTab === "closed" && (
        <div className="space-y-1 max-h-[300px] overflow-y-auto">
          {closedTrades.length === 0 ? (
            <p className="text-xs text-[var(--muted-foreground)] py-2">No closed trades yet.</p>
          ) : (
            closedTrades.map((t) => (
              <div key={t.id} className="rounded border px-3 py-2 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className="font-medium text-sm">{t.ticker}</span>
                  <span className={`text-xs ${t.direction === "BUY" ? "text-green-400" : "text-red-400"}`}>{t.direction}</span>
                  <span className="text-xs text-[var(--muted-foreground)]">
                    {t.quantity} @ ${t.entry_price.toLocaleString(undefined, { minimumFractionDigits: 2 })} → ${t.exit_price?.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </span>
                </div>
                <div className="text-right">
                  <span className={`text-sm font-medium ${(t.pnl ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                    {(t.pnl ?? 0) >= 0 ? "+" : ""}${(t.pnl ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </span>
                  {t.pnl_pct !== null && (
                    <span className={`text-xs ml-1 ${t.pnl_pct >= 0 ? "text-green-400" : "text-red-400"}`}>
                      ({t.pnl_pct >= 0 ? "+" : ""}{t.pnl_pct}%)
                    </span>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* Telegram Trades Tab */}
      {activeTab === "telegram" && (
        <div>
          <p className="text-xs text-[var(--muted-foreground)] mb-2">
            Trades logged via Telegram bot (<code>/bought</code>, <code>/sold</code>).
            Bot: {replyTrades?.bot_active ? <span className="text-green-400">Active</span> : <span>Inactive</span>}
          </p>
          {replyTrades && replyTrades.trades.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-[var(--muted-foreground)]">
                    <th className="text-left py-1">Time</th>
                    <th className="text-left py-1">Ticker</th>
                    <th className="text-left py-1">Dir</th>
                    <th className="text-right py-1">Price</th>
                    <th className="text-right py-1">Qty</th>
                  </tr>
                </thead>
                <tbody>
                  {replyTrades.trades.slice(-15).reverse().map((t, i) => (
                    <tr key={i} className="border-t border-[var(--border)]">
                      <td className="py-1">{new Date(t.timestamp).toLocaleTimeString()}</td>
                      <td className="py-1 font-medium">{t.ticker}</td>
                      <td className={`py-1 ${t.direction === "BUY" ? "text-green-400" : "text-red-400"}`}>{t.direction}</td>
                      <td className="py-1 text-right">${t.entry_price.toLocaleString()}</td>
                      <td className="py-1 text-right">{t.quantity}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-xs text-[var(--muted-foreground)]">
              No trades via Telegram yet. Use <code>/bought AAPL 150 10</code> to log a trade.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, className = "" }: { label: string; value: string; className?: string }) {
  return (
    <div className="p-2 rounded border border-[var(--border)] bg-[var(--background)]">
      <div className="text-xs text-[var(--muted-foreground)]">{label}</div>
      <div className={`text-lg font-semibold ${className}`}>{value}</div>
    </div>
  );
}

function RiskMetric({
  label,
  value,
  detail,
  warning = false,
}: {
  label: string;
  value: string;
  detail: string;
  warning?: boolean;
}) {
  return (
    <div className="rounded border border-[var(--border)] p-2">
      <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">{label}</div>
      <div className={`text-sm font-semibold ${warning ? "text-red-400" : ""}`}>{value}</div>
      <div className="text-[10px] text-[var(--muted-foreground)]">{detail}</div>
    </div>
  );
}

function ExposureList({
  title,
  values,
  limit,
  empty = "No open exposure",
}: {
  title: string;
  values: Record<string, number>;
  limit: number;
  empty?: string;
}) {
  const rows = Object.entries(values).sort((first, second) => second[1] - first[1]).slice(0, 3);
  return (
    <div>
      <div className="mb-1 font-medium">{title} <span className="text-[var(--muted-foreground)]">({limit}% max)</span></div>
      {rows.length === 0 ? (
        <div className="text-[var(--muted-foreground)]">{empty}</div>
      ) : rows.map(([name, pct]) => (
        <div key={name} className="flex justify-between gap-2">
          <span className="truncate text-[var(--muted-foreground)]">{name}</span>
          <span className={pct > limit ? "text-red-400" : ""}>{pct}%</span>
        </div>
      ))}
    </div>
  );
}

function PositionRisk({ trade }: { trade: Trade }) {
  const stopDistance = trade.direction === "BUY"
    ? trade.entry_price - trade.stop_loss
    : trade.stop_loss - trade.entry_price;
  const risk = Math.max(0, stopDistance * trade.quantity);
  return (
    <span className="text-xs text-orange-400" title={`Stop ${trade.stop_loss.toLocaleString()}`}>
      ${risk.toLocaleString(undefined, { maximumFractionDigits: 2 })} risk-to-stop
    </span>
  );
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`pb-2 text-sm ${active ? "border-b-2 border-blue-500 text-[var(--foreground)]" : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"}`}
    >
      {children}
    </button>
  );
}
