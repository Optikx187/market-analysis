import { useEffect, useMemo, useState } from "react";
import {
  fetchScannerStatus,
  triggerScan,
  updateOpportunityAction,
  updateScannerConfig,
  type Opportunity,
  type OpportunityActionPayload,
  type ScannerStatus,
  type ScanResult,
} from "@/lib/api";
import type { DeepLinkFocus } from "@/lib/deepLink";

interface SavedView {
  name: string;
  minimumScore: number;
  eligibility: "all" | "eligible" | "ineligible";
  direction: "all" | "BUY" | "SELL";
}

interface EditValues {
  entryLow: string;
  entryHigh: string;
  stopLoss: string;
  targets: string;
  quantity: string;
  timeStop: string;
}

const SAVED_VIEWS_KEY = "scanner-opportunity-views";

const money = (value: number) => value.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const price = (value: number) => value.toLocaleString(undefined, { maximumFractionDigits: 6 });

export default function ScannerPanel({ focus }: { focus?: DeepLinkFocus }) {
  const [status, setStatus] = useState<ScannerStatus | null>(null);
  const [tickerFilter, setTickerFilter] = useState("");
  const [scanning, setScanning] = useState(false);
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [msg, setMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [minimumScore, setMinimumScore] = useState(0);
  const [eligibility, setEligibility] = useState<"all" | "eligible" | "ineligible">("all");
  const [direction, setDirection] = useState<"all" | "BUY" | "SELL">("all");
  const [savedViews, setSavedViews] = useState<SavedView[]>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValues, setEditValues] = useState<EditValues | null>(null);

  const load = () => fetchScannerStatus().then((next) => {
    setStatus(next);
    if (next.last_scan_result) setScanResult(next.last_scan_result);
  }).catch(() => {});

  useEffect(() => {
    load();
    try {
      const stored = JSON.parse(localStorage.getItem(SAVED_VIEWS_KEY) || "[]") as SavedView[];
      if (Array.isArray(stored)) setSavedViews(stored);
    } catch {
      localStorage.removeItem(SAVED_VIEWS_KEY);
    }
    const interval = setInterval(load, 15000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!focus) return;
    setTickerFilter(focus.ticker ?? "");
    setMinimumScore(0);
    setEligibility("all");
    setDirection("all");
    if (focus.opportunityId) setExpandedId(focus.opportunityId);
  }, [focus]);

  const filteredSignals = useMemo(() => {
    if (!scanResult) return [];
    const ticker = tickerFilter.trim().toUpperCase();
    return scanResult.signals
      .filter((signal) => !ticker || signal.ticker.toUpperCase().includes(ticker))
      .filter((signal) => signal.score >= minimumScore)
      .filter((signal) => eligibility === "all" || (eligibility === "eligible" ? signal.eligible : !signal.eligible))
      .filter((signal) => direction === "all" || signal.direction === direction)
      .sort((first, second) => second.score - first.score || first.ticker.localeCompare(second.ticker));
  }, [scanResult, minimumScore, eligibility, direction, tickerFilter]);

  const handleScanNow = async () => {
    setScanning(true);
    setMsg(null);
    try {
      const result = await triggerScan();
      setScanResult(result);
      setMsg({
        type: "success",
        text: `Scan complete: ${result.scanned} scanned, ${result.signals_found} signals, ${result.notifications_sent} notifications`,
      });
      load();
    } catch {
      setMsg({ type: "error", text: "Scan failed. Check that services are running." });
    }
    setScanning(false);
  };

  const handleToggle = async () => {
    if (!status) return;
    try {
      await updateScannerConfig({ enabled: !status.enabled });
      load();
    } catch {
      setMsg({ type: "error", text: "Failed to update scanner config" });
    }
  };

  const handleIntervalChange = async (intervalMinutes: number) => {
    try {
      await updateScannerConfig({ interval_minutes: intervalMinutes });
      load();
    } catch {
      setMsg({ type: "error", text: "Failed to update interval" });
    }
  };

  const handleMarketHoursToggle = async () => {
    if (!status) return;
    try {
      await updateScannerConfig({ market_hours_only: !status.market_hours_only });
      load();
    } catch {
      setMsg({ type: "error", text: "Failed to update market hours setting" });
    }
  };

  const replaceOpportunity = (opportunity: Opportunity) => {
    setScanResult((current) => current ? {
      ...current,
      signals: current.signals.map((signal) => signal.opportunity.id === opportunity.id ? {
        ...signal,
        recommended_size_usd: opportunity.trade_plan?.position_size_usd ?? 0,
        opportunity,
      } : signal),
    } : current);
  };

  const handleAction = async (opportunity: Opportunity, payload: OpportunityActionPayload) => {
    setActionBusy(`${opportunity.id}:${payload.action}`);
    setMsg(null);
    try {
      const updated = await updateOpportunityAction(opportunity.id, payload);
      replaceOpportunity(updated);
      setEditingId(null);
      setEditValues(null);
      setMsg({ type: "success", text: `${opportunity.ticker} marked ${updated.user_decision}` });
    } catch {
      setMsg({ type: "error", text: `Could not ${payload.action} ${opportunity.ticker}` });
    }
    setActionBusy(null);
  };

  const beginEdit = (opportunity: Opportunity) => {
    if (!opportunity.trade_plan) return;
    setEditingId(opportunity.id);
    setEditValues({
      entryLow: String(opportunity.trade_plan.entry_zone.low),
      entryHigh: String(opportunity.trade_plan.entry_zone.high),
      stopLoss: String(opportunity.trade_plan.stop_loss),
      targets: opportunity.trade_plan.targets.map((target) => target.price).join(", "),
      quantity: String(opportunity.trade_plan.quantity),
      timeStop: opportunity.trade_plan.time_stop,
    });
  };

  const saveEdit = (opportunity: Opportunity) => {
    if (!editValues) return;
    const targets = editValues.targets.split(",").map((value) => Number(value.trim())).filter((value) => Number.isFinite(value) && value > 0);
    handleAction(opportunity, {
      action: "edit",
      edit: {
        entry_zone_low: Number(editValues.entryLow),
        entry_zone_high: Number(editValues.entryHigh),
        stop_loss: Number(editValues.stopLoss),
        targets,
        quantity: Number(editValues.quantity),
        time_stop: editValues.timeStop,
      },
    });
  };

  const saveView = () => {
    const name = window.prompt("Saved view name");
    if (!name?.trim()) return;
    const next = [...savedViews.filter((view) => view.name !== name.trim()), {
      name: name.trim(), minimumScore, eligibility, direction,
    }];
    setSavedViews(next);
    localStorage.setItem(SAVED_VIEWS_KEY, JSON.stringify(next));
  };

  const applyView = (name: string) => {
    const view = savedViews.find((candidate) => candidate.name === name);
    if (!view) return;
    setMinimumScore(view.minimumScore);
    setEligibility(view.eligibility);
    setDirection(view.direction);
  };

  return (
    <div className="rounded-lg border bg-[var(--card)] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
        <div>
          <h3 className="text-sm font-semibold">Ranked Opportunity Scanner</h3>
          <p className="text-xs text-[var(--muted-foreground)]">Transparent scores, risk-controlled sizing, and complete trade plans</p>
        </div>
        <button onClick={handleScanNow} disabled={scanning} className="rounded bg-[var(--primary)] text-[var(--primary-foreground)] px-3 py-1.5 text-xs font-medium disabled:opacity-50">
          {scanning ? "Scanning..." : "Scan Now"}
        </button>
      </div>

      {msg && <div className={`mb-3 rounded border p-2 text-xs ${msg.type === "success" ? "border-green-600 bg-green-600/10 text-green-400" : "border-red-600 bg-red-600/10 text-red-400"}`}>{msg.text}</div>}

      {status && (
        <div className="grid gap-2 md:grid-cols-3 mb-3">
          <div className="flex items-center justify-between rounded bg-[var(--background)] p-2">
            <span className="text-xs">Auto-Scan</span>
            <button onClick={handleToggle} className={`px-2 py-1 text-xs rounded ${status.enabled ? "bg-green-600 text-white" : "bg-[var(--secondary)] text-[var(--muted-foreground)]"}`}>{status.enabled ? "Enabled" : "Disabled"}</button>
          </div>
          <div className="flex items-center justify-between rounded bg-[var(--background)] p-2">
            <span className="text-xs">Interval</span>
            <div className="flex gap-1">{[5, 15, 30, 60].map((minutes) => <button key={minutes} onClick={() => handleIntervalChange(minutes)} className={`px-1.5 py-0.5 text-[10px] rounded ${status.interval_minutes === minutes ? "bg-[var(--primary)] text-[var(--primary-foreground)]" : "bg-[var(--secondary)]"}`}>{minutes}m</button>)}</div>
          </div>
          <div className="flex items-center justify-between rounded bg-[var(--background)] p-2">
            <span className="text-xs">Market Hours</span>
            <button onClick={handleMarketHoursToggle} className={`px-2 py-1 text-xs rounded ${status.market_hours_only ? "bg-green-600 text-white" : "bg-[var(--secondary)]"}`}>{status.market_hours_only ? "Only" : "24/7"}</button>
          </div>
        </div>
      )}

      <div className="grid gap-2 rounded border border-[var(--border)] bg-[var(--background)] p-3 sm:grid-cols-2 lg:grid-cols-5">
        <label className="text-[10px] text-[var(--muted-foreground)]">Ticker
          <input value={tickerFilter} onChange={(event) => setTickerFilter(event.target.value)} placeholder="All tickers" className="mt-1 w-full rounded border bg-[var(--card)] px-2 py-1 text-xs" />
        </label>
        <label className="text-[10px] text-[var(--muted-foreground)]">Minimum score
          <input type="number" min="0" max="100" value={minimumScore} onChange={(event) => setMinimumScore(Number(event.target.value))} className="mt-1 w-full rounded border bg-[var(--card)] px-2 py-1 text-xs" />
        </label>
        <label className="text-[10px] text-[var(--muted-foreground)]">Eligibility
          <select value={eligibility} onChange={(event) => setEligibility(event.target.value as typeof eligibility)} className="mt-1 w-full rounded border bg-[var(--card)] px-2 py-1 text-xs"><option value="all">All</option><option value="eligible">Eligible</option><option value="ineligible">Ineligible</option></select>
        </label>
        <label className="text-[10px] text-[var(--muted-foreground)]">Direction
          <select value={direction} onChange={(event) => setDirection(event.target.value as typeof direction)} className="mt-1 w-full rounded border bg-[var(--card)] px-2 py-1 text-xs"><option value="all">All</option><option value="BUY">Buy</option><option value="SELL">Sell</option></select>
        </label>
        <div className="flex items-end gap-1">
          <select defaultValue="" onChange={(event) => applyView(event.target.value)} className="min-w-0 flex-1 rounded border bg-[var(--card)] px-2 py-1 text-xs"><option value="">Saved views</option>{savedViews.map((view) => <option key={view.name} value={view.name}>{view.name}</option>)}</select>
          <button onClick={saveView} className="rounded bg-[var(--secondary)] px-2 py-1 text-xs">Save</button>
        </div>
      </div>

      {scanResult && (
        <div className="mt-3 flex flex-wrap gap-3 text-[10px] text-[var(--muted-foreground)]">
          <span>{scanResult.scanned} scanned</span><span>{scanResult.signals_found} signals</span><span>{scanResult.quality_rejected} data blocked</span><span>{filteredSignals.length} shown</span>
        </div>
      )}

      <div className="mt-3 space-y-2">
        {filteredSignals.map((signal) => {
          const opportunity = signal.opportunity;
          const plan = opportunity.trade_plan;
          const expanded = expandedId === opportunity.id;
          const editing = editingId === opportunity.id && editValues;
          return (
            <div key={opportunity.id} className={`rounded border p-3 bg-[var(--background)] ${opportunity.eligible ? "border-green-600/40" : "border-amber-600/40"}`}>
              <button onClick={() => setExpandedId(expanded ? null : opportunity.id)} className="w-full text-left">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-lg font-semibold tabular-nums">{opportunity.score.toFixed(1)}</span>
                    <div><div className="text-sm font-medium">{signal.ticker} <span className={signal.direction === "BUY" ? "text-green-400" : signal.direction === "SELL" ? "text-red-400" : "text-amber-400"}>{signal.direction}</span></div><div className="text-[10px] text-[var(--muted-foreground)]">{opportunity.status} · {opportunity.regime?.trend ?? "unknown"} regime · {opportunity.timeframe_agreement?.available ? `${opportunity.timeframe_agreement.score.toFixed(0)}% TF` : "TF incomplete"} · {opportunity.user_decision}</div></div>
                  </div>
                  <div className="text-right"><div className={opportunity.eligible ? "text-green-400 text-xs" : "text-amber-400 text-xs"}>{opportunity.eligible ? "Eligible" : "Ineligible"}</div><div className="text-[10px] text-[var(--muted-foreground)]">{expanded ? "Hide details" : "Explain score and plan"}</div></div>
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded bg-[var(--secondary)]"><div className={`h-full ${opportunity.eligible ? "bg-green-500" : "bg-amber-500"}`} style={{ width: `${Math.min(100, opportunity.score)}%` }} /></div>
                <div className="mt-2 text-xs text-[var(--muted-foreground)]">{signal.reason}</div>
              </button>

              {expanded && (
                <div className="mt-3 space-y-3 border-t border-[var(--border)] pt-3">
                  <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">{opportunity.components.map((component) => <div key={component.name} className="rounded bg-[var(--card)] p-2"><div className="flex justify-between text-[10px]"><span>{component.label}</span><span className={component.available ? "" : "text-amber-400"}>{component.available ? component.score.toFixed(0) : "Missing"}</span></div><div className="mt-1 text-[10px] text-[var(--muted-foreground)]">{component.explanation}</div><div className="mt-1 text-[9px] text-[var(--muted-foreground)]">Weight {component.weight_pct}% · Contribution {component.contribution.toFixed(1)}</div></div>)}</div>

                  {opportunity.regime && <div className="rounded border border-[var(--border)] bg-[var(--card)] p-2 text-xs"><div className="font-medium">Market regime and timeframe agreement</div><div className="mt-1 text-[var(--muted-foreground)]">{opportunity.regime.label} · {opportunity.regime.session_profile}</div><div className="mt-1 flex flex-wrap gap-3">{opportunity.timeframe_agreement && Object.entries(opportunity.timeframe_agreement.details).map(([timeframe, detail]) => <span key={timeframe}>{timeframe}: {detail.trend}{detail.agrees ? " (agrees)" : ""}</span>)}</div><div className="mt-1">Fit {opportunity.regime_controls?.fit_score.toFixed(0) ?? 0}/100 · Size {opportunity.regime_controls?.size_multiplier.toFixed(2) ?? "0.00"}x</div></div>}

                  {opportunity.eligibility_reasons.length > 0 && <div className="rounded border border-amber-600/40 bg-amber-600/10 p-2 text-xs text-amber-300"><div className="font-medium">Eligibility blockers</div>{opportunity.eligibility_reasons.map((reason) => <div key={reason}>• {reason}</div>)}</div>}
                  {opportunity.event_warnings.length > 0 && <div className="rounded border border-amber-600/30 p-2 text-xs"><div className="font-medium">Event warnings</div>{opportunity.event_warnings.map((warning) => <div key={warning} className="text-[var(--muted-foreground)]">• {warning}</div>)}</div>}

                  {plan && !editing && (
                    <div className="rounded border border-[var(--border)] bg-[var(--card)] p-3 text-xs">
                      <div className="mb-2 font-medium">Risk-Controlled Trade Plan</div>
                      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                        <div><span className="text-[var(--muted-foreground)]">Entry zone</span><div>{price(plan.entry_zone.low)} – {price(plan.entry_zone.high)}</div></div>
                        <div><span className="text-[var(--muted-foreground)]">Stop</span><div>{price(plan.stop_loss)}</div></div>
                        <div><span className="text-[var(--muted-foreground)]">Quantity / size</span><div>{price(plan.quantity)} / {money(plan.position_size_usd)}</div></div>
                        <div><span className="text-[var(--muted-foreground)]">Maximum loss</span><div className="text-red-400">{money(plan.maximum_planned_loss_usd)}</div></div>
                        <div><span className="text-[var(--muted-foreground)]">Targets</span><div>{plan.targets.map((target) => `${target.label}: ${price(target.price)}`).join(" · ")}</div></div>
                        <div><span className="text-[var(--muted-foreground)]">Costs</span><div>{money(plan.estimated_costs_usd)} ({plan.estimated_cost_bps} bps)</div></div>
                        <div><span className="text-[var(--muted-foreground)]">Net reward/risk</span><div>{plan.net_reward_risk.toFixed(2)}</div></div>
                        <div><span className="text-[var(--muted-foreground)]">Time stop</span><div>{plan.time_stop}</div></div>
                      </div>
                      <div className="mt-2 grid gap-1 text-[var(--muted-foreground)] sm:grid-cols-2">
                        <div><span className="font-medium text-[var(--foreground)]">Scale in: </span>{plan.scale_in.map((step) => `${step.entry_pct}% ${step.instruction}`).join(" · ")}</div>
                        <div><span className="font-medium text-[var(--foreground)]">Scale out: </span>{plan.scale_out.map((step) => `${step.exit_pct}% ${step.instruction}`).join(" · ")}</div>
                      </div>
                      <div className="mt-2 text-[var(--muted-foreground)]">{plan.invalidation_reason}</div>
                    </div>
                  )}

                  {editing && (
                    <div className="grid gap-2 rounded border border-[var(--border)] bg-[var(--card)] p-3 sm:grid-cols-2 lg:grid-cols-3">
                      {([[
                        "Entry low", "entryLow"], ["Entry high", "entryHigh"], ["Stop loss", "stopLoss"], ["Targets, comma-separated", "targets"], ["Quantity", "quantity"], ["Time stop", "timeStop"],
                      ] as Array<[string, keyof EditValues]>).map(([label, key]) => <label key={key} className="text-[10px] text-[var(--muted-foreground)]">{label}<input value={editValues[key]} onChange={(event) => setEditValues({ ...editValues, [key]: event.target.value })} className="mt-1 w-full rounded border bg-[var(--background)] px-2 py-1 text-xs" /></label>)}
                      <div className="flex gap-2 sm:col-span-2 lg:col-span-3"><button onClick={() => saveEdit(opportunity)} disabled={actionBusy !== null} className="rounded bg-[var(--primary)] px-3 py-1 text-xs text-[var(--primary-foreground)]">Save plan</button><button onClick={() => { setEditingId(null); setEditValues(null); }} className="rounded bg-[var(--secondary)] px-3 py-1 text-xs">Cancel</button></div>
                    </div>
                  )}

                  <div className="flex flex-wrap gap-2">
                    <button onClick={() => handleAction(opportunity, { action: "approve" })} disabled={!opportunity.eligible || actionBusy !== null} className="rounded bg-green-700 px-3 py-1 text-xs text-white disabled:opacity-40">Approve</button>
                    <button onClick={() => handleAction(opportunity, { action: "reject" })} disabled={actionBusy !== null} className="rounded bg-red-700 px-3 py-1 text-xs text-white disabled:opacity-40">Reject</button>
                    <button onClick={() => handleAction(opportunity, { action: "snooze", snooze_minutes: 60 })} disabled={actionBusy !== null} className="rounded bg-[var(--secondary)] px-3 py-1 text-xs disabled:opacity-40">Snooze 1h</button>
                    {plan && <button onClick={() => beginEdit(opportunity)} disabled={actionBusy !== null} className="rounded bg-[var(--secondary)] px-3 py-1 text-xs disabled:opacity-40">Edit Plan</button>}
                  </div>
                </div>
              )}
            </div>
          );
        })}
        {scanResult && filteredSignals.length === 0 && <div className="rounded border border-dashed p-4 text-center text-xs text-[var(--muted-foreground)]">No opportunities match the current view.</div>}
      </div>
    </div>
  );
}
