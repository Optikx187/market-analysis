import { useEffect, useState } from "react";
import {
  fetchPriceAlerts,
  createPriceAlert,
  deletePriceAlert,
  checkPriceAlerts,
  type PriceAlertItem,
} from "@/lib/api";

export default function PriceAlertsPanel() {
  const [alerts, setAlerts] = useState<PriceAlertItem[]>([]);
  const [ticker, setTicker] = useState("");
  const [condition, setCondition] = useState("below");
  const [threshold, setThreshold] = useState("");
  const [msg, setMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [checking, setChecking] = useState(false);

  const load = () => fetchPriceAlerts().then(setAlerts).catch(() => {});

  useEffect(() => {
    load();
  }, []);

  const handleCreate = async () => {
    const t = ticker.trim().toUpperCase();
    const th = parseFloat(threshold);
    if (!t || isNaN(th) || th <= 0) {
      setMsg({ type: "error", text: "Enter a valid ticker and price threshold" });
      return;
    }
    try {
      await createPriceAlert(t, condition, th);
      setMsg({ type: "success", text: `Alert created: ${t} ${condition} $${th}` });
      setTicker("");
      setThreshold("");
      load();
    } catch {
      setMsg({ type: "error", text: "Failed to create alert" });
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await deletePriceAlert(id);
      load();
    } catch {
      setMsg({ type: "error", text: "Failed to delete alert" });
    }
  };

  const handleCheck = async () => {
    setChecking(true);
    try {
      const result = await checkPriceAlerts();
      if (result.triggered.length > 0) {
        setMsg({
          type: "success",
          text: `${result.triggered.length} alert(s) triggered: ${result.triggered.map((t) => `${t.ticker} ${t.condition} $${t.threshold} (now $${t.current_price.toFixed(2)})`).join(", ")}`,
        });
      } else {
        setMsg({ type: "success", text: `Checked ${result.checked} alerts — none triggered` });
      }
      load();
    } catch {
      setMsg({ type: "error", text: "Failed to check alerts" });
    }
    setChecking(false);
  };

  const active = alerts.filter((a) => !a.triggered);
  const triggered = alerts.filter((a) => a.triggered);

  return (
    <div className="rounded-lg border bg-[var(--card)] p-4">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-semibold">Price Alerts</h3>
          <p className="text-xs text-[var(--muted-foreground)]">
            Get notified when a ticker crosses a price threshold
          </p>
        </div>
        <button
          onClick={handleCheck}
          disabled={checking}
          className="rounded bg-[var(--secondary)] px-3 py-1 text-xs hover:bg-[var(--accent)] disabled:opacity-50"
        >
          {checking ? "Checking..." : "Check Now"}
        </button>
      </div>

      {msg && (
        <div className={`mb-3 rounded border p-2 text-xs ${
          msg.type === "success" ? "border-green-600 bg-green-600/10 text-green-400" : "border-red-600 bg-red-600/10 text-red-400"
        }`}>
          {msg.text}
        </div>
      )}

      <div className="flex gap-2 mb-4">
        <input
          value={ticker}
          onChange={(e) => setTicker(e.target.value)}
          placeholder="Ticker"
          className="w-24 rounded border bg-[var(--input)] px-2 py-1.5 text-sm"
        />
        <select
          value={condition}
          onChange={(e) => setCondition(e.target.value)}
          className="rounded border bg-[var(--input)] px-2 py-1.5 text-sm"
        >
          <option value="below">Below</option>
          <option value="above">Above</option>
        </select>
        <input
          value={threshold}
          onChange={(e) => setThreshold(e.target.value)}
          placeholder="Price"
          type="number"
          step="0.01"
          className="w-28 rounded border bg-[var(--input)] px-2 py-1.5 text-sm"
        />
        <button
          onClick={handleCreate}
          className="rounded bg-[var(--primary)] text-[var(--primary-foreground)] px-3 py-1.5 text-xs font-medium"
        >
          Add Alert
        </button>
      </div>

      {active.length > 0 && (
        <div className="mb-3">
          <div className="text-xs font-medium text-[var(--muted-foreground)] mb-1">Active Alerts</div>
          <div className="space-y-1">
            {active.map((a) => (
              <div key={a.id} className="flex items-center justify-between py-1.5 px-3 rounded bg-[var(--background)]">
                <div className="flex items-center gap-2">
                  <div className="h-2 w-2 rounded-full bg-blue-500" />
                  <span className="text-sm font-medium">{a.ticker}</span>
                  <span className="text-xs text-[var(--muted-foreground)]">{a.condition} ${a.threshold.toLocaleString()}</span>
                </div>
                <button
                  onClick={() => handleDelete(a.id)}
                  className="text-xs text-red-400 hover:text-red-300"
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {triggered.length > 0 && (
        <div>
          <div className="text-xs font-medium text-[var(--muted-foreground)] mb-1">Triggered</div>
          <div className="space-y-1">
            {triggered.map((a) => (
              <div key={a.id} className="flex items-center justify-between py-1.5 px-3 rounded bg-[var(--background)] opacity-60">
                <div className="flex items-center gap-2">
                  <div className="h-2 w-2 rounded-full bg-green-500" />
                  <span className="text-sm font-medium">{a.ticker}</span>
                  <span className="text-xs text-[var(--muted-foreground)]">{a.condition} ${a.threshold.toLocaleString()}</span>
                  {a.triggered_at && (
                    <span className="text-[10px] text-[var(--muted-foreground)]">
                      triggered {new Date(a.triggered_at).toLocaleString()}
                    </span>
                  )}
                </div>
                <button
                  onClick={() => handleDelete(a.id)}
                  className="text-xs text-[var(--muted-foreground)] hover:text-red-400"
                >
                  Clear
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {alerts.length === 0 && (
        <div className="text-xs text-[var(--muted-foreground)] text-center py-4">
          No price alerts set. Use the form above to create one.
        </div>
      )}
    </div>
  );
}
