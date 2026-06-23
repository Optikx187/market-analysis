import { useEffect, useRef, useState } from "react";
import {
  fetchAssets,
  addAsset,
  removeAsset,
  analyzeSignal,
  processSignal,
  fetchQuote,
  lookupSymbol,
  fetchCandleCounts,
  refreshData,
  refreshAllData,
  exportAssets,
  importAssets,
  type Asset,
  type Quote,
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
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [feedback, setFeedback] = useState("");
  const [lookupError, setLookupError] = useState("");
  const [pollingEnabled, setPollingEnabled] = useState(true);
  const [pollInterval, setPollInterval] = useState(30);
  const [candleCounts, setCandleCounts] = useState<Record<string, number>>({});
  const [refreshing, setRefreshing] = useState<string | null>(null);
  const [refreshingAll, setRefreshingAll] = useState(false);
  const [expandedTicker, setExpandedTicker] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const load = () => fetchAssets().then(setAssets).catch(() => setFeedback("Unable to load watchlist."));

  const loadCandleCounts = async () => {
    try {
      const counts = await fetchCandleCounts();
      setCandleCounts(counts);
    } catch {
      // Fallback: no counts available
    }
  };

  useEffect(() => { load(); }, []);
  useEffect(() => { 
    if (assets.length > 0) loadCandleCounts();
  }, [assets.length]);

  // Polling: batch quote fetches with Promise.all
  useEffect(() => {
    if (!pollingEnabled || assets.length === 0) return;
    
    const fetchQuotes = () => {
      Promise.all(
        assets.map((a) =>
          fetchQuote(a.ticker, a.asset_type)
            .then((q) => ({ ticker: a.ticker, quote: q }))
            .catch(() => null)
        )
      ).then((results) => {
        const updated: Record<string, Quote> = {};
        for (const r of results) {
          if (r) updated[r.ticker] = r.quote;
        }
        setQuotes((prev) => ({ ...prev, ...updated }));
      });
    };
    
    fetchQuotes();
    const interval = setInterval(fetchQuotes, pollInterval * 1000);
    return () => clearInterval(interval);
  }, [assets, pollingEnabled, pollInterval]);

  useEffect(() => {
    if (ticker.length < 2) { setName(""); setLookupError(""); return; }
    const handle = setTimeout(() => {
      lookupSymbol(ticker, assetType).then((res) => {
        if (res.recognized && res.name && res.name !== res.ticker) {
          setName(res.name);
          setLookupError("");
        } else if (res.recognized) {
          setName(res.name);
          setLookupError("");
        } else {
          setName("");
          setLookupError(`"${ticker}" not recognized as a valid ${assetType} ticker.`);
        }
      }).catch(() => { setName(""); setLookupError(""); });
    }, 500);
    return () => clearTimeout(handle);
  }, [ticker, assetType]);

  const handleAdd = async () => {
    if (!ticker || !name) return;
    setFeedback("");
    
    try {
      const lookup = await lookupSymbol(ticker, assetType);
      if (!lookup.recognized) {
        setFeedback(`Ticker "${ticker}" not found on ${assetType === "crypto" ? "Binance" : "market data provider"}. Please check the symbol and try again.`);
        return;
      }
    } catch (e: any) {
      setFeedback(`Unable to validate ticker: ${e?.response?.data?.detail || "API unavailable. Please check your market data API credentials and try again."}`);
      return;
    }
    
    try {
      await addAsset(ticker, name, assetType);
      setTicker("");
      setName("");
      setFeedback("");
      setLookupError("");
      load();
    } catch (e: any) {
      setFeedback(e?.response?.data?.detail || "Failed to add ticker.");
    }
  };

  const handleRemove = async (t: string) => {
    try {
      await removeAsset(t);
      setFeedback("");
      load();
    } catch (e: any) {
      setFeedback(e?.response?.data?.detail || `Failed to remove ${t}. Please try again.`);
    }
  };

  const handleAnalyze = async (t: string) => {
    setAnalyzing(t);
    setFeedback("");
    try {
      const candleCount = candleCounts[t] || 0;
      if (candleCount < 201) {
        setFeedback(`${t}: Insufficient data (${candleCount} candles, need 201+). Click "Refresh Data" to fetch historical data.`);
        return;
      }
      
      const asset = assets.find((a) => a.ticker === t);
      const type = asset?.asset_type ?? "stock";
      const signal = await analyzeSignal(t, 10000, type);
      if (!signal) {
        setFeedback(`${t}: No actionable signal returned by quant engine.`);
        return;
      }
      const decision = await processSignal(signal);
      setLastDecision(decision);
      onSignalProcessed?.(decision);
      setFeedback(`${t}: Analysis complete.`);
    } catch (e: any) {
      const errorDetail = e?.response?.data?.detail;
      if (errorDetail?.includes("Insufficient data")) {
        setFeedback(`${t}: ${errorDetail}. Click "Refresh Data" to fetch historical data.`);
      } else {
        setFeedback(errorDetail || `${t}: Analyze failed. Check service logs and candle data.`);
      }
    } finally {
      setAnalyzing(null);
    }
  };
  
  const handleRefresh = async (t: string) => {
    setRefreshing(t);
    setFeedback(`Fetching historical data for ${t}...`);
    try {
      const result = await refreshData(t);
      setCandleCounts((prev) => ({ ...prev, [t]: result.candles }));
      if (result.candles >= 201) {
        setFeedback(`${t}: Data refreshed successfully (${result.candles} candles). Ready for analysis.`);
      } else {
        setFeedback(`${t}: Data refreshed (${result.candles} candles). Need 201+ candles for analysis.`);
      }
    } catch (e: any) {
      setFeedback(e?.response?.data?.detail || `${t}: Failed to refresh data.`);
    } finally {
      setRefreshing(null);
    }
  };

  const handleRefreshAll = async () => {
    setRefreshingAll(true);
    setFeedback(`Refreshing data for all ${assets.length} tickers...`);
    try {
      const result = await refreshAllData();
      const details = result.details;
      const newCounts: Record<string, number> = {};
      for (const [t, d] of Object.entries(details)) {
        if (d.candles !== undefined) newCounts[t] = d.candles;
      }
      setCandleCounts((prev) => ({ ...prev, ...newCounts }));
      setFeedback(`Refreshed ${result.refreshed}/${result.total} tickers successfully.`);
    } catch (e: any) {
      setFeedback(e?.response?.data?.detail || "Failed to refresh all data.");
    } finally {
      setRefreshingAll(false);
    }
  };

  const handleExport = async () => {
    try {
      const data = await exportAssets();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `watchlist-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setFeedback(`Exported ${data.length} tickers.`);
    } catch {
      setFeedback("Failed to export watchlist.");
    }
  };

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const items = JSON.parse(text);
      if (!Array.isArray(items) || items.length === 0) {
        setFeedback("Invalid file: expected a JSON array of tickers.");
        return;
      }
      // Validate shape
      for (const item of items) {
        if (!item.ticker || !item.name || !item.asset_type) {
          setFeedback('Invalid format: each item needs "ticker", "name", and "asset_type" fields.');
          return;
        }
      }
      const result = await importAssets(items);
      setFeedback(
        `Imported ${result.total_imported} tickers (${result.added.length} new, ${result.reactivated.length} reactivated, ${result.skipped.length} skipped).`
      );
      load();
    } catch {
      setFeedback("Failed to import: invalid JSON file.");
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  return (
    <div className="rounded-lg border bg-[var(--card)] p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Watchlist</h2>
        <div className="flex items-center gap-2 text-xs">
          <label className="flex items-center gap-1 cursor-pointer">
            <input
              type="checkbox"
              checked={pollingEnabled}
              onChange={(e) => setPollingEnabled(e.target.checked)}
              className="rounded"
            />
            Live Prices
          </label>
          <select
            className="rounded border bg-[var(--input)] px-1 py-0.5 text-xs"
            value={pollInterval}
            onChange={(e) => setPollInterval(Number(e.target.value))}
            disabled={!pollingEnabled}
            title="Polling interval"
          >
            <option value={10}>10s</option>
            <option value={30}>30s</option>
            <option value={60}>1m</option>
            <option value={300}>5m</option>
          </select>
        </div>
      </div>

      {/* Action bar: Refresh All, Export, Import */}
      {assets.length > 0 && (
        <div className="flex gap-2 mb-3">
          <button
            onClick={handleRefreshAll}
            disabled={refreshingAll}
            className="rounded bg-[var(--secondary)] px-2 py-1 text-xs hover:bg-[var(--accent)] disabled:opacity-50"
          >
            {refreshingAll ? "Refreshing..." : "Refresh All"}
          </button>
          <button
            onClick={handleExport}
            className="rounded bg-[var(--secondary)] px-2 py-1 text-xs hover:bg-[var(--accent)]"
          >
            Export
          </button>
          <label className="rounded bg-[var(--secondary)] px-2 py-1 text-xs hover:bg-[var(--accent)] cursor-pointer">
            Import
            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              onChange={handleImport}
              className="hidden"
            />
          </label>
        </div>
      )}

      {feedback && <div className="mb-3 rounded border border-yellow-600 bg-yellow-600/10 p-2 text-xs text-yellow-300">{feedback}</div>}
      {lookupError && <div className="mb-3 rounded border border-red-600 bg-red-600/10 p-2 text-xs text-red-300">{lookupError}</div>}

      <div className="flex flex-wrap gap-2 mb-4">
        <select
          className="rounded border bg-[var(--input)] px-2 py-1 text-sm"
          value={assetType} onChange={(e) => { setAssetType(e.target.value); setName(""); setLookupError(""); }}
        >
          <option value="crypto">Crypto</option>
          <option value="stock">Stock</option>
        </select>
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
        <button
          onClick={handleAdd}
          disabled={!ticker || !name}
          className="rounded bg-[var(--primary)] text-[var(--primary-foreground)] px-3 py-1 text-sm font-medium disabled:opacity-50 whitespace-nowrap"
        >
          + Add
        </button>
      </div>

      {/* Empty state with import option */}
      {assets.length === 0 && (
        <div className="rounded border border-dashed p-4 text-center text-sm text-[var(--muted-foreground)]">
          <p className="mb-1">Your watchlist is empty.</p>
          <p className="text-xs mb-2">Add tickers above to start tracking. Examples: <strong>BTC</strong>, <strong>ETH</strong>, <strong>SPY</strong>, <strong>AAPL</strong></p>
          <label className="inline-block rounded bg-[var(--secondary)] px-3 py-1 text-xs hover:bg-[var(--accent)] cursor-pointer">
            Import from file
            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              onChange={handleImport}
              className="hidden"
            />
          </label>
        </div>
      )}

      <div className="max-h-[420px] overflow-y-auto space-y-1">
        {assets.map((a) => {
          const q = quotes[a.ticker];
          const isExpanded = expandedTicker === a.ticker;
          return (
            <div key={a.ticker} className="rounded border">
              {/* Compact row — always visible */}
              <div
                className="flex items-center gap-2 px-2 py-1.5 cursor-pointer hover:bg-[var(--secondary)]/30"
                onClick={() => setExpandedTicker(isExpanded ? null : a.ticker)}
              >
                <span className="font-medium text-sm w-14 shrink-0">{a.ticker}</span>
                <span className="text-xs text-[var(--muted-foreground)] truncate flex-1 min-w-0">{a.name}</span>
                <span className="text-sm font-medium tabular-nums w-24 text-right">
                  {q?.price ? `$${q.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : <span className="text-[var(--muted-foreground)] text-xs">Loading...</span>}
                </span>
                <span className={`text-xs tabular-nums w-16 text-right ${q?.change_pct !== undefined && q?.change_pct !== null ? (q.change_pct >= 0 ? "text-green-400" : "text-red-400") : "text-[var(--muted-foreground)]"}`}>
                  {q?.change_pct !== undefined && q?.change_pct !== null ? `${q.change_pct >= 0 ? "+" : ""}${q.change_pct}%` : "—"}
                </span>
                <span className="text-[10px] px-1 py-0.5 rounded bg-[var(--secondary)] text-[var(--muted-foreground)] shrink-0">{a.asset_type}</span>
                <svg className={`w-3 h-3 shrink-0 text-[var(--muted-foreground)] transition-transform ${isExpanded ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" /></svg>
              </div>
              {/* Expanded details */}
              {isExpanded && (
                <div className="border-t border-[var(--border)] px-2 py-2 space-y-2">
                  <div className="flex items-center gap-2 text-xs text-[var(--muted-foreground)]">
                    <span>Vol: {q?.volume ? q.volume.toLocaleString() : "N/A"}</span>
                    <span>·</span>
                    <span className={candleCounts[a.ticker] >= 201 ? "text-green-400" : "text-yellow-400"}>
                      {candleCounts[a.ticker] ?? 0} candles
                    </span>
                  </div>
                  <div className="flex gap-1">
                    <button onClick={(e) => { e.stopPropagation(); handleRefresh(a.ticker); }} disabled={refreshing === a.ticker}
                      className="rounded bg-[var(--secondary)] px-2 py-0.5 text-xs hover:bg-[var(--accent)]">
                      {refreshing === a.ticker ? "Fetching..." : "Refresh Data"}
                    </button>
                    <button onClick={(e) => { e.stopPropagation(); handleAnalyze(a.ticker); }} disabled={analyzing === a.ticker || (candleCounts[a.ticker] || 0) < 201}
                      className={`rounded px-2 py-0.5 text-xs ${
                        (candleCounts[a.ticker] || 0) >= 201
                          ? "bg-[var(--accent)] hover:bg-[var(--primary)] hover:text-[var(--primary-foreground)]"
                          : "bg-[var(--secondary)] opacity-50 cursor-not-allowed"
                      }`}>
                      {analyzing === a.ticker ? "Analyzing..." : "Analyze"}
                    </button>
                    <button onClick={(e) => { e.stopPropagation(); handleRemove(a.ticker); }}
                      className="rounded bg-[var(--destructive)] text-[var(--destructive-foreground)] px-2 py-0.5 text-xs">
                      Remove
                    </button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {lastDecision && (
        <div className={`mt-4 rounded border p-3 text-sm ${lastDecision.approved ? "border-green-600" : "border-red-600"}`}>
          <div className="font-semibold">{lastDecision.ticker}  {lastDecision.direction} ({lastDecision.status})</div>
          <div>Approved: {lastDecision.approved ? "Yes" : "No"}</div>
          <div>Kelly: {lastDecision.kelly_pct}% | Size: ${lastDecision.optimal_size_usd}</div>
          {lastDecision.capital_overspend && <div className="text-red-400 font-bold mt-1">CAPITAL OVERSPEND WARNING</div>}
          <div className="text-xs text-[var(--muted-foreground)] mt-1">{lastDecision.reason}</div>
        </div>
      )}
    </div>
  );
}
