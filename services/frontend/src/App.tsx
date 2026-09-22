import { useEffect, useState } from "react";
import WatchlistPanel from "@/components/WatchlistPanel";
import PortfolioPanel from "@/components/PortfolioPanel";
import TradesPanel from "@/components/TradesPanel";
import AlertsPanel from "@/components/AlertsPanel";
import SettingsPanel from "@/components/SettingsPanel";
import HelpPanel from "@/components/HelpPanel";
import GettingStartedPanel from "@/components/GettingStartedPanel";
import DashboardWidget from "@/components/DashboardWidget";
import ScannerPanel from "@/components/ScannerPanel";
import PriceAlertsPanel from "@/components/PriceAlertsPanel";
import HistoricalChart from "@/components/HistoricalChart";
import AttributionPanel from "@/components/AttributionPanel";
import OrdersPanel from "@/components/OrdersPanel";
import LiveTradingPanel from "@/components/LiveTradingPanel";
import ActionInbox from "@/components/ActionInbox";
import AuthPanel from "@/components/AuthPanel";
import {
  clearAuthSession,
  fetchAuthSession,
  fetchAuthStatus,
  fetchOnboardingStatus,
  getStoredAuthToken,
  getStoredAuthUsername,
  type ActionItem,
  type AuthResponse,
} from "@/lib/api";
import { deepLinkFocus, deepLinkTab, type DeepLinkFocus } from "@/lib/deepLink";

const APP_VERSION = "3.0.0";

type Tab =
  | "alerts"
  | "orders"
  | "live"
  | "trades"
  | "performance"
  | "scanner"
  | "price-alerts"
  | "chart"
  | "settings"
  | "help";

const TAB_ORDER: Tab[] = [
  "alerts",
  "orders",
  "live",
  "trades",
  "performance",
  "scanner",
  "price-alerts",
  "chart",
  "settings",
  "help",
];

/** Shortcuts must never fire while the user is typing. */
const isTypingTarget = (target: EventTarget | null): boolean => {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
};

function App() {
  const [tab, setTab] = useState<Tab>("alerts");
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [selectedChartTicker, setSelectedChartTicker] = useState<string | null>(null);
  const [focus, setFocus] = useState<DeepLinkFocus | null>(null);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [authReady, setAuthReady] = useState(false);
  const [authEnabled, setAuthEnabled] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [authUsername, setAuthUsername] = useState<string | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);

  const openContext = (item: ActionItem) => {
    const nextTab = deepLinkTab(item.deep_link);
    setFocus(deepLinkFocus(item.deep_link, Date.now()));
    setTab(nextTab);
    if (item.ticker) setSelectedChartTicker(item.ticker);
  };

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (isTypingTarget(event.target)) return;
      if (event.key === "a") {
        window.dispatchEvent(new Event("focus-action-inbox"));
        event.preventDefault();
        return;
      }
      if (event.key === "d") {
        document.getElementById("dashboard-heading")?.scrollIntoView({ block: "start" });
        event.preventDefault();
        return;
      }
      if (event.key === "]" || event.key === "[") {
        const delta = event.key === "]" ? 1 : -1;
        setTab((current) => {
          const index = TAB_ORDER.indexOf(current);
          return TAB_ORDER[(index + delta + TAB_ORDER.length) % TAB_ORDER.length];
        });
        event.preventDefault();
        return;
      }
      if (event.key === "?") {
        setShowShortcuts((current) => !current);
        event.preventDefault();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  useEffect(() => {
    console.log(`%cMarket Analysis v${APP_VERSION}`, "font-weight:bold;font-size:14px;color:#22c55e");
    console.log("Quant signals · Half-Kelly sizing · Capital preservation");
  }, []);

  const loadApplication = async () => {
    const dismissed = localStorage.getItem("onboarding_complete");
    try {
      const status = await fetchOnboardingStatus();
      if (!dismissed && !status.has_credentials) setShowOnboarding(true);
    } catch {
      if (!dismissed) setShowOnboarding(true);
    } finally {
      setLoaded(true);
    }
  };

  useEffect(() => {
    const initialize = async () => {
      try {
        const status = await fetchAuthStatus();
        setAuthEnabled(status.auth_enabled);
        if (status.auth_enabled) {
          if (!getStoredAuthToken()) {
            setAuthReady(true);
            return;
          }
          try {
            await fetchAuthSession();
          } catch {
            clearAuthSession();
            setAuthReady(true);
            return;
          }
          setAuthUsername(getStoredAuthUsername());
        }
        setAuthenticated(true);
        setAuthReady(true);
        await loadApplication();
      } catch {
        setAuthError("Unable to determine the authentication mode. Check portfolio-engine connectivity.");
        setAuthReady(true);
      }
    };
    void initialize();
  }, []);

  const handleAuthenticated = (session: AuthResponse) => {
    setAuthUsername(session.username);
    setAuthenticated(true);
    setLoaded(false);
    void loadApplication();
  };

  const logout = () => {
    clearAuthSession();
    setAuthenticated(false);
    setAuthUsername(null);
    setLoaded(false);
    setShowOnboarding(false);
  };

  const completeOnboarding = () => {
    localStorage.setItem("onboarding_complete", "1");
    setShowOnboarding(false);
  };

  if (!authReady || (authenticated && !loaded)) {
    return (
      <div className="min-h-screen bg-[var(--background)] text-[var(--foreground)] flex items-center justify-center">
        <div className="text-sm text-[var(--muted-foreground)]">Loading...</div>
      </div>
    );
  }

  if (authError) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-[var(--background)] px-4 text-[var(--foreground)]">
        <div className="max-w-md rounded border border-red-500/50 bg-red-500/10 p-4 text-sm text-red-300" role="alert">
          {authError}
        </div>
      </main>
    );
  }

  if (authEnabled && !authenticated) {
    return <AuthPanel onAuthenticated={handleAuthenticated} />;
  }

  if (showOnboarding) {
    return <GettingStartedPanel onComplete={completeOnboarding} />;
  }

  const tabLabels: Record<Tab, string> = {
    alerts: "Alerts",
    orders: "Orders (Paper)",
    live: "Live Trading",
    trades: "Trades",
    performance: "Performance",
    scanner: "Scanner",
    "price-alerts": "Price Alerts",
    chart: "Chart",
    settings: "Settings",
    help: "Help & Docs",
  };

  return (
    <div className={`min-h-screen bg-[var(--background)] text-[var(--foreground)] border-t-4 ${tab === "live" ? "border-red-600" : "border-amber-500"}`}>
      {tab === "live" ? (
        <div className="bg-red-700 px-4 py-1 text-center text-xs font-bold uppercase tracking-wide text-white">
          Live trading controls &middot; real broker orders possible when armed &middot; real money at risk
        </div>
      ) : (
        <div className="bg-amber-500 px-4 py-1 text-center text-xs font-bold uppercase tracking-wide text-black">
          Paper trading mode &middot; simulated fills only &middot; no broker order is ever submitted
        </div>
      )}
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
            {authEnabled && (
              <div className="flex items-center gap-2 text-xs">
                <span className="text-[var(--muted-foreground)]">{authUsername ?? "Authenticated user"}</span>
                <button
                  className="rounded border border-[var(--border)] px-2 py-1 hover:bg-[var(--muted)]"
                  onClick={logout}
                  type="button"
                >
                  Sign out
                </button>
              </div>
            )}
            <div className="flex items-center gap-2 text-xs text-[var(--muted-foreground)]">
              <div className="h-2 w-2 rounded-full bg-green-500 animate-pulse" />
              System Active
            </div>
          </div>
        </div>
      </header>

      <main className="container mx-auto max-w-full overflow-x-hidden px-4 py-6 space-y-6">
        <ActionInbox onOpenContext={openContext} />

        <div className="flex flex-col gap-1 text-[10px] text-[var(--muted-foreground)] sm:flex-row sm:items-center sm:justify-between">
          <button
            onClick={() => setShowShortcuts((current) => !current)}
            aria-expanded={showShortcuts}
            aria-controls="keyboard-shortcuts"
            className="self-start rounded border px-2 py-0.5 focus-visible:ring-2 focus-visible:ring-blue-400"
          >
            Keyboard shortcuts (press ?)
          </button>
          {showShortcuts && (
            <ul id="keyboard-shortcuts" className="flex flex-wrap gap-3">
              <li><kbd className="rounded border px-1">a</kbd> focus Action Required</li>
              <li><kbd className="rounded border px-1">d</kbd> jump to dashboard</li>
              <li><kbd className="rounded border px-1">[</kbd> / <kbd className="rounded border px-1">]</kbd> previous / next tab</li>
              <li><kbd className="rounded border px-1">?</kbd> toggle this list</li>
            </ul>
          )}
        </div>

        <DashboardWidget />

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-1">
            <WatchlistPanel onViewChart={(t) => { setSelectedChartTicker(t); setTab("chart"); }} />
          </div>
          <div className="lg:col-span-2">
            <PortfolioPanel />
          </div>
        </div>

        <div className="flex gap-2 border-b border-[var(--border)] pb-2 overflow-x-auto">
          {(Object.keys(tabLabels) as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-1.5 text-sm rounded-t whitespace-nowrap ${
                tab === t ? "bg-[var(--primary)] text-[var(--primary-foreground)]" : "text-[var(--muted-foreground)]"
              }`}
            >
              {tabLabels[t]}
            </button>
          ))}
        </div>

        {tab === "alerts" && <AlertsPanel />}
        {tab === "orders" && <OrdersPanel focus={focus ?? undefined} />}
        {tab === "live" && <LiveTradingPanel />}
        {tab === "trades" && (
          <TradesPanel focus={focus ?? undefined} onClearFocus={() => setFocus(null)} />
        )}
        {tab === "performance" && <AttributionPanel />}
        {tab === "scanner" && <ScannerPanel focus={focus ?? undefined} />}
        {tab === "price-alerts" && <PriceAlertsPanel />}
        {tab === "chart" && <HistoricalChart ticker={selectedChartTicker} />}
        {tab === "settings" && <SettingsPanel focus={focus ?? undefined} />}
        {tab === "help" && <HelpPanel />}
      </main>

      <footer className="border-t border-[var(--border)] py-4 mt-8">
        <div className="container mx-auto px-4 text-center text-xs text-[var(--muted-foreground)]">
          Market Analysis v{APP_VERSION} &middot; Capital Preservation First &middot; Docker Orchestrated
          &middot; <span className="font-semibold text-amber-500">PAPER</span>
        </div>
      </footer>
    </div>
  );
}

export default App;
