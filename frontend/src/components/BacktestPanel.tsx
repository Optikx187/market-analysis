import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { BarChart3 } from "lucide-react";
import type { BacktestResult } from "@/lib/api";

interface Props {
  result: BacktestResult | null;
}

export default function BacktestPanel({ result }: Props) {
  if (!result) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <BarChart3 className="h-5 w-5" />
            Backtest Results
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground text-center py-8">
            Click the chart icon on a watchlist asset to run a backtest.
          </p>
        </CardContent>
      </Card>
    );
  }

  const equityData = result.equity_curve.map((eq, i) => ({
    index: i,
    equity: eq,
  }));

  const stats = [
    { label: "Ticker", value: result.ticker },
    { label: "Total Trades", value: result.total_trades },
    { label: "Wins / Losses", value: `${result.wins} / ${result.losses}` },
    { label: "Win Rate", value: `${result.win_rate.toFixed(1)}%` },
    {
      label: "Total PnL",
      value: `$${result.total_pnl.toLocaleString()}`,
      color: result.total_pnl >= 0 ? "text-green-500" : "text-red-500",
    },
    { label: "Max Drawdown", value: `${result.max_drawdown.toFixed(2)}%` },
    { label: "Profit Factor", value: result.profit_factor.toFixed(2) },
    {
      label: "Final Balance",
      value: `$${result.final_balance.toLocaleString()}`,
    },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BarChart3 className="h-5 w-5" />
          Backtest: {result.ticker}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          {stats.map((stat) => (
            <div key={stat.label} className="p-2 rounded-lg border">
              <p className="text-xs text-muted-foreground">{stat.label}</p>
              <p className={`font-semibold text-sm ${stat.color || ""}`}>
                {stat.value}
              </p>
            </div>
          ))}
        </div>

        {equityData.length > 0 && (
          <div className="h-48">
            <p className="text-sm font-medium mb-2">Equity Curve</p>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={equityData}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                <XAxis dataKey="index" tick={false} />
                <YAxis />
                <Tooltip />
                <Line
                  type="monotone"
                  dataKey="equity"
                  stroke="hsl(var(--chart-2))"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}

        {result.trades.length > 0 && (
          <div>
            <p className="text-sm font-medium mb-2">Trade Log</p>
            <div className="space-y-1 max-h-40 overflow-y-auto">
              {result.trades.map((t, i) => (
                <div
                  key={i}
                  className="flex items-center gap-2 text-xs py-1 px-2 rounded border"
                >
                  <Badge variant={t.direction === "BUY" ? "default" : "destructive"} className="text-[10px]">
                    {t.direction}
                  </Badge>
                  <span className="font-mono">
                    ${t.entry_price.toFixed(2)} → ${t.exit_price.toFixed(2)}
                  </span>
                  <span
                    className={`font-mono ml-auto ${
                      t.pnl >= 0 ? "text-green-500" : "text-red-500"
                    }`}
                  >
                    ${t.pnl.toFixed(2)} ({t.pnl_pct.toFixed(1)}%)
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
