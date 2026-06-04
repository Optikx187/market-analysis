import { useState, useEffect } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Bell, AlertTriangle } from "lucide-react";
import type { Signal } from "@/lib/api";
import { fetchSignals } from "@/lib/api";

interface Props {
  refreshKey: number;
}

export default function SignalsPanel({ refreshKey }: Props) {
  const [signals, setSignals] = useState<Signal[]>([]);

  useEffect(() => {
    const load = async () => {
      try {
        setSignals(await fetchSignals());
      } catch {
        /* empty */
      }
    };
    load();
  }, [refreshKey]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Bell className="h-5 w-5" />
          Signals
          <Badge variant="secondary">{signals.length}</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ScrollArea className="h-80">
          <div className="space-y-2">
            {signals.map((signal) => (
              <div
                key={signal.id}
                className={`p-3 rounded-lg border ${
                  signal.suppressed ? "opacity-50" : ""
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold">{signal.ticker}</span>
                    <Badge
                      variant={signal.direction === "BUY" ? "default" : "destructive"}
                    >
                      {signal.direction}
                    </Badge>
                    {signal.suppressed && (
                      <Badge variant="outline" className="text-yellow-600">
                        <AlertTriangle className="h-3 w-3 mr-1" />
                        Suppressed
                      </Badge>
                    )}
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {signal.created_at
                      ? new Date(signal.created_at).toLocaleString()
                      : ""}
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div>
                    <span className="text-muted-foreground">Entry: </span>
                    <span className="font-mono">${signal.trigger_price.toLocaleString()}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Stop: </span>
                    <span className="font-mono text-red-500">
                      ${signal.stop_loss.toLocaleString()}
                    </span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Target: </span>
                    <span className="font-mono text-green-500">
                      ${signal.target_price.toLocaleString()}
                    </span>
                  </div>
                </div>
                {signal.reason && (
                  <p className="text-xs text-muted-foreground mt-1">{signal.reason}</p>
                )}
                <div className="flex gap-3 mt-1 text-xs text-muted-foreground">
                  {signal.risk_reward && <span>R:R {signal.risk_reward}</span>}
                  {signal.rsi_value && <span>RSI {signal.rsi_value}</span>}
                  {signal.atr_value && <span>ATR {signal.atr_value.toFixed(4)}</span>}
                </div>
              </div>
            ))}
            {signals.length === 0 && (
              <p className="text-sm text-muted-foreground text-center py-8">
                No signals generated yet. Analyze an asset to get signals.
              </p>
            )}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}
