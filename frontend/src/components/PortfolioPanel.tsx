import { useState, useEffect } from "react";
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
import { Wallet, TrendingUp, TrendingDown, BarChart3 } from "lucide-react";
import type { Portfolio } from "@/lib/api";
import { fetchPortfolio } from "@/lib/api";

export default function PortfolioPanel() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        setPortfolio(await fetchPortfolio());
      } catch {
        /* empty */
      }
    };
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, []);

  if (!portfolio) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Portfolio</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground">Loading...</p>
        </CardContent>
      </Card>
    );
  }

  const stats = [
    { label: "Balance", value: `$${portfolio.balance.toLocaleString()}`, icon: Wallet },
    { label: "Equity", value: `$${portfolio.equity.toLocaleString()}`, icon: BarChart3 },
    {
      label: "Total PnL",
      value: `$${portfolio.total_pnl.toLocaleString()}`,
      icon: portfolio.total_pnl >= 0 ? TrendingUp : TrendingDown,
      color: portfolio.total_pnl >= 0 ? "text-green-500" : "text-red-500",
    },
    { label: "Win Rate", value: `${portfolio.win_rate}%`, icon: TrendingUp },
    { label: "Max Drawdown", value: `${portfolio.max_drawdown}%`, icon: TrendingDown },
    { label: "Profit Factor", value: `${portfolio.profit_factor}`, icon: BarChart3 },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Wallet className="h-5 w-5" />
          Virtual Portfolio
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          {stats.map((stat) => (
            <div key={stat.label} className="p-3 rounded-lg border bg-card">
              <div className="flex items-center gap-1.5 mb-1">
                <stat.icon className={`h-3.5 w-3.5 ${stat.color || "text-muted-foreground"}`} />
                <span className="text-xs text-muted-foreground">{stat.label}</span>
              </div>
              <p className={`font-semibold text-sm ${stat.color || ""}`}>{stat.value}</p>
            </div>
          ))}
        </div>

        <div className="flex gap-2">
          <Badge variant="secondary">
            Wins: {portfolio.win_count}
          </Badge>
          <Badge variant="secondary">
            Losses: {portfolio.loss_count}
          </Badge>
        </div>

        {portfolio.equity_curve.length > 0 && (
          <div className="h-48">
            <p className="text-sm font-medium mb-2">Equity Curve</p>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={portfolio.equity_curve}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                <XAxis
                  dataKey="timestamp"
                  tick={false}
                  className="text-muted-foreground"
                />
                <YAxis className="text-muted-foreground" />
                <Tooltip />
                <Line
                  type="monotone"
                  dataKey="equity"
                  stroke="hsl(var(--chart-1))"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
