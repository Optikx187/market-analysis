import { useEffect, useState } from "react";
import { fetchAlerts, type AlertLog, type RiskDecision } from "@/lib/api";

function parseRiskDecision(value: string | null): RiskDecision | null {
  if (!value) return null;
  try {
    return JSON.parse(value) as RiskDecision;
  } catch {
    return null;
  }
}

function formatDateTime(dateStr: string | null): string {
  if (!dateStr) return "Unknown";
  const date = new Date(dateStr);
  return date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

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
          {alerts.map((a) => {
            const decision = parseRiskDecision(a.risk_decision_json);
            const action = decision?.action || (a.approved ? "approved" : "rejected");
            const rejected = !a.approved || action === "rejected";
            const reduced = action === "reduced";
            return (
              <div
                key={a.id}
                className={`rounded border p-3 text-sm ${rejected ? "border-red-600/70" : reduced ? "border-orange-500/70" : "border-[var(--border)]"}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold">{a.ticker}</span>
                  <div className="flex items-center gap-1">
                    <span className={`px-2 py-0.5 rounded text-xs ${
                      rejected ? "bg-red-900 text-red-300" : reduced ? "bg-orange-900 text-orange-300" : "bg-green-900 text-green-300"
                    }`}>
                      {action.replace("_", " ").toUpperCase()}
                    </span>
                    <span className={`px-2 py-0.5 rounded text-xs ${
                      a.direction === "BUY" ? "bg-green-900 text-green-300" :
                      a.direction === "SELL" ? "bg-red-900 text-red-300" :
                      "bg-yellow-900 text-yellow-300"
                    }`}>
                      {a.direction}
                    </span>
                  </div>
                </div>
                <div className="text-xs text-[var(--muted-foreground)] mt-1">
                  {formatDateTime(a.created_at)} | Status: {a.status} | Price: ${a.trigger_price.toFixed(2)}
                  {decision && ` | Requested: $${decision.requested_size_usd.toFixed(2)} | Recommended: $${decision.recommended_size_usd.toFixed(2)}`}
                  {!decision && a.optimal_size_usd != null && ` | Size: $${a.optimal_size_usd.toFixed(2)}`}
                  {a.kelly_pct != null && ` | Kelly: ${a.kelly_pct}%`}
                </div>
                {decision?.reasons.map((reason) => (
                  <div key={`${reason.code}-${reason.message}`} className={`mt-1 text-xs ${rejected ? "text-red-400" : "text-orange-400"}`}>
                    <span className="font-medium">{reason.code}</span>: {reason.message}
                  </div>
                ))}
                {a.capital_overspend && !decision && (
                  <div className="text-xs text-red-400 font-bold mt-0.5">CAPITAL OVERSPEND WARNING</div>
                )}
                {a.message && (
                  <div className="text-xs text-[var(--muted-foreground)] mt-1">{a.message}</div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
