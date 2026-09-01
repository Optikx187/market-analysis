import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  acknowledgeActionItem,
  apiErrorMessage,
  fetchActionItems,
  refreshActionItems,
  reopenActionItem,
  resolveActionItem,
  snoozeActionItem,
  type ActionItem,
  type ActionItemCounts,
  type ActionItemFilters,
} from "@/lib/api";
import { deepLinkLabel } from "@/lib/deepLink";

const UNRESOLVED = "open,acknowledged,snoozed";

const STATUS_VIEWS: { value: string; label: string }[] = [
  { value: UNRESOLVED, label: "Needs attention" },
  { value: "open", label: "Open" },
  { value: "acknowledged", label: "Acknowledged" },
  { value: "snoozed", label: "Snoozed" },
  { value: "resolved", label: "Resolved" },
  { value: "", label: "All" },
];

const CATEGORIES = ["opportunity", "risk", "data", "event", "execution", "operations"];
const SEVERITIES = ["critical", "warning", "info"];
const SNOOZE_CHOICES = [
  { minutes: 60, label: "1h" },
  { minutes: 240, label: "4h" },
  { minutes: 1440, label: "1d" },
];

/** Severity presentation never relies on color alone: each level also carries a
 * distinct glyph, text label, and border weight. */
const SEVERITY_STYLE: Record<string, { glyph: string; label: string; border: string; text: string }> = {
  critical: { glyph: "▲", label: "Critical", border: "border-l-4 border-l-red-500 border-dashed", text: "text-red-300" },
  warning: { glyph: "◆", label: "Warning", border: "border-l-4 border-l-amber-500", text: "text-amber-300" },
  info: { glyph: "●", label: "Info", border: "border-l-4 border-l-blue-500 border-dotted", text: "text-blue-300" },
};

const formatTime = (value: string | null): string => {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("en-US", {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit", hour12: true,
  });
};

export default function ActionInbox({ onOpenContext }: { onOpenContext: (item: ActionItem) => void }) {
  const [items, setItems] = useState<ActionItem[]>([]);
  const [counts, setCounts] = useState<ActionItemCounts | null>(null);
  const [mandatoryNote, setMandatoryNote] = useState("");
  const [statusView, setStatusView] = useState(UNRESOLVED);
  const [category, setCategory] = useState("");
  const [severity, setSeverity] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [statusMessage, setStatusMessage] = useState("Loading Action Required inbox…");
  const [error, setError] = useState<string | null>(null);
  const headingRef = useRef<HTMLDivElement>(null);

  const filters = useMemo<ActionItemFilters>(() => {
    const next: ActionItemFilters = {};
    if (statusView) next.status = statusView;
    if (category) next.category = category;
    if (severity) next.severity = severity;
    return next;
  }, [statusView, category, severity]);

  const applyResponse = useCallback(
    (response: { items: ActionItem[]; counts: ActionItemCounts; mandatory_note: string }) => {
      setItems(response.items);
      setCounts(response.counts);
      setMandatoryNote(response.mandatory_note);
      setError(null);
    },
    [],
  );

  const load = useCallback(async () => {
    try {
      const response = await fetchActionItems(filters);
      applyResponse(response);
      setStatusMessage(
        `${response.counts.unresolved} item${response.counts.unresolved === 1 ? "" : "s"} need attention, ` +
          `${response.counts.mandatory} mandatory. Showing ${response.items.length}.`,
      );
    } catch (err) {
      setError(apiErrorMessage(err, "Action Required inbox is unavailable."));
      setStatusMessage("Action Required inbox is unavailable.");
    }
  }, [applyResponse, filters]);

  useEffect(() => {
    let cancelled = false;
    const initial = async () => {
      try {
        const response = await refreshActionItems(filters);
        if (!cancelled) applyResponse(response);
      } catch {
        if (!cancelled) await load();
      }
    };
    initial();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const interval = setInterval(() => { load(); }, 60000);
    return () => clearInterval(interval);
  }, [load]);

  const refreshNow = async () => {
    setRefreshing(true);
    try {
      const response = await refreshActionItems(filters);
      applyResponse(response);
      const refreshed = response.refreshed;
      setStatusMessage(
        refreshed
          ? `Refreshed: ${refreshed.created} new, ${refreshed.updated} updated, ${refreshed.cleared} cleared.`
          : "Inbox refreshed.",
      );
    } catch (err) {
      setError(apiErrorMessage(err, "Failed to refresh Action Required items."));
    }
    setRefreshing(false);
  };

  const runTransition = async (item: ActionItem, action: (id: number) => Promise<ActionItem>, label: string) => {
    setBusyId(item.id);
    try {
      await action(item.id);
      setStatusMessage(`${label}: ${item.title}`);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err, `Failed to ${label.toLowerCase()} this item.`));
    }
    setBusyId(null);
  };

  const focusInbox = () => headingRef.current?.focus();

  useEffect(() => {
    const handler = () => focusInbox();
    window.addEventListener("focus-action-inbox", handler);
    return () => window.removeEventListener("focus-action-inbox", handler);
  }, []);

  const filtersActive = Boolean(category || severity);

  return (
    <section
      aria-labelledby="action-inbox-heading"
      className="rounded-lg border-2 border-[var(--border)] bg-[var(--card)] p-4"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div
          id="action-inbox"
          ref={headingRef}
          tabIndex={-1}
          className="focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
        >
          <h2 id="action-inbox-heading" className="text-lg font-semibold">
            Action Required
          </h2>
          <p className="text-xs text-[var(--muted-foreground)]">
            Decisions waiting on you — acknowledge, snooze, resolve, or open the context.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {counts && (
            <ul className="flex flex-wrap items-center gap-1 text-[10px]" aria-label="Action Required counts">
              <li className="rounded border px-1.5 py-0.5">Needs attention: {counts.unresolved}</li>
              <li className="rounded border px-1.5 py-0.5">Open: {counts.open}</li>
              <li className="rounded border border-red-500 px-1.5 py-0.5">
                ▲ Critical: {counts.by_severity.critical}
              </li>
              <li className="rounded border border-amber-500 px-1.5 py-0.5">
                ◆ Warning: {counts.by_severity.warning}
              </li>
              <li className="rounded border px-1.5 py-0.5">Mandatory: {counts.mandatory}</li>
            </ul>
          )}
          <button
            onClick={refreshNow}
            disabled={refreshing}
            className="rounded bg-[var(--secondary)] px-3 py-1 text-xs hover:bg-[var(--accent)] focus-visible:ring-2 focus-visible:ring-blue-400 disabled:opacity-50"
          >
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
        <label className="text-[10px] text-[var(--muted-foreground)]">
          View
          <select
            value={statusView}
            onChange={(event) => setStatusView(event.target.value)}
            className="mt-1 w-full rounded border bg-[var(--input)] px-2 py-1 text-xs"
          >
            {STATUS_VIEWS.map((view) => (
              <option key={view.label} value={view.value}>{view.label}</option>
            ))}
          </select>
        </label>
        <label className="text-[10px] text-[var(--muted-foreground)]">
          Category
          <select
            value={category}
            onChange={(event) => setCategory(event.target.value)}
            className="mt-1 w-full rounded border bg-[var(--input)] px-2 py-1 text-xs"
          >
            <option value="">All categories</option>
            {CATEGORIES.map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </select>
        </label>
        <label className="text-[10px] text-[var(--muted-foreground)]">
          Severity
          <select
            value={severity}
            onChange={(event) => setSeverity(event.target.value)}
            className="mt-1 w-full rounded border bg-[var(--input)] px-2 py-1 text-xs"
          >
            <option value="">All severities</option>
            {SEVERITIES.map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </select>
        </label>
      </div>

      {mandatoryNote && (
        <p className={`mt-2 rounded border p-2 text-[10px] ${filtersActive ? "border-red-500 bg-red-500/10" : "border-[var(--border)]"}`}>
          <span aria-hidden="true">▲ </span>
          {mandatoryNote}
        </p>
      )}

      <div aria-live="polite" role="status" className="mt-2 text-[10px] text-[var(--muted-foreground)]">
        {statusMessage}
      </div>

      {error && (
        <div className="mt-2 rounded border border-red-600 bg-red-600/10 p-2 text-xs text-red-300">{error}</div>
      )}

      {items.length === 0 ? (
        <p className="mt-3 text-sm text-[var(--muted-foreground)]">
          Nothing needs a decision in this view.
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {items.map((item) => {
            const style = SEVERITY_STYLE[item.severity] ?? SEVERITY_STYLE.info;
            const busy = busyId === item.id;
            return (
              <li
                key={item.id}
                className={`rounded border bg-[var(--background)] p-3 ${style.border}`}
              >
                <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-1.5 text-[10px] uppercase tracking-wide">
                      <span className={`font-semibold ${style.text}`}>
                        <span aria-hidden="true">{style.glyph} </span>
                        {style.label}
                      </span>
                      <span className="rounded border px-1.5 py-0.5">{item.category}</span>
                      <span className="rounded border px-1.5 py-0.5">{item.source_type.replace(/_/g, " ")}</span>
                      <span className="rounded border px-1.5 py-0.5">{item.status}</span>
                      {item.is_mandatory && (
                        <span className="rounded border border-red-500 px-1.5 py-0.5 text-red-300">
                          Mandatory — cannot be filtered out
                        </span>
                      )}
                      {item.ticker && <span className="rounded border px-1.5 py-0.5">{item.ticker}</span>}
                    </div>
                    <div className="mt-1 text-sm font-medium">{item.title}</div>
                    <p className="text-xs text-[var(--muted-foreground)] break-words">{item.message}</p>
                    <div className="mt-1 text-[10px] text-[var(--muted-foreground)]">
                      First seen {formatTime(item.first_seen_at)} · updated {formatTime(item.updated_at)}
                      {item.snoozed_until ? ` · snoozed until ${formatTime(item.snoozed_until)}` : ""}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1.5 sm:justify-end">
                    <button
                      onClick={() => onOpenContext(item)}
                      className="rounded bg-[var(--primary)] px-2 py-1 text-[10px] font-medium text-[var(--primary-foreground)] focus-visible:ring-2 focus-visible:ring-blue-400"
                      aria-label={`${deepLinkLabel(item.deep_link)} for ${item.title}`}
                    >
                      Open context
                    </button>
                    <button
                      onClick={() => runTransition(item, acknowledgeActionItem, "Acknowledged")}
                      disabled={busy}
                      className="rounded bg-[var(--secondary)] px-2 py-1 text-[10px] hover:bg-[var(--accent)] focus-visible:ring-2 focus-visible:ring-blue-400 disabled:opacity-50"
                      aria-label={`Acknowledge ${item.title}`}
                    >
                      Ack
                    </button>
                    {SNOOZE_CHOICES.map((choice) => (
                      <button
                        key={choice.minutes}
                        onClick={() => runTransition(item, (id) => snoozeActionItem(id, choice.minutes), `Snoozed ${choice.label}`)}
                        disabled={busy}
                        className="rounded bg-[var(--secondary)] px-2 py-1 text-[10px] hover:bg-[var(--accent)] focus-visible:ring-2 focus-visible:ring-blue-400 disabled:opacity-50"
                        aria-label={`Snooze ${item.title} for ${choice.label}`}
                      >
                        Snooze {choice.label}
                      </button>
                    ))}
                    {item.status === "resolved" ? (
                      <button
                        onClick={() => runTransition(item, reopenActionItem, "Reopened")}
                        disabled={busy}
                        className="rounded bg-[var(--secondary)] px-2 py-1 text-[10px] hover:bg-[var(--accent)] focus-visible:ring-2 focus-visible:ring-blue-400 disabled:opacity-50"
                        aria-label={`Reopen ${item.title}`}
                      >
                        Reopen
                      </button>
                    ) : (
                      <button
                        onClick={() => runTransition(item, resolveActionItem, "Resolved")}
                        disabled={busy}
                        className="rounded bg-[var(--secondary)] px-2 py-1 text-[10px] hover:bg-[var(--accent)] focus-visible:ring-2 focus-visible:ring-blue-400 disabled:opacity-50"
                        aria-label={`Resolve ${item.title}`}
                      >
                        Resolve
                      </button>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
