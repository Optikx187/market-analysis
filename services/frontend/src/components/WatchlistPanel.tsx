import { useEffect, useState } from "react";
import {
  fetchAssets,
  addAsset,
  removeAsset,
  analyzeSignal,
  processSignal,
  type Asset,
  type Signal,
  type SignalDecision,
} from "@/lib/api";

interface Props {
  onSignalProcessed?: (decision: SignalDecision) => void;
}

export default function WatchlistPanel({ onSignalProcessed }: Props) {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [ticker, setTicker] = useState("");
  const [name, setName] = useState("");
  const [assetType, setAssetType] = useState("crypto");
  const [analyzing, setAnalyzing] = useState<string | null>(null);
  const [lastDecision, setLastDecision] = useState<SignalDecision | null>(null);

  const load = () => fetchAssets().then(setAssets).catch(() => {});

  useEffect(() => { load(); }, []);

  const handleAdd = async () => {
    if (!ticker || !name) return;
    await addAsset(ticker, name, assetType);
    setTicker("");
    setName("");
    load();
  };

  const handleRemove = async (t: string) => {
    await removeAsset(t);
    load();
  };

  const handleAnalyze = async (t: string) => {
    setAnalyzing(t);
    try {
      const signal = await analyzeSignal(t);
      if (signal) {
        const decision = await processSignal(signal);
        setLastDecision(decision);
        onSignalProcessed?.(decision);
      }
    } catch {
      // Insufficient data or service error
    }
    setAnalyzing(null);
  };

  return (
    <div className="rounded-lg border bg-[var(--card)] p-4">
      <h2 className="text-lg font-semibold mb-3">Watchlist</h2>

      <div className="flex flex-wrap gap-2 mb-4">
        <div className="flex gap-2 flex-1 min-w-0">
          <input
            className="w-24 min-w-0 rounded border bg-[var(--input)] px-2 py-1 text-sm"
            placeholder="Ticker" value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          />
          <input
            className="flex-1 min-w-0 rounded border bg-[var(--input)] px-2 py-1 text-sm"
            placeholder="Name" value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          />
        </div>
        <div className="flex gap-2">
          <select
            className="rounded border bg-[var(--input)] px-2 py-1 text-sm"
            value={assetType} onChange={(e) => setAssetType(e.target.value)}
          >
            <option value="crypto">Crypto</option>
            <option value="stock">Stock</option>
          </select>
          <button
            onClick={handleAdd}
            disabled={!ticker || !name}
            className="rounded bg-[var(--primary)] text-[var(--primary-foreground)] px-3 py-1 text-sm font-medium disabled:opacity-50 whitespace-nowrap"
          >
            + Add
          </button>
        </div>
      </div>

      <div className="space-y-2">
        {assets.length === 0 && (
          <div className="rounded border border-dashed p-4 text-center text-sm text-[var(--muted-foreground)]">
            <p className="mb-1">Your watchlist is empty.</p>
            <p className="text-xs">
              Add tickers above to start tracking. Examples:
              <strong> BTC</strong> (Bitcoin), <strong>ETH</strong> (Ethereum),
              <strong> SPY</strong> (S&P 500), <strong>AAPL</strong> (Apple)
            </p>
          </div>
        )}
        {assets.map((a) => (
          <div key={a.ticker} className="flex items-center justify-between rounded border p-2">
            <div>
              <span className="font-medium">{a.ticker}</span>
              <span className="text-xs text-[var(--muted-foreground)] ml-2">{a.name}</span>
              <span className="text-xs ml-2 px-1.5 py-0.5 rounded bg-[var(--secondary)]">
                {a.asset_type}
              </span>
            </div>
            <div className="flex gap-1">
              <button
                onClick={() => handleAnalyze(a.ticker)}
                disabled={analyzing === a.ticker}
                className="rounded bg-[var(--accent)] px-2 py-0.5 text-xs hover:bg-[var(--primary)] hover:text-[var(--primary-foreground)]"
              >
                {analyzing === a.ticker ? "..." : "Analyze"}
              </button>
              <button
                onClick={() => handleRemove(a.ticker)}
                className="rounded bg-[var(--destructive)] text-[var(--destructive-foreground)] px-2 py-0.5 text-xs"
              >
                Remove
              </button>
            </div>
          </div>
        ))}
      </div>

      {lastDecision && (
        <div className={`mt-4 rounded border p-3 text-sm ${
          lastDecision.approved ? "border-green-600" : "border-red-600"
        }`}>
          <div className="font-semibold">
            {lastDecision.ticker} — {lastDecision.direction} ({lastDecision.status})
          </div>
          <div>Approved: {lastDecision.approved ? "Yes" : "No"}</div>
          <div>Kelly: {lastDecision.kelly_pct}% | Size: ${lastDecision.optimal_size_usd}</div>
          {lastDecision.capital_overspend && (
            <div className="text-red-400 font-bold mt-1">CAPITAL OVERSPEND WARNING</div>
          )}
          <div className="text-xs text-[var(--muted-foreground)] mt-1">{lastDecision.reason}</div>
        </div>
      )}
    </div>
  );
}
