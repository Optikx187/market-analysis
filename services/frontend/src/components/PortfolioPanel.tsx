import { useEffect, useState } from "react";
import { fetchPortfolio, updateBalance, type Portfolio } from "@/lib/api";

export default function PortfolioPanel() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [equityHistory, setEquityHistory] = useState<number[]>([]);
  const [editingBalance, setEditingBalance] = useState(false);
  const [newBalance, setNewBalance] = useState("");
  const [balanceMsg, setBalanceMsg] = useState("");

  useEffect(() => {
    const loadPortfolio = () => {
      fetchPortfolio()
        .then((p) => {
          setPortfolio(p);
          // Track equity history for visualization
          setEquityHistory((prev) => {
            const newHistory = [...prev, p.equity];
            // Keep last 30 data points
            return newHistory.slice(-30);
          });
        })
        .catch(() => {});
    };

    loadPortfolio();
    // Poll portfolio every 30 seconds for live simulation tracking
    const interval = setInterval(loadPortfolio, 30000);
    return () => clearInterval(interval);
  }, []);

  if (!portfolio) return <div className="rounded-lg border bg-[var(--card)] p-4">Loading portfolio...</div>;

  const pnlColor = portfolio.total_pnl >= 0 ? "text-green-400" : "text-red-400";
  const equityChange = equityHistory.length > 1 
    ? ((portfolio.equity - equityHistory[0]) / equityHistory[0] * 100).toFixed(2)
    : "0.00";
  const isPositive = parseFloat(equityChange) >= 0;

  // Simple equity curve visualization using ASCII-like bars
  const maxEquity = Math.max(...equityHistory, portfolio.equity);
  const minEquity = Math.min(...equityHistory, portfolio.equity);
  const equityRange = maxEquity - minEquity || 1;

  return (
    <div className="rounded-lg border bg-[var(--card)] p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Portfolio & Trading</h2>
        <div className="flex items-center gap-2">
          <div className="h-2 w-2 rounded-full bg-green-500 animate-pulse" />
          <span className="text-xs text-green-400">Active</span>
        </div>
      </div>

      {/* Balance Update */}
      <div className="mb-4 p-3 rounded border border-[var(--border)] bg-[var(--background)]">
        <div className="flex items-center justify-between">
          <div>
            <span className="text-xs text-[var(--muted-foreground)]">Account Balance</span>
            <div className="text-lg font-semibold">${portfolio.balance.toLocaleString()}</div>
          </div>
          {!editingBalance ? (
            <button
              onClick={() => { setEditingBalance(true); setNewBalance(portfolio.balance.toString()); setBalanceMsg(""); }}
              className="rounded bg-[var(--secondary)] px-3 py-1.5 text-xs hover:bg-[var(--accent)]"
            >
              Update Balance
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
              <button
                onClick={async () => {
                  const val = parseFloat(newBalance);
                  if (isNaN(val) || val < 0) return;
                  try {
                    const res = await updateBalance(val);
                    setBalanceMsg(res.message);
                    setEditingBalance(false);
                    fetchPortfolio().then(setPortfolio);
                  } catch {
                    setBalanceMsg("Failed to update balance.");
                  }
                }}
                className="rounded bg-green-600 text-white px-2 py-1 text-xs"
              >
                Save
              </button>
              <button onClick={() => setEditingBalance(false)} className="text-xs text-[var(--muted-foreground)]">Cancel</button>
            </div>
          )}
        </div>
        {balanceMsg && <div className="text-xs text-green-400 mt-1">{balanceMsg}</div>}
        <p className="text-xs text-[var(--muted-foreground)] mt-1">
          Set this to your actual trading capital. Recommendations will be sized based on this balance.
        </p>
      </div>

      {/* Equity Curve Visualization */}
      {equityHistory.length > 1 && (
        <div className="mb-4 p-3 rounded border border-[var(--border)] bg-[var(--background)]">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs text-[var(--muted-foreground)]">Equity Curve (Live)</span>
            <span className={`text-xs ${isPositive ? "text-green-400" : "text-red-400"}`}>
              {isPositive ? "+" : ""}{equityChange}% from start
            </span>
          </div>
          <div className="flex items-end gap-0.5 h-16">
            {equityHistory.map((eq, i) => {
              const heightPct = ((eq - minEquity) / equityRange) * 100;
              const barColor = i > 0 && eq >= equityHistory[i - 1] ? "bg-green-500" : "bg-red-500";
              return (
                <div
                  key={i}
                  className={`flex-1 ${barColor} rounded-t`}
                  style={{ height: `${Math.max(heightPct, 5)}%`, minHeight: "2px" }}
                  title={`$${eq.toLocaleString()}`}
                />
              );
            })}
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat label="Balance" value={`$${portfolio.balance.toLocaleString()}`} info="Available paper-trading cash after open positions." />
        <Stat label="Equity" value={`$${portfolio.equity.toLocaleString()}`} info="Total account value including cash and open position value." />
        <Stat label="Total PnL" value={`$${portfolio.total_pnl.toLocaleString()}`} className={pnlColor} info="Cumulative realized and tracked profit/loss." />
        <Stat label="Win Rate" value={`${portfolio.win_rate}%`} info="Percentage of closed trades that were profitable." />
        <Stat label="Max Drawdown" value={`${portfolio.max_drawdown}%`} info="Largest peak-to-trough equity decline." />
        <Stat label="Profit Factor" value={portfolio.profit_factor.toString()} info="Gross profit divided by gross loss." />
        <Stat label="Wins / Losses" value={`${portfolio.win_count} / ${portfolio.loss_count}`} info="Closed profitable trades compared with closed losing trades." />
        <Stat label="Open Positions" value={`${portfolio.open_positions || 0}`} info="Currently active paper trades." />
      </div>
    </div>
  );
}

function Stat({ label, value, className = "", info = "" }: { label: string; value: string; className?: string; info?: string }) {
  return (
    <div>
      <div className="text-xs text-[var(--muted-foreground)]">
        {label} {info && <span title={info} className="cursor-help rounded-full border px-1">i</span>}
      </div>
      <div className={`text-lg font-semibold ${className}`}>{value}</div>
    </div>
  );
}
