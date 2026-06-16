import { useEffect, useState } from "react";
import { fetchTrades, logManualTrade, type Trade } from "@/lib/api";

function formatDateTime(dateStr: string | null): string {
  if (!dateStr) return "—";
  const date = new Date(dateStr);
  return date.toLocaleString("en-US", {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit", hour12: true,
  });
}

export default function TradesPanel() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ ticker: "", direction: "BUY", entry_price: "", quantity: "", stop_loss: "", target_price: "" });
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [saving, setSaving] = useState(false);

  const loadTrades = () => fetchTrades().then(setTrades).catch(() => {});
  useEffect(() => { loadTrades(); }, []);

  const handleSubmit = async () => {
    const price = parseFloat(form.entry_price);
    const qty = parseFloat(form.quantity);
    if (!form.ticker || isNaN(price) || isNaN(qty) || price <= 0 || qty <= 0) {
      setMessage({ type: "error", text: "Ticker, price, and quantity are required." });
      return;
    }
    setSaving(true);
    setMessage(null);
    try {
      await logManualTrade({
        ticker: form.ticker,
        direction: form.direction,
        entry_price: price,
        quantity: qty,
        stop_loss: form.stop_loss ? parseFloat(form.stop_loss) : undefined,
        target_price: form.target_price ? parseFloat(form.target_price) : undefined,
      });
      setMessage({ type: "success", text: `Trade logged: ${form.direction} ${qty} ${form.ticker} @ $${price}` });
      setForm({ ticker: "", direction: "BUY", entry_price: "", quantity: "", stop_loss: "", target_price: "" });
      setShowForm(false);
      loadTrades();
    } catch {
      setMessage({ type: "error", text: "Failed to log trade." });
    }
    setSaving(false);
  };

  return (
    <div className="rounded-lg border bg-[var(--card)] p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Trades</h2>
        <button
          onClick={() => { setShowForm(!showForm); setMessage(null); }}
          className="rounded bg-[var(--secondary)] px-3 py-1.5 text-xs hover:bg-[var(--accent)]"
        >
          {showForm ? "Cancel" : "Log Trade"}
        </button>
      </div>

      {message && (
        <div className={`mb-3 rounded border p-2 text-xs ${
          message.type === "success" ? "border-green-600 bg-green-600/10 text-green-400" : "border-red-600 bg-red-600/10 text-red-400"
        }`}>
          {message.text}
        </div>
      )}

      {showForm && (
        <div className="mb-4 p-3 rounded border border-[var(--border)] bg-[var(--background)] space-y-2">
          <div className="text-xs font-medium text-[var(--muted-foreground)] mb-1">Log a manually executed trade</div>
          <div className="grid grid-cols-2 gap-2">
            <input value={form.ticker} onChange={(e) => setForm({ ...form, ticker: e.target.value.toUpperCase() })}
              placeholder="Ticker (e.g. BTC)" className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs" />
            <select value={form.direction} onChange={(e) => setForm({ ...form, direction: e.target.value })}
              className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs">
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
            </select>
            <input type="number" value={form.entry_price} onChange={(e) => setForm({ ...form, entry_price: e.target.value })}
              placeholder="Entry price" step="0.01" className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs" />
            <input type="number" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })}
              placeholder="Quantity" step="0.0001" className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs" />
            <input type="number" value={form.stop_loss} onChange={(e) => setForm({ ...form, stop_loss: e.target.value })}
              placeholder="Stop loss (opt)" step="0.01" className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs" />
            <input type="number" value={form.target_price} onChange={(e) => setForm({ ...form, target_price: e.target.value })}
              placeholder="Target price (opt)" step="0.01" className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs" />
          </div>
          <div className="flex justify-end">
            <button onClick={handleSubmit} disabled={saving}
              className="rounded bg-[var(--primary)] text-[var(--primary-foreground)] px-4 py-1.5 text-xs font-medium disabled:opacity-50">
              {saving ? "Saving..." : "Log Trade"}
            </button>
          </div>
        </div>
      )}

      {trades.length === 0 ? (
        <p className="text-sm text-[var(--muted-foreground)]">No trades yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-[var(--muted-foreground)] border-b">
                <th className="pb-2">Ticker</th>
                <th className="pb-2">Dir</th>
                <th className="pb-2">Entry</th>
                <th className="pb-2">Exit</th>
                <th className="pb-2">Qty</th>
                <th className="pb-2">PnL</th>
                <th className="pb-2">Status</th>
                <th className="pb-2">Opened</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.id} className="border-b border-[var(--border)]">
                  <td className="py-1.5 font-medium">{t.ticker}</td>
                  <td className={t.direction === "BUY" ? "text-green-400" : "text-red-400"}>
                    {t.direction}
                  </td>
                  <td>${t.entry_price.toFixed(2)}</td>
                  <td>{t.exit_price ? `$${t.exit_price.toFixed(2)}` : "—"}</td>
                  <td>{t.quantity.toFixed(4)}</td>
                  <td className={
                    t.pnl != null ? (t.pnl >= 0 ? "text-green-400" : "text-red-400") : ""
                  }>
                    {t.pnl != null ? `$${t.pnl.toFixed(2)}` : "—"}
                  </td>
                  <td>
                    <span className={`px-1.5 py-0.5 rounded text-xs ${
                      t.status === "OPEN" ? "bg-blue-900 text-blue-300" : "bg-gray-800 text-gray-400"
                    }`}>
                      {t.status}
                    </span>
                  </td>
                  <td className="text-xs text-[var(--muted-foreground)]">{formatDateTime(t.opened_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
