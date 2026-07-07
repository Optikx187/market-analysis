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
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
      <div className="rounded-lg border bg-[var(--card)] p-3">
        <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">Balance</div>
        <div className="text-lg font-bold">${data.balance.toLocaleString()}</div>
      </div>
      <div className="rounded-lg border bg-[var(--card)] p-3">
        <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">Total P&L</div>
        <div className={`text-lg font-bold ${pnlColor}`}>
          {pnlSign}${data.total_pnl.toLocaleString()} ({pnlSign}{data.total_pnl_pct}%)
        </div>
      </div>
      <div className="rounded-lg border bg-[var(--card)] p-3">
        <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">Open Positions</div>
        <div className="text-lg font-bold">{data.open_positions}</div>
      </div>
      <div className="rounded-lg border bg-[var(--card)] p-3">
        <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">Today's Signals</div>
        <div className="text-lg font-bold">
          {data.todays_approved} <span className="text-xs text-[var(--muted-foreground)]">/ {data.todays_signals} scanned</span>
        </div>
      </div>
    </div>
  );
}
