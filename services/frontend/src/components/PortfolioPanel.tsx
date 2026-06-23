import { useEffect, useState } from "react";
import {
  fetchPortfolio,
  fetchTrades,
  updateBalance,
  logManualTrade,
  closeTrade,
  fetchReplyTrades,
  type Portfolio,
  type Trade,
  type ReplyTradesResponse,
} from "@/lib/api";

export default function PortfolioPanel() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [replyTrades, setReplyTrades] = useState<ReplyTradesResponse | null>(null);

  // Balance editing
  const [editingBalance, setEditingBalance] = useState(false);
  const [newBalance, setNewBalance] = useState("");
  const [balanceMsg, setBalanceMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Manual trade form
  const [showTradeForm, setShowTradeForm] = useState(false);
  const [tradeForm, setTradeForm] = useState({ ticker: "", direction: "BUY", entry_price: "", quantity: "" });
  const [tradeFormMsg, setTradeFormMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [tradeSubmitting, setTradeSubmitting] = useState(false);

  // Close trade
  const [closingTradeId, setClosingTradeId] = useState<number | null>(null);
  const [exitPrice, setExitPrice] = useState("");

  // Tab
  const [activeTab, setActiveTab] = useState<"open" | "closed" | "telegram">("open");

  const loadAll = () => {
    fetchPortfolio().then(setPortfolio).catch(() => {});
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
        ticker: tradeForm.ticker.toUpperCase(),
        direction: tradeForm.direction,
        entry_price: parseFloat(tradeForm.entry_price),
        quantity: parseFloat(tradeForm.quantity),
      });
      setTradeFormMsg({ type: "success", text: `${tradeForm.direction} logged for ${tradeForm.ticker.toUpperCase()}` });
      setTradeForm({ ticker: "", direction: "BUY", entry_price: "", quantity: "" });
      loadAll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to log trade";
      setTradeFormMsg({ type: "error", text: msg });
    } finally {
      setTradeSubmitting(false);
    }
  };

  const handleCloseTrade = async (tradeId: number) => {
    const price = parseFloat(exitPrice);
    if (isNaN(price) || price <= 0) return;
    try {
      await closeTrade(tradeId, price);
      setClosingTradeId(null);
      setExitPrice("");
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
                    {t.quantity} @ ${t.entry_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </span>
                  <span className="text-xs text-[var(--muted-foreground)]">
                    = ${(t.quantity * t.entry_price).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </span>
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
                    <button onClick={() => handleCloseTrade(t.id)} className="rounded bg-red-600 text-white px-2 py-1 text-xs">Close</button>
                    <button onClick={() => { setClosingTradeId(null); setExitPrice(""); }} className="text-xs text-[var(--muted-foreground)]">X</button>
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
