import { useEffect, useState } from "react";
import WatchlistPanel from "@/components/WatchlistPanel";
import PortfolioPanel from "@/components/PortfolioPanel";
import TradesPanel from "@/components/TradesPanel";
import AlertsPanel from "@/components/AlertsPanel";
import SettingsPanel from "@/components/SettingsPanel";
import HelpPanel from "@/components/HelpPanel";
import GettingStartedPanel from "@/components/GettingStartedPanel";
import { fetchOnboardingStatus } from "@/lib/api";

const APP_VERSION = "2.2.0";

type Tab = "alerts" | "trades" | "settings" | "help";

function App() {
  const [tab, setTab] = useState<Tab>("alerts");
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    console.log(`%cMarket Analysis v${APP_VERSION}`, "font-weight:bold;font-size:14px;color:#22c55e");
    console.log("Quant signals · Half-Kelly sizing · Capital preservation");
  }, []);

  useEffect(() => {
    const dismissed = localStorage.getItem("onboarding_complete");
    fetchOnboardingStatus()
      .then((s) => {
        // Skip onboarding if: (a) localStorage flag OR (b) market data APIs configured
        if (!dismissed && !s.has_credentials) {
          setShowOnboarding(true);
        }
        setLoaded(true);
      })
      .catch(() => {
        // On error, only show onboarding if not dismissed
        if (!dismissed) setShowOnboarding(true);
        setLoaded(true);
      });
  }, []);

  const completeOnboarding = () => {
    localStorage.setItem("onboarding_complete", "1");
    setShowOnboarding(false);
  };

  if (!loaded) {
    return (
      <div className="min-h-screen bg-[var(--background)] text-[var(--foreground)] flex items-center justify-center">
        <div className="text-sm text-[var(--muted-foreground)]">Loading...</div>
      </div>
    );
  }

  if (showOnboarding) {
    return <GettingStartedPanel onComplete={completeOnboarding} />;
  }

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
          <div className="flex items-center gap-4">
            <button
              onClick={() => { setShowOnboarding(true); }}
              className="text-xs text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
              title="Re-run the Getting Started wizard"
            >
              Setup Wizard
            </button>
            <div className="flex items-center gap-2 text-xs text-[var(--muted-foreground)]">
              <div className="h-2 w-2 rounded-full bg-green-500 animate-pulse" />
              System Active
            </div>
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
          {(["alerts", "trades", "settings", "help"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-1.5 text-sm rounded-t capitalize ${
                tab === t ? "bg-[var(--primary)] text-[var(--primary-foreground)]" : "text-[var(--muted-foreground)]"
              }`}
            >
              {t === "help" ? "Help & Docs" : t}
            </button>
          ))}
        </div>

        {tab === "alerts" && <AlertsPanel />}
        {tab === "trades" && <TradesPanel />}
        {tab === "settings" && <SettingsPanel />}
        {tab === "help" && <HelpPanel />}
      </main>

      <footer className="border-t border-[var(--border)] py-4 mt-8">
        <div className="container mx-auto px-4 text-center text-xs text-[var(--muted-foreground)]">
          Market Analysis v{APP_VERSION} &middot; Capital Preservation First &middot; Docker Orchestrated
        </div>
      </footer>
    </div>
  );
}

export default App;
