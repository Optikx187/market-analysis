import { useEffect, useState } from "react";
import { fetchPortfolio, fetchRobinhoodBalance, type Portfolio } from "@/lib/api";

export default function PortfolioPanel() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [rhBalance, setRhBalance] = useState<number | null>(null);
  const [rhConnected, setRhConnected] = useState(false);

  useEffect(() => {
    fetchPortfolio().then(setPortfolio).catch(() => {});
    fetchRobinhoodBalance()
      .then((r) => { setRhBalance(r.buying_power); setRhConnected(r.connected); })
      .catch(() => {});
  }, []);

  if (!portfolio) return <div className="rounded-lg border bg-[var(--card)] p-4">Loading portfolio...</div>;

  const pnlColor = portfolio.total_pnl >= 0 ? "text-green-400" : "text-red-400";

  return (
    <div className="rounded-lg border bg-[var(--card)] p-4">
      <h2 className="text-lg font-semibold mb-3">Portfolio</h2>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat label="Balance" value={`$${portfolio.balance.toLocaleString()}`} />
        <Stat label="Equity" value={`$${portfolio.equity.toLocaleString()}`} />
        <Stat label="Total PnL" value={`$${portfolio.total_pnl.toLocaleString()}`} className={pnlColor} />
        <Stat label="Win Rate" value={`${portfolio.win_rate}%`} />
        <Stat label="Max Drawdown" value={`${portfolio.max_drawdown}%`} />
        <Stat label="Profit Factor" value={portfolio.profit_factor.toString()} />
        <Stat label="Wins / Losses" value={`${portfolio.win_count} / ${portfolio.loss_count}`} />
        <Stat
          label="Robinhood Cash"
          value={rhConnected ? `$${rhBalance?.toLocaleString() ?? "—"}` : "Not connected"}
          className={rhConnected ? "text-green-400" : "text-[var(--muted-foreground)]"}
        />
      </div>
    </div>
  );
}

function Stat({ label, value, className = "" }: { label: string; value: string; className?: string }) {
  return (
    <div>
      <div className="text-xs text-[var(--muted-foreground)]">{label}</div>
      <div className={`text-lg font-semibold ${className}`}>{value}</div>
    </div>
  );
}
