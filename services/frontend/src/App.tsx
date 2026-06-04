import { useState } from "react";
import WatchlistPanel from "@/components/WatchlistPanel";
import PortfolioPanel from "@/components/PortfolioPanel";
import TradesPanel from "@/components/TradesPanel";
import AlertsPanel from "@/components/AlertsPanel";
import SettingsPanel from "@/components/SettingsPanel";

type Tab = "alerts" | "trades" | "settings";

function App() {
  const [tab, setTab] = useState<Tab>("alerts");

  return (
    <div className="min-h-screen bg-[var(--background)] text-[var(--foreground)]">
      <header className="border-b border-[var(--border)] bg-[var(--card)]">
        <div className="container mx-auto px-4 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold tracking-tight">
              Market Analysis — Microservices
            </h1>
            <p className="text-xs text-[var(--muted-foreground)]">
              Quant signals &middot; Half-Kelly sizing &middot; Capital preservation
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs text-[var(--muted-foreground)]">
            <div className="h-2 w-2 rounded-full bg-green-500 animate-pulse" />
            System Active
          </div>
        </div>
      </header>

      <main className="container mx-auto px-4 py-6 space-y-6">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-1">
            <WatchlistPanel />
          </div>
          <div className="lg:col-span-2">
            <PortfolioPanel />
          </div>
        </div>

        <div className="flex gap-2 border-b border-[var(--border)] pb-2">
          {(["alerts", "trades", "settings"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-1.5 text-sm rounded-t capitalize ${
                tab === t ? "bg-[var(--primary)] text-[var(--primary-foreground)]" : "text-[var(--muted-foreground)]"
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        {tab === "alerts" && <AlertsPanel />}
        {tab === "trades" && <TradesPanel />}
        {tab === "settings" && <SettingsPanel />}
      </main>

      <footer className="border-t border-[var(--border)] py-4 mt-8">
        <div className="container mx-auto px-4 text-center text-xs text-[var(--muted-foreground)]">
          Market Analysis Microservices v2.0 &middot; Capital Preservation First &middot; Docker Orchestrated
        </div>
      </footer>
    </div>
  );
}

export default App;
