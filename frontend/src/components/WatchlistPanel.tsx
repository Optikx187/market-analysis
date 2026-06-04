import { useState, useEffect } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Plus,
  RefreshCw,
  TrendingUp,
  Trash2,
  Search,
  BarChart3,
} from "lucide-react";
import type { Asset } from "@/lib/api";
import {
  fetchAssets,
  addAsset,
  removeAsset,
  refreshData,
  analyzeSignal,
  runBacktest,
} from "@/lib/api";

interface Props {
  onSignalGenerated: () => void;
  onBacktestResult: (result: unknown) => void;
}

export default function WatchlistPanel({ onSignalGenerated, onBacktestResult }: Props) {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [newTicker, setNewTicker] = useState("");
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState<"crypto" | "stock">("crypto");
  const [loading, setLoading] = useState<string | null>(null);

  const loadAssets = async () => {
    try {
      const data = await fetchAssets();
      setAssets(data.filter((a) => a.is_active));
    } catch {
      /* empty */
    }
  };

  useEffect(() => {
    loadAssets();
  }, []);

  const handleAdd = async () => {
    if (!newTicker.trim()) return;
    try {
      await addAsset(newTicker.trim().toUpperCase(), newName || newTicker.toUpperCase(), newType);
      setNewTicker("");
      setNewName("");
      loadAssets();
    } catch {
      /* empty */
    }
  };

  const handleRemove = async (ticker: string) => {
    await removeAsset(ticker);
    loadAssets();
  };

  const handleRefresh = async (ticker: string) => {
    setLoading(ticker);
    try {
      await refreshData(ticker);
    } finally {
      setLoading(null);
    }
  };

  const handleAnalyze = async (ticker: string) => {
    setLoading(`analyze-${ticker}`);
    try {
      await analyzeSignal(ticker);
      onSignalGenerated();
    } catch {
      /* empty */
    } finally {
      setLoading(null);
    }
  };

  const handleBacktest = async (ticker: string) => {
    setLoading(`bt-${ticker}`);
    try {
      const result = await runBacktest(ticker);
      onBacktestResult(result);
    } catch {
      /* empty */
    } finally {
      setLoading(null);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Search className="h-5 w-5" />
          Watchlist
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex gap-2 flex-wrap">
          <Input
            placeholder="Ticker (e.g. BTC, AAPL)"
            value={newTicker}
            onChange={(e) => setNewTicker(e.target.value)}
            className="w-32"
          />
          <Input
            placeholder="Name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            className="w-40"
          />
          <select
            value={newType}
            onChange={(e) => setNewType(e.target.value as "crypto" | "stock")}
            className="px-3 py-2 rounded-md border border-input bg-background text-sm"
          >
            <option value="crypto">Crypto</option>
            <option value="stock">Stock</option>
          </select>
          <Button onClick={handleAdd} size="sm">
            <Plus className="h-4 w-4 mr-1" /> Add
          </Button>
        </div>

        <div className="space-y-2">
          {assets.map((asset) => (
            <div
              key={asset.id}
              className="flex items-center justify-between p-3 rounded-lg border bg-card"
            >
              <div className="flex items-center gap-2">
                <span className="font-semibold">{asset.ticker}</span>
                <span className="text-sm text-muted-foreground">{asset.name}</span>
                <Badge variant={asset.asset_type === "crypto" ? "default" : "secondary"}>
                  {asset.asset_type}
                </Badge>
              </div>
              <div className="flex gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleRefresh(asset.ticker)}
                  disabled={loading === asset.ticker}
                  title="Refresh data"
                >
                  <RefreshCw className={`h-4 w-4 ${loading === asset.ticker ? "animate-spin" : ""}`} />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleAnalyze(asset.ticker)}
                  disabled={loading === `analyze-${asset.ticker}`}
                  title="Analyze signals"
                >
                  <TrendingUp className="h-4 w-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleBacktest(asset.ticker)}
                  disabled={loading === `bt-${asset.ticker}`}
                  title="Run backtest"
                >
                  <BarChart3 className="h-4 w-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleRemove(asset.ticker)}
                  title="Remove"
                >
                  <Trash2 className="h-4 w-4 text-destructive" />
                </Button>
              </div>
            </div>
          ))}
          {assets.length === 0 && (
            <p className="text-sm text-muted-foreground text-center py-4">
              No assets in watchlist. Add one above.
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
