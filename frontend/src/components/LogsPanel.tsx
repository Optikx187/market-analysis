import { useState, useEffect } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Terminal } from "lucide-react";
import type { SystemLog } from "@/lib/api";
import { fetchLogs } from "@/lib/api";

export default function LogsPanel() {
  const [logs, setLogs] = useState<SystemLog[]>([]);

  useEffect(() => {
    const load = async () => {
      try {
        setLogs(await fetchLogs());
      } catch {
        /* empty */
      }
    };
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  const levelColor = (level: string) => {
    switch (level) {
      case "ERROR":
        return "destructive";
      case "WARNING":
        return "outline";
      default:
        return "secondary";
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Terminal className="h-5 w-5" />
          System Logs
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ScrollArea className="h-64">
          <div className="space-y-1">
            {logs.map((log) => (
              <div
                key={log.id}
                className="flex items-start gap-2 py-1.5 px-2 rounded text-xs border-b border-border/50"
              >
                <Badge
                  variant={levelColor(log.level) as "default" | "destructive" | "outline" | "secondary"}
                  className="text-[10px] shrink-0"
                >
                  {log.level}
                </Badge>
                <span className="text-muted-foreground shrink-0">
                  {log.created_at
                    ? new Date(log.created_at).toLocaleTimeString()
                    : ""}
                </span>
                <span className="break-all">{log.message}</span>
              </div>
            ))}
            {logs.length === 0 && (
              <p className="text-sm text-muted-foreground text-center py-4">
                No logs yet.
              </p>
            )}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}
