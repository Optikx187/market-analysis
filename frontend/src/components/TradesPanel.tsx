import { useState, useEffect } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ArrowUpDown } from "lucide-react";
import type { Trade } from "@/lib/api";
import { fetchTrades } from "@/lib/api";

export default function TradesPanel() {
  const [trades, setTrades] = useState<Trade[]>([]);

  useEffect(() => {
    const load = async () => {
      try {
        setTrades(await fetchTrades());
      } catch {
        /* empty */
      }
    };
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, []);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ArrowUpDown className="h-5 w-5" />
          Trade History
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ScrollArea className="h-80">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Ticker</TableHead>
                <TableHead>Direction</TableHead>
                <TableHead>Entry</TableHead>
                <TableHead>Exit</TableHead>
                <TableHead>Qty</TableHead>
                <TableHead>PnL</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {trades.map((trade) => (
                <TableRow key={trade.id}>
                  <TableCell className="font-semibold">{trade.ticker}</TableCell>
                  <TableCell>
                    <Badge
                      variant={trade.direction === "BUY" ? "default" : "destructive"}
                    >
                      {trade.direction}
                    </Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    ${trade.entry_price.toLocaleString()}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {trade.exit_price
                      ? `$${trade.exit_price.toLocaleString()}`
                      : "—"}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {trade.quantity.toFixed(4)}
                  </TableCell>
                  <TableCell>
                    {trade.pnl !== null ? (
                      <span
                        className={`font-mono text-xs ${
                          trade.pnl >= 0 ? "text-green-500" : "text-red-500"
                        }`}
                      >
                        ${trade.pnl.toFixed(2)} ({trade.pnl_pct?.toFixed(1)}%)
                      </span>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={trade.status === "OPEN" ? "default" : "secondary"}
                    >
                      {trade.status}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
              {trades.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-muted-foreground py-8">
                    No trades yet.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}
