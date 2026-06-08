import { useEffect, useState } from "react";
import {
  fetchAssets,
  addAsset,
  removeAsset,
  analyzeSignal,
  processSignal,
  fetchQuote,
  lookupSymbol,
  fetchCandles,
  refreshData,
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
  const [pollingEnabled, setPollingEnabled] = useState(true);
  const [pollInterval, setPollInterval] = useState(30); // seconds
  const [candleCounts, setCandleCounts] = useState<Record<string, number>>({});
  const [refreshing, setRefreshing] = useState<string | null>(null);

  const load = () => fetchAssets().then(setAssets).catch(() => setFeedback("Unable to load watchlist."));
  
  const loadCandleCounts = async () => {
    const counts: Record<string, number> = {};
    for (const asset of assets) {
      try {
        const candles = await fetchCandles(asset.ticker);
        counts[asset.ticker] = candles?.length || 0;
      } catch {
        counts[asset.ticker] = 0;
      }
    }
    setCandleCounts(counts);
  };

  useEffect(() => { load(); }, []);
  useEffect(() => { 
    if (assets.length > 0) loadCandleCounts();
  }, [assets]);

  // Polling effect for live price updates
  useEffect(() => {
    if (!pollingEnabled || assets.length === 0) return;
    
    const fetchQuotes = () => {
      assets.forEach((a) => {
        fetchQuote(a.ticker, a.asset_type).then((q) => setQuotes((p) => ({ ...p, [a.ticker]: q }))).catch(() => {});
      });
    };
    
    // Initial fetch
    fetchQuotes();
    
    // Set up interval
    const interval = setInterval(fetchQuotes, pollInterval * 1000);
    
    return () => clearInterval(interval);
  }, [assets, pollingEnabled, pollInterval]);

  useEffect(() => {
    if (ticker.length < 2) { setName(""); return; }
    const handle = setTimeout(() => {
      lookupSymbol(ticker, assetType).then((res) => {
        if (res.name && res.name !== res.ticker) setName(res.name);
        else if (res.recognized) setName(res.name);
        else setName("");
      }).catch(() => { setName(""); });
    }, 500);
    return () => clearTimeout(handle);
  }, [ticker, assetType]);

  const handleAdd = async () => {
    if (!ticker || !name) return;
    setFeedback("");
    
    // Validate ticker exists via API before adding
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
      load();
    } catch (e: any) {
      setFeedback(e?.response?.data?.detail || "Failed to add ticker.");
    }
  };

  const handleRemove = async (t: string) => {
    await removeAsset(t);
    load();
  };

  const handleAnalyze = async (t: string) => {
    setAnalyzing(t);
    setFeedback("");
    try {
      // Check if we have enough candle data
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
      {feedback && <div className="mb-3 rounded border border-yellow-600 bg-yellow-600/10 p-2 text-xs text-yellow-300">{feedback}</div>}

      <div className="flex flex-wrap gap-2 mb-4">
        <select
          className="rounded border bg-[var(--input)] px-2 py-1 text-sm"
          value={assetType} onChange={(e) => { setAssetType(e.target.value); setName(""); }}
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

      <div className="space-y-2">
        {assets.length === 0 && (
          <div className="rounded border border-dashed p-4 text-center text-sm text-[var(--muted-foreground)]">
            <p className="mb-1">Your watchlist is empty.</p>
            <p className="text-xs">Add tickers above to start tracking. Examples: <strong> BTC</strong>, <strong>ETH</strong>, <strong>SPY</strong>, <strong>AAPL</strong></p>
          </div>
        )}
        {assets.map((a) => {
          const q = quotes[a.ticker];
          return (
            <div key={a.ticker} className="rounded border p-2">
              <div className="flex items-center justify-between">
                <div>
                  <span className="font-medium">{a.ticker}</span>
                  <span className="text-xs text-[var(--muted-foreground)] ml-2">{a.name}</span>
                  <span className="text-xs ml-2 px-1.5 py-0.5 rounded bg-[var(--secondary)]">{a.asset_type}</span>
                  {candleCounts[a.ticker] !== undefined && (
                    <span className={`text-xs ml-2 px-1.5 py-0.5 rounded ${candleCounts[a.ticker] >= 201 ? "bg-green-600/20 text-green-400" : "bg-yellow-600/20 text-yellow-400"}`}>
                      {candleCounts[a.ticker]} candles
                    </span>
                  )}
                </div>
                <div className="flex gap-1">
                  <button onClick={() => handleRefresh(a.ticker)} disabled={refreshing === a.ticker}
                    className="rounded bg-[var(--secondary)] px-2 py-0.5 text-xs hover:bg-[var(--accent)]">
                    {refreshing === a.ticker ? "..." : "Refresh Data"}
                  </button>
                  <button onClick={() => handleAnalyze(a.ticker)} disabled={analyzing === a.ticker || (candleCounts[a.ticker] || 0) < 201}
                    className={`rounded px-2 py-0.5 text-xs ${
                      (candleCounts[a.ticker] || 0) >= 201 
                        ? "bg-[var(--accent)] hover:bg-[var(--primary)] hover:text-[var(--primary-foreground)]" 
                        : "bg-[var(--secondary)] opacity-50 cursor-not-allowed"
                    }`}>
                    {analyzing === a.ticker ? "..." : "Analyze"}
                  </button>
                  <button onClick={() => handleRemove(a.ticker)}
                    className="rounded bg-[var(--destructive)] text-[var(--destructive-foreground)] px-2 py-0.5 text-xs">
                    Remove
                  </button>
                </div>
              </div>
              <div className="mt-1 grid grid-cols-3 gap-2 text-xs text-[var(--muted-foreground)]">
                <div>Price: <span className="text-[var(--foreground)]">{q?.price ? `$${q.price.toLocaleString()}` : ""}</span></div>
                <div>24h/Day: <span className={q?.change_pct && q.change_pct >= 0 ? "text-green-400" : "text-red-400"}>{q?.change_pct ?? ""}%</span></div>
                <div>Volume: <span className="text-[var(--foreground)]">{q?.volume ? q.volume.toLocaleString() : ""}</span></div>
              </div>
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
