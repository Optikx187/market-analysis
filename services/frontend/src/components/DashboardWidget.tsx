import { useEffect, useState } from "react";
import { fetchDashboardSummary, type DashboardSummary } from "@/lib/api";

export default function DashboardWidget() {
  const [data, setData] = useState<DashboardSummary | null>(null);

  useEffect(() => {
    const load = () => fetchDashboardSummary().then(setData).catch(() => {});
    load();
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, []);

  if (!data) return null;

  const pnlColor = data.total_pnl >= 0 ? "text-green-400" : "text-red-400";
  const pnlSign = data.total_pnl >= 0 ? "+" : "";

  return (
    <div className="mb-6 space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <DashboardCard label="Balance" value={`$${data.balance.toLocaleString()}`} />
        <DashboardCard
          label="Total P&L"
          value={`${pnlSign}$${data.total_pnl.toLocaleString()} (${pnlSign}${data.total_pnl_pct}%)`}
          color={pnlColor}
        />
        <DashboardCard label="Open Positions" value={`${data.open_positions}`} />
        <DashboardCard label="Today's Signals" value={`${data.todays_approved} / ${data.todays_signals}`} detail="approved / scanned" />
        <DashboardCard
          label="Portfolio Heat"
          value={`${data.risk.heat.effective_pct}%`}
          detail={`$${data.risk.heat.effective_risk_usd.toLocaleString()} effective risk`}
          color={data.risk.heat.utilization_pct >= 80 ? "text-red-400" : "text-orange-400"}
        />
        <DashboardCard
          label="Heat Utilization"
          value={`${data.risk.heat.utilization_pct}%`}
          detail={`${data.risk.heat.limit_pct}% portfolio limit`}
          color={data.risk.heat.utilization_pct >= 80 ? "text-red-400" : "text-blue-400"}
        />
        <DashboardCard
          label="Largest Concentration"
          value={`${data.risk.exposure.largest_concentration.pct}%`}
          detail={`${data.risk.exposure.largest_concentration.category}: ${data.risk.exposure.largest_concentration.name}`}
          color="text-yellow-400"
        />
        <DashboardCard
          label="Risk Breaker"
          value={data.risk.breaker.active ? "New Risk Blocked" : "Clear"}
          detail={data.risk.breaker.active ? data.risk.breaker.reasons[0] || "Limit breached" : `${data.risk.breaker.current_drawdown_pct}% drawdown`}
          color={data.risk.breaker.active ? "text-red-400" : "text-green-400"}
        />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-[10px]">
        {data.risk.stress_tests.map((scenario) => (
          <div key={scenario.name} className="flex items-center justify-between rounded border border-[var(--border)] bg-[var(--card)] px-2 py-1">
            <span className="text-[var(--muted-foreground)]">{scenario.name}</span>
            <span className={scenario.estimated_pnl >= 0 ? "text-green-400" : "text-red-400"}>
              {scenario.estimated_pnl >= 0 ? "+" : ""}${scenario.estimated_pnl.toLocaleString()}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function DashboardCard({
  label,
  value,
  detail,
  color = "",
}: {
  label: string;
  value: string;
  detail?: string;
  color?: string;
}) {
  return (
    <div className="rounded-lg border bg-[var(--card)] p-3">
      <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">{label}</div>
      <div className={`text-lg font-bold ${color}`}>{value}</div>
      {detail && <div className="text-[10px] text-[var(--muted-foreground)]">{detail}</div>}
    </div>
  );
}
