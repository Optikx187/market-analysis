import { useEffect, useState } from "react";
import {
  Bell,
  BookOpen,
  CandlestickChart,
  ChartNoAxesCombined,
  CircleDollarSign,
  Gauge,
  Keyboard,
  LineChart,
  ListOrdered,
  LogOut,
  Radar,
  ScanSearch,
  Settings,
  ShieldCheck,
  Sparkles,
  User,
  WandSparkles,
  type LucideIcon,
} from "lucide-react";
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

const TAB_ICONS: Record<Tab, LucideIcon> = {
  alerts: Bell,
  orders: ListOrdered,
  live: CircleDollarSign,
  trades: CandlestickChart,
  performance: ChartNoAxesCombined,
  scanner: ScanSearch,
  "price-alerts": Radar,
  chart: LineChart,
  settings: Settings,
  help: BookOpen,
};

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
      <div className="app-shell flex items-center justify-center">
        <div className="surface-card flex items-center gap-3 px-5 py-4 text-sm text-[var(--muted-foreground)]">
          <Gauge className="h-5 w-5 animate-pulse text-[var(--primary)]" aria-hidden="true" />
          Loading your market workspace…
        </div>
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
    <div className="app-shell">
      {tab === "live" ? (
        <div className="mode-banner mode-banner-live px-4 py-2 text-center">
          Live execution workspace &middot; broker orders are possible when armed &middot; real capital at risk
        </div>
      ) : (
        <div className="mode-banner mode-banner-paper px-4 py-2 text-center">
          Paper workspace &middot; simulated fills only &middot; no broker order submitted
        </div>
      )}
      <header className="app-header">
        <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3 sm:px-6 lg:flex-row lg:items-center lg:justify-between lg:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <div className="brand-mark" aria-hidden="true">
              <Sparkles className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <div className="section-kicker">Portfolio intelligence</div>
              <h1 className="truncate text-lg font-bold tracking-tight sm:text-xl">
                Market Analysis
              </h1>
              <p className="hidden text-xs text-[var(--muted-foreground)] sm:block">
                Research, risk controls, and execution oversight in one workspace
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 sm:gap-3">
            <button
              onClick={() => { setShowOnboarding(true); }}
              className="status-chip hover:border-[var(--primary)] hover:text-[var(--foreground)]"
              title="Re-run the Getting Started wizard"
            >
              <WandSparkles className="h-3.5 w-3.5" aria-hidden="true" />
              Setup Wizard
            </button>
            {authEnabled && (
              <div className="status-chip">
                <User className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{authUsername ?? "Authenticated user"}</span>
                <button
                  className="ml-1 rounded-full p-1 text-[var(--muted-foreground)] hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
                  onClick={logout}
                  type="button"
                  aria-label="Sign out"
                  title="Sign out"
                >
                  <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              </div>
            )}
            <div className="status-chip">
              <div className="status-dot" aria-hidden="true" />
              System Active
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1600px] space-y-6 overflow-x-hidden px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <div className="flex flex-col gap-2 border-b border-[var(--border-subtle)] pb-5 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="section-kicker">Daily command center</div>
            <h2 className="section-title mt-1 text-2xl font-bold sm:text-3xl">Portfolio overview</h2>
            <p className="mt-1 max-w-2xl text-sm text-[var(--muted-foreground)]">
              Prioritize decisions, monitor portfolio risk, and move from research to action with clear context.
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs text-[var(--muted-foreground)]">
            <ShieldCheck className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
            Capital-preservation controls enabled
          </div>
        </div>

        <ActionInbox onOpenContext={openContext} />

        <div className="flex flex-col gap-2 text-[11px] text-[var(--muted-foreground)] sm:flex-row sm:items-center sm:justify-between">
          <button
            onClick={() => setShowShortcuts((current) => !current)}
            aria-expanded={showShortcuts}
            aria-controls="keyboard-shortcuts"
            className="status-chip self-start"
          >
            <Keyboard className="h-3.5 w-3.5" aria-hidden="true" />
            Keyboard shortcuts
          </button>
          {showShortcuts && (
            <ul id="keyboard-shortcuts" className="surface-card flex flex-wrap gap-3 px-3 py-2">
              <li><kbd className="rounded border px-1">a</kbd> focus Action Required</li>
              <li><kbd className="rounded border px-1">d</kbd> jump to dashboard</li>
              <li><kbd className="rounded border px-1">[</kbd> / <kbd className="rounded border px-1">]</kbd> previous / next tab</li>
              <li><kbd className="rounded border px-1">?</kbd> toggle this list</li>
            </ul>
          )}
        </div>

        <DashboardWidget />

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
          <div>
            <WatchlistPanel onViewChart={(t) => { setSelectedChartTicker(t); setTab("chart"); }} />
          </div>
          <div className="xl:col-span-2">
            <PortfolioPanel />
          </div>
        </div>

        <section aria-labelledby="workspace-heading" className="space-y-4 pt-2">
          <div>
            <div className="section-kicker">Workspace</div>
            <h2 id="workspace-heading" className="section-title mt-1 text-xl font-bold">Analysis &amp; execution tools</h2>
          </div>
          <div className="workspace-nav scroll-fade flex gap-1 overflow-x-auto rounded-xl border border-[var(--border-subtle)] bg-[var(--background-elevated)] p-1.5">
            {(Object.keys(tabLabels) as Tab[]).map((t) => {
              const Icon = TAB_ICONS[t];
              return (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`nav-pill ${tab === t ? "nav-pill-active" : ""}`}
                  aria-current={tab === t ? "page" : undefined}
                >
                  <Icon className="h-4 w-4" aria-hidden="true" />
                  {tabLabels[t]}
                </button>
              );
            })}
          </div>

          <div className="min-w-0">
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
          </div>
        </section>
      </main>

      <footer className="mt-10 border-t border-[var(--border-subtle)] bg-[var(--background-elevated)]/60 py-5">
        <div className="mx-auto flex max-w-[1600px] flex-col items-center justify-between gap-2 px-4 text-xs text-[var(--muted-foreground)] sm:flex-row sm:px-6 lg:px-8">
          <span>Market Analysis v{APP_VERSION} &middot; Decision support, not financial advice</span>
          <span className="inline-flex items-center gap-2">
            <span className="status-dot" aria-hidden="true" />
            Capital preservation first &middot;{" "}
            <strong className={tab === "live" ? "text-red-400" : "text-amber-400"}>
              {tab === "live" ? "LIVE" : "PAPER"}
            </strong>
          </span>
        </div>
      </footer>
    </div>
  );
}

export default App;
