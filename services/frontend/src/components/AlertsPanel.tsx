import { useEffect, useState } from "react";
import { fetchAlerts, type AlertLog } from "@/lib/api";

export default function AlertsPanel() {
  const [alerts, setAlerts] = useState<AlertLog[]>([]);

  useEffect(() => { fetchAlerts().then(setAlerts).catch(() => {}); }, []);

  return (
    <div className="rounded-lg border bg-[var(--card)] p-4">
      <h2 className="text-lg font-semibold mb-3">Alert History</h2>
      {alerts.length === 0 ? (
        <p className="text-sm text-[var(--muted-foreground)]">No alerts yet.</p>
      ) : (
        <div className="space-y-2">
          {alerts.map((a) => (
            <div
              key={a.id}
              className={`rounded border p-3 text-sm ${
                a.capital_overspend ? "border-red-600" : "border-[var(--border)]"
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="font-semibold">{a.ticker}</span>
                <span className={`px-2 py-0.5 rounded text-xs ${
                  a.direction === "BUY" ? "bg-green-900 text-green-300" :
                  a.direction === "SELL" ? "bg-red-900 text-red-300" :
                  "bg-yellow-900 text-yellow-300"
                }`}>
                  {a.direction}
                </span>
              </div>
              <div className="text-xs text-[var(--muted-foreground)] mt-1">
                Status: {a.status} | Price: ${a.trigger_price.toFixed(2)}
                {a.optimal_size_usd != null && ` | Size: $${a.optimal_size_usd.toFixed(2)}`}
                {a.kelly_pct != null && ` | Kelly: ${a.kelly_pct}%`}
              </div>
              {a.robinhood_buying_power != null && (
                <div className="text-xs mt-0.5">
                  Robinhood Buying Power: ${a.robinhood_buying_power.toLocaleString()}
                </div>
              )}
              {a.capital_overspend && (
                <div className="text-xs text-red-400 font-bold mt-0.5">CAPITAL OVERSPEND WARNING</div>
              )}
              {a.message && (
                <div className="text-xs text-[var(--muted-foreground)] mt-1">{a.message}</div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
