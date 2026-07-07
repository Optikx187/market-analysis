import { useEffect, useState } from "react";
import {
  fetchScannerStatus,
  updateScannerConfig,
  triggerScan,
  type ScannerStatus,
  type ScanResult,
} from "@/lib/api";

export default function ScannerPanel() {
  const [status, setStatus] = useState<ScannerStatus | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [msg, setMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const load = () => fetchScannerStatus().then(setStatus).catch(() => {});

  useEffect(() => {
    load();
    const interval = setInterval(load, 15000);
    return () => clearInterval(interval);
  }, []);

  const handleScanNow = async () => {
    setScanning(true);
    setMsg(null);
    try {
      const result = await triggerScan();
      setScanResult(result);
      setMsg({ type: "success", text: `Scan complete: ${result.scanned} scanned, ${result.signals_found} signals, ${result.notifications_sent} notifications sent` });
      load();
    } catch {
      setMsg({ type: "error", text: "Scan failed. Check that services are running." });
    }
    setScanning(false);
  };

  const handleToggle = async () => {
    if (!status) return;
    try {
      await updateScannerConfig({ enabled: !status.enabled });
      load();
    } catch {
      setMsg({ type: "error", text: "Failed to update scanner config" });
    }
  };

  const handleIntervalChange = async (mins: number) => {
    try {
      await updateScannerConfig({ interval_minutes: mins });
      load();
    } catch {
      setMsg({ type: "error", text: "Failed to update interval" });
    }
  };

  const handleMarketHoursToggle = async () => {
    if (!status) return;
    try {
      await updateScannerConfig({ market_hours_only: !status.market_hours_only });
      load();
    } catch {
      setMsg({ type: "error", text: "Failed to update market hours setting" });
    }
  };

  return (
    <div className="rounded-lg border bg-[var(--card)] p-4">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-semibold">Auto-Scanner</h3>
          <p className="text-xs text-[var(--muted-foreground)]">
            Automatically scan all watchlist tickers for signals
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleScanNow}
            disabled={scanning}
            className="rounded bg-[var(--primary)] text-[var(--primary-foreground)] px-3 py-1.5 text-xs font-medium disabled:opacity-50"
          >
            {scanning ? "Scanning..." : "Scan Now"}
          </button>
        </div>
      </div>

      {msg && (
        <div className={`mb-3 rounded border p-2 text-xs ${
          msg.type === "success" ? "border-green-600 bg-green-600/10 text-green-400" : "border-red-600 bg-red-600/10 text-red-400"
        }`}>
          {msg.text}
        </div>
      )}

      {status && (
        <div className="space-y-3">
          <div className="flex items-center justify-between py-2 px-3 rounded bg-[var(--background)]">
            <div className="flex items-center gap-2">
              <div className={`h-2 w-2 rounded-full ${status.enabled ? "bg-green-500 animate-pulse" : "bg-[var(--muted-foreground)]"}`} />
              <span className="text-sm">Auto-Scan</span>
            </div>
            <button
              onClick={handleToggle}
              className={`px-3 py-1 text-xs rounded ${
                status.enabled ? "bg-green-600 text-white" : "bg-[var(--secondary)] text-[var(--muted-foreground)]"
              }`}
            >
              {status.enabled ? "Enabled" : "Disabled"}
            </button>
          </div>

          <div className="flex items-center justify-between py-2 px-3 rounded bg-[var(--background)]">
            <span className="text-sm">Scan Interval</span>
            <div className="flex gap-1">
              {[5, 15, 30, 60].map((m) => (
                <button
                  key={m}
                  onClick={() => handleIntervalChange(m)}
                  className={`px-2 py-0.5 text-xs rounded ${
                    status.interval_minutes === m
                      ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                      : "bg-[var(--secondary)] text-[var(--muted-foreground)]"
                  }`}
                >
                  {m}m
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center justify-between py-2 px-3 rounded bg-[var(--background)]">
            <span className="text-sm">Market Hours Only</span>
            <button
              onClick={handleMarketHoursToggle}
              className={`px-3 py-1 text-xs rounded ${
                status.market_hours_only ? "bg-green-600 text-white" : "bg-[var(--secondary)] text-[var(--muted-foreground)]"
              }`}
            >
              {status.market_hours_only ? "Yes" : "No (24/7)"}
            </button>
          </div>

          <div className="grid grid-cols-3 gap-2 text-center">
            <div className="rounded bg-[var(--background)] p-2">
              <div className="text-xs text-[var(--muted-foreground)]">Total Scans</div>
              <div className="text-sm font-medium">{status.total_scans}</div>
            </div>
            <div className="rounded bg-[var(--background)] p-2">
              <div className="text-xs text-[var(--muted-foreground)]">Signals Found</div>
              <div className="text-sm font-medium">{status.total_signals_found}</div>
            </div>
            <div className="rounded bg-[var(--background)] p-2">
              <div className="text-xs text-[var(--muted-foreground)]">Last Scan</div>
              <div className="text-sm font-medium">
                {status.last_scan_at ? new Date(status.last_scan_at).toLocaleTimeString() : "Never"}
              </div>
            </div>
          </div>
        </div>
      )}

      {scanResult && scanResult.signals.length > 0 && (
        <div className="mt-3 pt-3 border-t border-[var(--border)]">
          <div className="text-xs font-medium text-[var(--muted-foreground)] mb-2">Latest Scan Signals</div>
          <div className="space-y-1">
            {scanResult.signals.map((s, i) => (
              <div key={i} className="flex items-center justify-between py-1 px-2 rounded bg-[var(--background)] text-xs">
                <span className="font-medium">{s.ticker}</span>
                <span className={s.direction === "BUY" ? "text-green-400" : "text-red-400"}>{s.direction}</span>
                <span className={s.approved ? "text-green-400" : "text-yellow-400"}>
                  {s.approved ? "Approved" : s.suppressed ? "Suppressed" : "Rejected"}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
