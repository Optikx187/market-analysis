import { useEffect, useState } from "react";
import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
} from "recharts";
import api from "@/lib/api";

interface ChartCandle {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface SignalMarker {
  date: string;
  direction: string;
  price: number;
}

interface Props {
  ticker: string | null;
}

export default function HistoricalChart({ ticker }: Props) {
  const [candles, setCandles] = useState<ChartCandle[]>([]);
  const [signals, setSignals] = useState<SignalMarker[]>([]);
  const [loading, setLoading] = useState(false);
  const [range, setRange] = useState(90);

  useEffect(() => {
    if (!ticker) return;
    setLoading(true);
    api.get(`/candles/${ticker}`).then((res) => {
      const data = res.data as ChartCandle[];
      setCandles(data);
    }).catch(() => setCandles([])).finally(() => setLoading(false));

    api.get(`/alerts?ticker=${ticker}&limit=50`).then((res) => {
      const alerts = res.data as Array<{ ticker: string; direction: string; trigger_price: number; created_at: string }>;
      setSignals(alerts.filter((a) => a.ticker === ticker).map((a) => ({
        date: a.created_at?.split("T")[0] || "",
        direction: a.direction,
        price: a.trigger_price,
      })));
    }).catch(() => setSignals([]));
  }, [ticker]);

  if (!ticker) {
    return (
      <div className="rounded-lg border bg-[var(--card)] p-4 text-center text-sm text-[var(--muted-foreground)]">
        Select a ticker from the watchlist to view its chart
      </div>
    );
  }

  if (loading) {
    return (
      <div className="rounded-lg border bg-[var(--card)] p-4 text-center text-sm text-[var(--muted-foreground)]">
        Loading chart for {ticker}...
      </div>
    );
  }

  const displayCandles = candles.slice(-range).map((c) => ({
    ...c,
    displayDate: c.date ? c.date.slice(5, 10) : "",
    bodyColor: c.close >= c.open ? "#22c55e" : "#ef4444",
    bodyHeight: Math.abs(c.close - c.open),
  }));

  const signalDates = new Set(signals.map((s) => s.date));

  return (
    <div className="rounded-lg border bg-[var(--card)] p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold">{ticker} Price Chart</h3>
        <div className="flex gap-1">
          {[30, 60, 90, 180].map((d) => (
            <button
              key={d}
              onClick={() => setRange(d)}
              className={`px-2 py-0.5 text-xs rounded ${
                range === d ? "bg-[var(--primary)] text-[var(--primary-foreground)]" : "bg-[var(--secondary)] text-[var(--muted-foreground)]"
              }`}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {displayCandles.length === 0 ? (
        <div className="text-xs text-[var(--muted-foreground)] text-center py-8">
          No candle data available for {ticker}
        </div>
      ) : (
        <div className="space-y-2">
          <ResponsiveContainer width="100%" height={280}>
            <ComposedChart data={displayCandles}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="displayDate" tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} />
              <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} />
              <Tooltip
                contentStyle={{ backgroundColor: "var(--card)", border: "1px solid var(--border)", fontSize: 12 }}
                labelStyle={{ color: "var(--foreground)" }}
                formatter={(value) => [`$${Number(value).toFixed(2)}`, ""]}
              />
              <Line type="monotone" dataKey="close" stroke="#3b82f6" dot={false} strokeWidth={1.5} />
              <Line type="monotone" dataKey="high" stroke="#22c55e44" dot={false} strokeWidth={0.5} />
              <Line type="monotone" dataKey="low" stroke="#ef444444" dot={false} strokeWidth={0.5} />
              {signals.map((s, i) => (
                <ReferenceLine
                  key={i}
                  y={s.price}
                  stroke={s.direction === "BUY" ? "#22c55e" : "#ef4444"}
                  strokeDasharray="3 3"
                  strokeWidth={0.8}
                />
              ))}
            </ComposedChart>
          </ResponsiveContainer>

          <ResponsiveContainer width="100%" height={60}>
            <ComposedChart data={displayCandles}>
              <XAxis dataKey="displayDate" hide />
              <YAxis hide />
              <Bar dataKey="volume" fill="#3b82f640" />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}

      {signals.length > 0 && (
        <div className="mt-2 text-[10px] text-[var(--muted-foreground)]">
          Signal history: {signals.map((s) => `${s.direction} @ $${s.price.toFixed(2)}`).join(", ")}
        </div>
      )}
    </div>
  );
}
