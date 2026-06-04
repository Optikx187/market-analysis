import { useState } from "react";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { Separator } from "@/components/ui/separator";
import WatchlistPanel from "@/components/WatchlistPanel";
import PortfolioPanel from "@/components/PortfolioPanel";
import SignalsPanel from "@/components/SignalsPanel";
import TradesPanel from "@/components/TradesPanel";
import BacktestPanel from "@/components/BacktestPanel";
import LogsPanel from "@/components/LogsPanel";
import SettingsPanel from "@/components/SettingsPanel";
import type { BacktestResult } from "@/lib/api";
import {
  BarChart3,
  LineChart,
  Bell,
  ArrowUpDown,
  Terminal,
  Settings,
} from "lucide-react";

function App() {
  const [signalRefreshKey, setSignalRefreshKey] = useState(0);
  const [backtestResult, setBacktestResult] = useState<BacktestResult | null>(null);

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b bg-card">
        <div className="container mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <BarChart3 className="h-7 w-7 text-primary" />
            <div>
              <h1 className="text-xl font-bold tracking-tight">
                Market Analysis Platform
              </h1>
              <p className="text-xs text-muted-foreground">
                Quantitative signals &middot; Paper trading &middot; Capital preservation
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <div className="h-2 w-2 rounded-full bg-green-500 animate-pulse" />
            System Active
          </div>
        </div>
      </header>

      <main className="container mx-auto px-4 py-6">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
          <div className="lg:col-span-1">
            <WatchlistPanel
              onSignalGenerated={() => setSignalRefreshKey((k) => k + 1)}
              onBacktestResult={(r) => setBacktestResult(r as BacktestResult)}
            />
          </div>
          <div className="lg:col-span-2">
            <PortfolioPanel />
          </div>
        </div>

        <Separator className="mb-6" />

        <Tabs defaultValue="signals" className="space-y-4">
          <TabsList className="grid grid-cols-5 w-full max-w-2xl">
            <TabsTrigger value="signals" className="flex items-center gap-1.5">
              <Bell className="h-3.5 w-3.5" /> Signals
            </TabsTrigger>
            <TabsTrigger value="trades" className="flex items-center gap-1.5">
              <ArrowUpDown className="h-3.5 w-3.5" /> Trades
            </TabsTrigger>
            <TabsTrigger value="backtest" className="flex items-center gap-1.5">
              <LineChart className="h-3.5 w-3.5" /> Backtest
            </TabsTrigger>
            <TabsTrigger value="logs" className="flex items-center gap-1.5">
              <Terminal className="h-3.5 w-3.5" /> Logs
            </TabsTrigger>
            <TabsTrigger value="settings" className="flex items-center gap-1.5">
              <Settings className="h-3.5 w-3.5" /> Settings
            </TabsTrigger>
          </TabsList>

          <TabsContent value="signals">
            <SignalsPanel refreshKey={signalRefreshKey} />
          </TabsContent>
          <TabsContent value="trades">
            <TradesPanel />
          </TabsContent>
          <TabsContent value="backtest">
            <BacktestPanel result={backtestResult} />
          </TabsContent>
          <TabsContent value="logs">
            <LogsPanel />
          </TabsContent>
          <TabsContent value="settings">
            <SettingsPanel />
          </TabsContent>
        </Tabs>
      </main>

      <footer className="border-t py-4 mt-8">
        <div className="container mx-auto px-4 text-center text-xs text-muted-foreground">
          Market Analysis Platform &middot; Capital Preservation First &middot; Paper Trading Only
        </div>
      </footer>
    </div>
  );
}

export default App;
