import { useEffect, useState } from "react";
import { fetchTrades, type Trade } from "@/lib/api";

export default function TradesPanel() {
  const [trades, setTrades] = useState<Trade[]>([]);

  useEffect(() => { fetchTrades().then(setTrades).catch(() => {}); }, []);

  return (
    <div className="rounded-lg border bg-[var(--card)] p-4">
      <h2 className="text-lg font-semibold mb-3">Trades</h2>
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
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
