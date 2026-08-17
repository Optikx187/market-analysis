import { useEffect, useState } from "react";
import {
  ATTRIBUTION_DIMENSIONS,
  attributionExportUrl,
  fetchAttribution,
  type AttributionDimension,
  type AttributionFilters,
  type AttributionResponse,
  type AttributedTrade,
  type AttributionGroup,
} from "@/lib/api";

const DIMENSION_LABELS: Record<AttributionDimension, string> = {
  strategy: "Strategy",
  ticker: "Ticker",
  asset_type: "Asset Type",
  sector: "Sector",
  timeframe: "Timeframe",
  regime: "Regime",
};

const RECOMMENDATION_LABELS: Record<string, string> = {
  keep_enabled: "Keep enabled",
  review_or_disable: "Review or disable",
  monitor: "Monitor",
  insufficient_history: "Insufficient sample",
};

const money = (value: number) =>
  `${value < 0 ? "-" : ""}$${Math.abs(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;

const optional = (value: number | null | undefined, suffix = "") =>
  value === null || value === undefined ? "Not recorded" : `${value}${suffix}`;

export default function AttributionPanel() {
  const [data, setData] = useState<AttributionResponse | null>(null);
  const [filters, setFilters] = useState<AttributionFilters>({});
  const [dimension, setDimension] = useState<AttributionDimension>("strategy");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedTrade, setExpandedTrade] = useState<number | null>(null);

  useEffect(() => {
    setLoading(true);
    fetchAttribution(filters)
      .then((payload) => {
        setData(payload);
        setError(null);
      })
      .catch(() => setError("Could not load attribution. Is the portfolio engine running?"))
      .finally(() => setLoading(false));
  }, [filters]);

  if (loading && !data) {
    return <div className="rounded-lg border bg-[var(--card)] p-4 text-sm">Loading attribution...</div>;
  }
  if (error && !data) {
    return <div className="rounded-lg border bg-[var(--card)] p-4 text-sm text-red-400">{error}</div>;
  }
  if (!data) return null;

  const { summary, reconciliation, min_sample_size: minSample } = data;
  const groups = data.dimensions[dimension] ?? [];
  const journals = new Map(data.journals.map((entry) => [entry.trade_id, entry]));
  const activeFilters = Object.entries(filters).filter(([, value]) => Boolean(value));

  return (
    <div className="rounded-lg border bg-[var(--card)] p-4 space-y-4">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h2 className="text-lg font-semibold">Performance Attribution</h2>
          <p className="text-xs text-[var(--muted-foreground)]">
            Net realized P&amp;L after fees and slippage, attributed across closed trades. Missing
            metadata is grouped as Unknown &mdash; never inferred.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <a
            href={attributionExportUrl("csv", filters)}
            className="rounded bg-[var(--secondary)] px-3 py-1.5 text-xs hover:bg-[var(--accent)]"
            download
          >
            Export CSV
          </a>
          <a
            href={attributionExportUrl("json", filters)}
            className="rounded bg-[var(--secondary)] px-3 py-1.5 text-xs hover:bg-[var(--accent)]"
            target="_blank"
            rel="noreferrer"
          >
            Export JSON
          </a>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
        {ATTRIBUTION_DIMENSIONS.map((key) => (
          <label key={key} className="text-xs space-y-1">
            <span className="text-[var(--muted-foreground)]">{DIMENSION_LABELS[key]}</span>
            <select
              value={filters[key] ?? ""}
              onChange={(event) =>
                setFilters({ ...filters, [key]: event.target.value || undefined })
              }
              className="w-full rounded border bg-[var(--input)] px-2 py-1.5 text-xs"
            >
              <option value="">All</option>
              {(data.filters_available[key] ?? []).map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      {activeFilters.length > 0 && (
        <button
          onClick={() => setFilters({})}
          className="text-xs text-[var(--muted-foreground)] underline"
        >
          Clear {activeFilters.length} filter{activeFilters.length > 1 ? "s" : ""}
        </button>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="Sample Size" value={`${summary.sample_size} closed`} />
        <Stat
          label="Net P&L (filtered)"
          value={money(summary.net_pnl)}
          className={summary.net_pnl >= 0 ? "text-green-400" : "text-red-400"}
        />
        <Stat label="Costs" value={money(summary.costs)} detail={`Gross ${money(summary.gross_pnl)}`} />
        <Stat
          label="Win Rate"
          value={summary.sample_size > 0 ? `${summary.win_rate}%` : "—"}
          detail={`${summary.wins}W / ${summary.losses}L`}
        />
      </div>

      <div
        className={`rounded border p-3 text-xs space-y-1 ${
          reconciliation.reconciles
            ? "border-green-600/40 bg-green-600/5"
            : "border-red-600/50 bg-red-600/5"
        }`}
      >
        <div className="flex items-center justify-between">
          <span className="font-medium">Reconciliation</span>
          <span className={reconciliation.reconciles ? "text-green-400" : "text-red-400"}>
            {reconciliation.reconciles ? "Exact match" : `Delta ${money(reconciliation.delta)}`}
          </span>
        </div>
        <div className="text-[var(--muted-foreground)]">
          Attributed net P&amp;L {money(reconciliation.attributed_net_pnl)} vs portfolio total P&amp;L{" "}
          {money(reconciliation.portfolio_total_pnl)} · filtered subset{" "}
          {money(reconciliation.filtered_net_pnl)}
        </div>
      </div>

      {!summary.sufficient_sample && (
        <div className="rounded border border-amber-500/40 bg-amber-500/5 p-2 text-xs text-amber-300">
          {summary.sample_note || `Fewer than ${minSample} closed trades`} — treat groupings as
          descriptive only. Recommendations and confidence calibration stay suppressed until{" "}
          {minSample} closed trades are attributed.
        </div>
      )}

      <div className="space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-medium">Grouped Results</span>
          <div className="flex gap-1 flex-wrap">
            {ATTRIBUTION_DIMENSIONS.map((key) => (
              <button
                key={key}
                onClick={() => setDimension(key)}
                className={`rounded px-2 py-1 text-xs ${
                  dimension === key
                    ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                    : "text-[var(--muted-foreground)] bg-[var(--secondary)]"
                }`}
              >
                {DIMENSION_LABELS[key]}
              </button>
            ))}
          </div>
        </div>
        {groups.length === 0 ? (
          <p className="text-xs text-[var(--muted-foreground)]">
            No closed trades match these filters yet.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-[var(--muted-foreground)]">
                <tr className="border-b border-[var(--border)]">
                  <th className="text-left py-1">{DIMENSION_LABELS[dimension]}</th>
                  <th className="text-right py-1">Sample</th>
                  <th className="text-right py-1">Net P&amp;L</th>
                  <th className="text-right py-1">Costs</th>
                  <th className="text-right py-1">Win Rate</th>
                  <th className="text-right py-1">Avg / Trade</th>
                  <th className="text-left py-1 pl-3">Guidance</th>
                </tr>
              </thead>
              <tbody>
                {groups.map((group) => (
                  <GroupRow key={group.key} group={group} minSample={minSample} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="space-y-2">
        <div className="text-sm font-medium">Confidence Calibration</div>
        {data.confidence_calibration.length === 0 ? (
          <p className="text-xs text-[var(--muted-foreground)]">No closed trades to calibrate.</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
            {data.confidence_calibration.map((band) => (
              <div key={band.band} className="rounded border border-[var(--border)] p-2 text-xs">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{band.band}</span>
                  <span className="text-[var(--muted-foreground)]">n={band.sample_size}</span>
                </div>
                {band.sufficient_sample ? (
                  <div className="text-[var(--muted-foreground)]">
                    Observed win rate {band.observed_win_rate}% · predicted{" "}
                    {optional(band.avg_signal_confidence, "%")} · gap{" "}
                    {optional(band.calibration_gap, " pts")}
                  </div>
                ) : (
                  <div className="text-amber-300">
                    Calibration suppressed — {band.sample_note || `needs ${minSample} trades`}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="space-y-2">
        <div className="text-sm font-medium">Trades &amp; Journals ({data.trades.length})</div>
        {data.trades.length === 0 ? (
          <p className="text-xs text-[var(--muted-foreground)]">Nothing to show for these filters.</p>
        ) : (
          <div className="space-y-1 max-h-[420px] overflow-y-auto">
            {data.trades.map((trade) => (
              <div key={trade.id} className="rounded border border-[var(--border)]">
                <button
                  onClick={() => setExpandedTrade(expandedTrade === trade.id ? null : trade.id)}
                  className="w-full px-3 py-2 flex items-center justify-between text-left"
                >
                  <span className="flex items-center gap-2 flex-wrap text-xs">
                    <span className="font-medium text-sm">{trade.ticker}</span>
                    <span className={trade.direction === "BUY" ? "text-green-400" : "text-red-400"}>
                      {trade.direction}
                    </span>
                    <span className="text-[var(--muted-foreground)]">
                      {trade.strategy ?? "Unknown"} · {trade.timeframe ?? "Unknown"} ·{" "}
                      {trade.regime ?? "Unknown"}
                    </span>
                    <span className="rounded bg-[var(--muted)] px-1.5 py-0.5 text-[10px] text-[var(--muted-foreground)]">
                      {trade.exit_count} exit{trade.exit_count === 1 ? "" : "s"}
                    </span>
                  </span>
                  <span
                    className={`text-sm font-medium ${
                      trade.net_pnl >= 0 ? "text-green-400" : "text-red-400"
                    }`}
                  >
                    {money(trade.net_pnl)}
                  </span>
                </button>
                {expandedTrade === trade.id && (
                  <TradeDetail trade={trade} summary={journals.get(trade.id)?.summary ?? null} />
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function GroupRow({ group, minSample }: { group: AttributionGroup; minSample: number }) {
  return (
    <tr className="border-b border-[var(--border)]/50">
      <td className="py-1">{group.key}</td>
      <td className="py-1 text-right">
        {group.sample_size}
        {!group.sufficient_sample && (
          <span className="text-amber-300" title={`Below the ${minSample}-trade minimum`}>
            {" "}
            !
          </span>
        )}
      </td>
      <td className={`py-1 text-right ${group.net_pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
        {money(group.net_pnl)}
      </td>
      <td className="py-1 text-right">{money(group.costs)}</td>
      <td className="py-1 text-right">{group.win_rate}%</td>
      <td className="py-1 text-right">{money(group.avg_net_pnl)}</td>
      <td className="py-1 pl-3">
        {group.sufficient_sample ? (
          RECOMMENDATION_LABELS[group.recommendation] ?? group.recommendation
        ) : (
          <span className="text-amber-300">{group.sample_note}</span>
        )}
      </td>
    </tr>
  );
}

function TradeDetail({ trade, summary }: { trade: AttributedTrade; summary: string | null }) {
  return (
    <div className="border-t border-[var(--border)] px-3 py-2 space-y-2 text-xs">
      {summary ? (
        <p className="text-[var(--muted-foreground)]">{summary}</p>
      ) : (
        <p className="text-amber-300">No automated journal recorded for this trade.</p>
      )}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <Detail label="Entry (planned)" value={`${trade.entry_price} (${optional(trade.planned_entry_price)})`} />
        <Detail
          label="Avg exit (planned)"
          value={`${optional(trade.average_exit_price)} (${optional(trade.planned_exit_price)})`}
        />
        <Detail label="Size (planned)" value={`${trade.quantity} (${optional(trade.planned_quantity)})`} />
        <Detail label="Confidence" value={optional(trade.signal_confidence, "%")} />
        <Detail label="Gross P&L" value={money(trade.gross_pnl ?? 0)} />
        <Detail label="Costs" value={money(trade.costs)} />
        <Detail
          label="MFE / MAE"
          value={
            trade.excursion_status === "calculated"
              ? `${money(trade.mfe_usd ?? 0)} / ${money(trade.mae_usd ?? 0)}`
              : `Unavailable (${trade.excursion_status})`
          }
        />
        <Detail label="Sector / Asset" value={`${trade.sector ?? "Unknown"} · ${trade.asset_type ?? "Unknown"}`} />
      </div>
      <div className="space-y-1">
        <div className="text-[var(--muted-foreground)]">Fills</div>
        {trade.executions.length === 0 ? (
          <div className="text-amber-300">No execution records (legacy trade).</div>
        ) : (
          trade.executions.map((execution) => (
            <div key={execution.id} className="flex items-center justify-between">
              <span>
                {execution.kind} {execution.quantity} @ ${execution.price}
              </span>
              <span className="text-[var(--muted-foreground)]">
                fees {money(execution.fees)} · slippage {money(execution.slippage)}
                {execution.net_pnl !== null ? ` · net ${money(execution.net_pnl)}` : ""}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  detail,
  className = "",
}: {
  label: string;
  value: string;
  detail?: string;
  className?: string;
}) {
  return (
    <div className="rounded border border-[var(--border)] bg-[var(--background)] p-2">
      <div className="text-[10px] text-[var(--muted-foreground)]">{label}</div>
      <div className={`text-sm font-medium ${className}`}>{value}</div>
      {detail && <div className="text-[10px] text-[var(--muted-foreground)]">{detail}</div>}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] text-[var(--muted-foreground)]">{label}</div>
      <div>{value}</div>
    </div>
  );
}
