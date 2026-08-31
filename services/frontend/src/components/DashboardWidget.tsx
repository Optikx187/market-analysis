import { useEffect, useMemo, useState } from "react";
import {
  apiErrorMessage,
  deleteDashboardLayout,
  fetchDashboardPreferences,
  fetchDashboardSummary,
  resetDashboardPreferences,
  saveDashboardLayout,
  saveDashboardPreferences,
  type DashboardMode,
  type DashboardPreferences,
  type DashboardSummary,
  type DashboardWidgetPreference,
} from "@/lib/api";

const WIDGET_LABELS: Record<string, string> = {
  pnl: "P&L / Equity",
  cash: "Cash",
  exposure: "Exposure",
  heat: "Portfolio Heat",
  drawdown: "Drawdown / Breaker",
  regime: "Market Regime",
  provider_health: "Provider Health",
  top_opportunities: "Top Opportunities",
};

const money = (value: number) => `$${Number(value ?? 0).toLocaleString()}`;

export default function DashboardWidget() {
  const [data, setData] = useState<DashboardSummary | null>(null);
  const [preferences, setPreferences] = useState<DashboardPreferences | null>(null);
  const [draft, setDraft] = useState<DashboardWidgetPreference[]>([]);
  const [mode, setMode] = useState<DashboardMode>("detailed");
  const [customizing, setCustomizing] = useState(false);
  const [layoutName, setLayoutName] = useState("");
  const [selectedLayout, setSelectedLayout] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = () => fetchDashboardSummary().then(setData).catch(() => {});
    load();
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, []);

  const applyPreferences = (next: DashboardPreferences) => {
    setPreferences(next);
    setDraft(next.widgets);
    setMode(next.mode);
  };

  useEffect(() => {
    fetchDashboardPreferences()
      .then(applyPreferences)
      .catch((err: unknown) => setError(apiErrorMessage(err, "Dashboard preferences unavailable; showing defaults.")));
  }, []);

  const visibleWidgets = useMemo(
    () => draft.filter((widget) => widget.enabled).map((widget) => widget.id),
    [draft],
  );

  const persist = async (widgets: DashboardWidgetPreference[], nextMode: DashboardMode) => {
    try {
      applyPreferences(await saveDashboardPreferences({ widgets, mode: nextMode }));
      setNotice("Dashboard layout saved.");
      setError(null);
    } catch (err) {
      setError(apiErrorMessage(err, "Failed to save dashboard layout."));
    }
  };

  const move = (index: number, delta: number) => {
    const target = index + delta;
    if (target < 0 || target >= draft.length) return;
    const next = [...draft];
    [next[index], next[target]] = [next[target], next[index]];
    setDraft(next);
    persist(next, mode);
  };

  const toggle = (index: number) => {
    const next = draft.map((widget, position) =>
      position === index ? { ...widget, enabled: !widget.enabled } : widget,
    );
    setDraft(next);
    persist(next, mode);
  };

  const changeMode = (nextMode: DashboardMode) => {
    setMode(nextMode);
    persist(draft, nextMode);
  };

  const saveNamed = async () => {
    const name = layoutName.trim();
    if (!name) {
      setError("Enter a layout name before saving.");
      return;
    }
    try {
      applyPreferences(await saveDashboardLayout(name, { widgets: draft, mode }));
      setLayoutName("");
      setNotice(`Saved layout "${name}".`);
      setError(null);
    } catch (err) {
      setError(apiErrorMessage(err, "Failed to save the named layout."));
    }
  };

  const applyNamed = () => {
    const layout = preferences?.layouts[selectedLayout];
    if (!layout) return;
    setDraft(layout.widgets);
    setMode(layout.mode);
    persist(layout.widgets, layout.mode);
    setNotice(`Applied layout "${selectedLayout}".`);
  };

  const deleteNamed = async () => {
    if (!selectedLayout) return;
    try {
      applyPreferences(await deleteDashboardLayout(selectedLayout));
      setNotice(`Deleted layout "${selectedLayout}".`);
      setSelectedLayout("");
      setError(null);
    } catch (err) {
      setError(apiErrorMessage(err, "Failed to delete the named layout."));
    }
  };

  const resetDefaults = async () => {
    try {
      applyPreferences(await resetDashboardPreferences());
      setNotice("Dashboard reset to defaults.");
      setError(null);
    } catch (err) {
      setError(apiErrorMessage(err, "Failed to reset the dashboard."));
    }
  };

  const detailed = mode === "detailed";
  const layoutNames = Object.keys(preferences?.layouts ?? {});

  return (
    <section aria-labelledby="dashboard-heading" className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <h2 id="dashboard-heading" className="text-sm font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
          Dashboard ({mode})
        </h2>
        <button
          onClick={() => setCustomizing((current) => !current)}
          aria-expanded={customizing}
          aria-controls="dashboard-customize"
          className="self-start rounded bg-[var(--secondary)] px-3 py-1 text-xs hover:bg-[var(--accent)] focus-visible:ring-2 focus-visible:ring-blue-400"
        >
          {customizing ? "Close customize" : "Customize dashboard"}
        </button>
      </div>

      <div aria-live="polite" role="status" className="text-[10px] text-[var(--muted-foreground)]">
        {notice}
      </div>
      {error && (
        <div className="rounded border border-red-600 bg-red-600/10 p-2 text-xs text-red-300">{error}</div>
      )}

      {customizing && (
        <div id="dashboard-customize" className="rounded-lg border bg-[var(--card)] p-3 space-y-3">
          <fieldset>
            <legend className="text-xs font-medium">Widgets and order</legend>
            <ul className="mt-2 space-y-1">
              {draft.map((widget, index) => (
                <li key={widget.id} className="flex flex-wrap items-center justify-between gap-2 rounded border px-2 py-1">
                  <label className="flex items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      checked={widget.enabled}
                      onChange={() => toggle(index)}
                      aria-label={`Show ${WIDGET_LABELS[widget.id] ?? widget.id} widget`}
                    />
                    <span>{index + 1}. {WIDGET_LABELS[widget.id] ?? widget.id}</span>
                  </label>
                  <span className="flex gap-1">
                    <button
                      onClick={() => move(index, -1)}
                      disabled={index === 0}
                      aria-label={`Move ${WIDGET_LABELS[widget.id] ?? widget.id} up`}
                      className="rounded bg-[var(--secondary)] px-2 py-0.5 text-[10px] hover:bg-[var(--accent)] focus-visible:ring-2 focus-visible:ring-blue-400 disabled:opacity-40"
                    >
                      ↑ Up
                    </button>
                    <button
                      onClick={() => move(index, 1)}
                      disabled={index === draft.length - 1}
                      aria-label={`Move ${WIDGET_LABELS[widget.id] ?? widget.id} down`}
                      className="rounded bg-[var(--secondary)] px-2 py-0.5 text-[10px] hover:bg-[var(--accent)] focus-visible:ring-2 focus-visible:ring-blue-400 disabled:opacity-40"
                    >
                      ↓ Down
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          </fieldset>

          <fieldset className="flex flex-wrap items-center gap-3">
            <legend className="text-xs font-medium">Density</legend>
            {(["compact", "detailed"] as DashboardMode[]).map((value) => (
              <label key={value} className="flex items-center gap-1 text-xs">
                <input
                  type="radio"
                  name="dashboard-mode"
                  value={value}
                  checked={mode === value}
                  onChange={() => changeMode(value)}
                />
                {value}
              </label>
            ))}
          </fieldset>

          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <label className="text-[10px] text-[var(--muted-foreground)]">
              Save current as layout
              <input
                value={layoutName}
                onChange={(event) => setLayoutName(event.target.value)}
                placeholder="e.g. Mobile monitoring"
                className="mt-1 w-full rounded border bg-[var(--input)] px-2 py-1 text-xs"
              />
            </label>
            <button
              onClick={saveNamed}
              className="rounded bg-[var(--primary)] px-3 py-1 text-xs text-[var(--primary-foreground)] focus-visible:ring-2 focus-visible:ring-blue-400"
            >
              Save layout
            </button>
            <label className="text-[10px] text-[var(--muted-foreground)]">
              Saved layouts
              <select
                value={selectedLayout}
                onChange={(event) => setSelectedLayout(event.target.value)}
                className="mt-1 w-full rounded border bg-[var(--input)] px-2 py-1 text-xs"
              >
                <option value="">Select a layout</option>
                {layoutNames.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </label>
            <button
              onClick={applyNamed}
              disabled={!selectedLayout}
              className="rounded bg-[var(--secondary)] px-3 py-1 text-xs hover:bg-[var(--accent)] focus-visible:ring-2 focus-visible:ring-blue-400 disabled:opacity-40"
            >
              Apply
            </button>
            <button
              onClick={deleteNamed}
              disabled={!selectedLayout}
              className="rounded bg-[var(--secondary)] px-3 py-1 text-xs hover:bg-[var(--accent)] focus-visible:ring-2 focus-visible:ring-blue-400 disabled:opacity-40"
            >
              Delete
            </button>
            <button
              onClick={resetDefaults}
              className="rounded bg-[var(--secondary)] px-3 py-1 text-xs hover:bg-[var(--accent)] focus-visible:ring-2 focus-visible:ring-blue-400"
            >
              Reset defaults
            </button>
          </div>
        </div>
      )}

      {!data ? (
        <p className="text-xs text-[var(--muted-foreground)]">Dashboard metrics unavailable right now.</p>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {visibleWidgets.map((id) => (
              <WidgetCard key={id} id={id} data={data} detailed={detailed} />
            ))}
          </div>
          {detailed && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-[10px]">
              {data.risk.stress_tests.map((scenario) => (
                <div key={scenario.name} className="flex items-center justify-between rounded border border-[var(--border)] bg-[var(--card)] px-2 py-1">
                  <span className="text-[var(--muted-foreground)]">{scenario.name}</span>
                  <span className={scenario.estimated_pnl >= 0 ? "text-green-400" : "text-red-400"}>
                    {scenario.estimated_pnl >= 0 ? "+" : ""}{money(scenario.estimated_pnl)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function WidgetCard({ id, data, detailed }: { id: string; data: DashboardSummary; detailed: boolean }) {
  const label = WIDGET_LABELS[id] ?? id;

  if (id === "pnl") {
    const positive = data.total_pnl >= 0;
    const sign = positive ? "+" : "";
    return (
      <DashboardCard
        label={label}
        value={`${sign}${money(data.total_pnl)} (${sign}${data.total_pnl_pct}%)`}
        detail={detailed ? `Equity ${money(data.equity)} · ${data.open_positions} open · ${data.todays_approved}/${data.todays_signals} signals` : undefined}
        color={positive ? "text-green-400" : "text-red-400"}
        marker={positive ? "▲" : "▼"}
      />
    );
  }

  if (id === "cash") {
    const cash = data.cash;
    if (!cash?.available) return <UnavailableCard label={label} reason="Cash inputs unavailable" />;
    return (
      <DashboardCard
        label={label}
        value={money(cash.free)}
        detail={detailed ? `${money(cash.balance)} balance · ${money(cash.reserved)} reserved` : undefined}
      />
    );
  }

  if (id === "exposure") {
    const concentration = data.risk.exposure.largest_concentration;
    return (
      <DashboardCard
        label={label}
        value={`${concentration.pct}%`}
        detail={detailed ? `${concentration.category}: ${concentration.name}` : undefined}
        color="text-yellow-400"
        marker="◆"
      />
    );
  }

  if (id === "heat") {
    const heat = data.risk.heat;
    const hot = heat.utilization_pct >= 80;
    return (
      <DashboardCard
        label={label}
        value={`${heat.effective_pct}%`}
        detail={detailed ? `${heat.utilization_pct}% of ${heat.limit_pct}% limit · ${money(heat.effective_risk_usd)} risk` : `${heat.utilization_pct}% used`}
        color={hot ? "text-red-400" : "text-orange-400"}
        marker={hot ? "▲" : "◆"}
      />
    );
  }

  if (id === "drawdown") {
    const breaker = data.risk.breaker;
    return (
      <DashboardCard
        label={label}
        value={breaker.active ? "New Risk Blocked" : "Clear"}
        detail={
          breaker.active
            ? breaker.reasons[0] || "Limit breached"
            : `${breaker.current_drawdown_pct}% drawdown`
        }
        color={breaker.active ? "text-red-400" : "text-green-400"}
        marker={breaker.active ? "▲" : "●"}
      />
    );
  }

  if (id === "regime") {
    const regime = data.regime;
    if (!regime?.available) {
      return <UnavailableCard label={label} reason={regime?.reason || "No completed scan yet"} />;
    }
    return (
      <DashboardCard
        label={label}
        value={regime.label || "—"}
        detail={detailed ? `Trend ${regime.trend} · vol ${regime.volatility} · breadth ${regime.breadth}` : undefined}
      />
    );
  }

  if (id === "provider_health") {
    const health = data.provider_health;
    if (!health?.available) {
      return <UnavailableCard label={label} reason={health?.reason || "Provider status unavailable"} />;
    }
    const quality = health.data_quality;
    const connectivity = Object.entries(health.connectivity ?? {});
    const down = connectivity.filter(([, value]) => value.status !== "ok").map(([name]) => name);
    return (
      <DashboardCard
        label={label}
        value={down.length === 0 ? "All providers OK" : `${down.length} degraded`}
        detail={
          detailed
            ? `${quality ? `${quality.blocked} blocked · ${quality.warnings} warnings · ${quality.healthy} healthy` : "No data-quality report"}${down.length ? ` · ${down.join(", ")}` : ""}`
            : down.join(", ") || undefined
        }
        color={down.length === 0 ? "text-green-400" : "text-red-400"}
        marker={down.length === 0 ? "●" : "▲"}
      />
    );
  }

  if (id === "top_opportunities") {
    const top = data.top_opportunities;
    if (!top?.available) {
      return <UnavailableCard label={label} reason={top?.reason || "Scanner results unavailable"} />;
    }
    if (top.items.length === 0) {
      return <UnavailableCard label={label} reason="No eligible opportunities" />;
    }
    return (
      <div className="rounded-lg border bg-[var(--card)] p-3">
        <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">{label}</div>
        <ul className="mt-1 space-y-0.5 text-xs">
          {top.items.slice(0, detailed ? 5 : 3).map((item) => (
            <li key={item.id} className="flex items-center justify-between gap-2">
              <span className="font-medium">{item.ticker} {item.direction}</span>
              <span className="text-[var(--muted-foreground)]">{item.score.toFixed(1)}</span>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return null;
}

function UnavailableCard({ label, reason }: { label: string; reason: string }) {
  return (
    <div className="rounded-lg border border-dashed bg-[var(--card)] p-3">
      <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">{label}</div>
      <div className="text-sm font-semibold text-[var(--muted-foreground)]">
        <span aria-hidden="true">◻ </span>Unavailable
      </div>
      <div className="text-[10px] text-[var(--muted-foreground)]">{reason}</div>
    </div>
  );
}

function DashboardCard({
  label,
  value,
  detail,
  color = "",
  marker,
}: {
  label: string;
  value: string;
  detail?: string;
  color?: string;
  marker?: string;
}) {
  return (
    <div className="rounded-lg border bg-[var(--card)] p-3">
      <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">{label}</div>
      <div className={`text-lg font-bold break-words ${color}`}>
        {marker && <span aria-hidden="true">{marker} </span>}
        {value}
      </div>
      {detail && <div className="text-[10px] text-[var(--muted-foreground)] break-words">{detail}</div>}
    </div>
  );
}
