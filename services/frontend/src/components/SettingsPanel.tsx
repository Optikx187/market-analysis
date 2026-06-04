import { useEffect, useState } from "react";
import { fetchCredentialStatus, type CredentialStatus } from "@/lib/api";

export default function SettingsPanel() {
  const [status, setStatus] = useState<CredentialStatus | null>(null);

  useEffect(() => {
    fetchCredentialStatus().then(setStatus).catch(() => {});
  }, []);

  return (
    <div className="rounded-lg border bg-[var(--card)] p-4">
      <h2 className="text-lg font-semibold mb-3">Settings & Credentials</h2>
      <p className="text-sm text-[var(--muted-foreground)] mb-4">
        Credentials are stored in the local <code>.env</code> file (never committed).
        Run <code>python3 scripts/setup.py</code> to configure or update.
      </p>

      {!status ? (
        <div className="text-sm text-[var(--muted-foreground)]">Loading credential status...</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          <StatusCard
            title="Robinhood"
            subtitle="Trade execution & capital guardrail"
            connected={status.robinhood}
          />
          <StatusCard
            title="Binance"
            subtitle="Crypto market data"
            connected={status.binance}
          />
          <StatusCard
            title="Alpaca"
            subtitle="Stock market data"
            connected={status.alpaca}
          />
          <StatusCard
            title="Telegram"
            subtitle="Signal notifications"
            connected={status.telegram}
          />
          <StatusCard
            title="Discord"
            subtitle="Signal notifications"
            connected={status.discord}
          />
        </div>
      )}

      <div className="mt-6 rounded border p-3 bg-[var(--secondary)]">
        <h3 className="text-sm font-semibold mb-2">Quick Setup</h3>
        <ol className="text-xs text-[var(--muted-foreground)] space-y-1.5 list-decimal list-inside">
          <li>Run <code className="bg-[var(--input)] px-1 rounded">python3 scripts/setup.py</code> from the project root</li>
          <li>Enter your API keys when prompted (press Enter to skip any)</li>
          <li>Restart services: <code className="bg-[var(--input)] px-1 rounded">docker-compose restart</code></li>
        </ol>
      </div>

      <div className="mt-4 rounded border p-3 bg-[var(--secondary)]">
        <h3 className="text-sm font-semibold mb-2">Getting Started with Tickers</h3>
        <p className="text-xs text-[var(--muted-foreground)]">
          Your watchlist starts empty. Use the Watchlist panel to add any tickers you want to track.
          Popular examples: <strong>BTC</strong> (Bitcoin), <strong>ETH</strong> (Ethereum),
          <strong> SPY</strong> (S&P 500 ETF), <strong>AAPL</strong> (Apple), <strong>TSLA</strong> (Tesla).
        </p>
      </div>
    </div>
  );
}

function StatusCard({
  title,
  subtitle,
  connected,
}: {
  title: string;
  subtitle: string;
  connected: boolean;
}) {
  return (
    <div className="rounded border p-3 flex items-start gap-3">
      <div className={`mt-0.5 h-2.5 w-2.5 rounded-full flex-shrink-0 ${
        connected ? "bg-green-500" : "bg-[var(--muted-foreground)]"
      }`} />
      <div>
        <div className="text-sm font-medium">{title}</div>
        <div className="text-xs text-[var(--muted-foreground)]">{subtitle}</div>
        <div className={`text-xs mt-1 ${connected ? "text-green-400" : "text-[var(--muted-foreground)]"}`}>
          {connected ? "Connected" : "Not configured"}
        </div>
      </div>
    </div>
  );
}
