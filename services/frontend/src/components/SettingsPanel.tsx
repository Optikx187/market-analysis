import { useEffect, useState } from "react";
import { fetchCredentialStatus, saveCredentials, type CredentialStatus } from "@/lib/api";

interface EditingService {
  name: string;
  fields: { key: string; label: string; type: string; placeholder: string }[];
}

const SERVICES: EditingService[] = [
  {
    name: "Robinhood",
    fields: [
      { key: "ROBINHOOD_USERNAME", label: "Username / Email", type: "text", placeholder: "your@email.com" },
      { key: "ROBINHOOD_PASSWORD", label: "Password", type: "password", placeholder: "Enter password" },
      { key: "ROBINHOOD_TOTP", label: "TOTP Secret (optional)", type: "password", placeholder: "2FA authenticator secret" },
    ],
  },
  {
    name: "Binance",
    fields: [
      { key: "BINANCE_API_KEY", label: "API Key", type: "text", placeholder: "Binance API key" },
      { key: "BINANCE_API_SECRET", label: "API Secret", type: "password", placeholder: "Binance API secret" },
    ],
  },
  {
    name: "Alpaca",
    fields: [
      { key: "ALPACA_API_KEY", label: "API Key", type: "text", placeholder: "Alpaca API key" },
      { key: "ALPACA_API_SECRET", label: "API Secret", type: "password", placeholder: "Alpaca API secret" },
    ],
  },
  {
    name: "Telegram",
    fields: [
      { key: "TELEGRAM_BOT_TOKEN", label: "Bot Token", type: "text", placeholder: "123456:ABC-DEF..." },
      { key: "TELEGRAM_CHAT_ID", label: "Chat ID", type: "text", placeholder: "-1001234567890" },
    ],
  },
  {
    name: "Discord",
    fields: [
      { key: "DISCORD_WEBHOOK_URL", label: "Webhook URL", type: "text", placeholder: "https://discord.com/api/webhooks/..." },
    ],
  },
];

const SERVICE_INFO: Record<string, { subtitle: string; help: string }> = {
  Robinhood: { subtitle: "Trade execution & capital guardrail", help: "Use your Robinhood login credentials. TOTP is your 2FA authenticator secret (optional)." },
  Binance: { subtitle: "Crypto market data", help: "Get API keys at binance.com/api-management. Enable 'Read Only' permissions." },
  Alpaca: { subtitle: "Stock market data", help: "Sign up at alpaca.markets (free). Go to API Keys in the dashboard." },
  Telegram: { subtitle: "Signal notifications", help: "Message @BotFather on Telegram, send /newbot to create a bot and get your token." },
  Discord: { subtitle: "Signal notifications", help: "Server Settings -> Integrations -> Webhooks -> New Webhook -> Copy URL." },
};

export default function SettingsPanel() {
  const [status, setStatus] = useState<CredentialStatus | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [formData, setFormData] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const loadStatus = () => fetchCredentialStatus().then(setStatus).catch(() => {});

  useEffect(() => { loadStatus(); }, []);

  const handleEdit = (serviceName: string) => {
    setEditing(serviceName);
    setFormData({});
    setMessage(null);
  };

  const handleSave = async (service: EditingService) => {
    const creds: Record<string, string> = {};
    for (const f of service.fields) {
      if (formData[f.key]) creds[f.key] = formData[f.key];
    }
    if (Object.keys(creds).length === 0) {
      setMessage({ type: "error", text: "Enter at least one field." });
      return;
    }
    setSaving(true);
    setMessage(null);
    try {
      const res = await saveCredentials(creds);
      setMessage({ type: "success", text: `Saved: ${res.saved.join(", ")}. Restart services to apply.` });
      setEditing(null);
      loadStatus();
    } catch {
      setMessage({ type: "error", text: "Failed to save. Check that services are running." });
    }
    setSaving(false);
  };

  return (
    <div className="rounded-lg border bg-[var(--card)] p-4">
      <h2 className="text-lg font-semibold mb-1">Settings & Credentials</h2>
      <p className="text-xs text-[var(--muted-foreground)] mb-4">
        Configure your API keys and service credentials below. All credentials are stored locally
        in the <code>.env</code> file and never leave your machine.
      </p>

      {message && (
        <div className={`mb-4 rounded border p-3 text-sm ${
          message.type === "success"
            ? "border-green-600 bg-green-600/10 text-green-400"
            : "border-red-600 bg-red-600/10 text-red-400"
        }`}>
          {message.text}
        </div>
      )}

      {!status ? (
        <div className="text-sm text-[var(--muted-foreground)]">Loading credential status...</div>
      ) : (
        <div className="space-y-3">
          {SERVICES.map((service) => {
            const key = service.name.toLowerCase() as keyof CredentialStatus;
            const connected = status[key] ?? false;
            const info = SERVICE_INFO[service.name];
            const isEditing = editing === service.name;

            return (
              <div key={service.name} className="rounded border p-4">
                <div className="flex items-start justify-between">
                  <div className="flex items-start gap-3">
                    <div className={`mt-1 h-2.5 w-2.5 rounded-full flex-shrink-0 ${
                      connected ? "bg-green-500" : "bg-[var(--muted-foreground)]"
                    }`} />
                    <div>
                      <div className="text-sm font-medium">{service.name}</div>
                      <div className="text-xs text-[var(--muted-foreground)]">{info.subtitle}</div>
                      <div className={`text-xs mt-0.5 ${connected ? "text-green-400" : "text-[var(--muted-foreground)]"}`}>
                        {connected ? "Connected" : "Not configured"}
                      </div>
                    </div>
                  </div>
                  {!isEditing && (
                    <button onClick={() => handleEdit(service.name)}
                      className="rounded bg-[var(--secondary)] px-3 py-1 text-xs hover:bg-[var(--accent)]">
                      {connected ? "Update" : "Configure"}
                    </button>
                  )}
                </div>

                {isEditing && (
                  <div className="mt-3 pt-3 border-t border-[var(--border)] space-y-3">
                    <div className="rounded bg-[var(--secondary)] p-2 text-xs text-[var(--muted-foreground)]">
                      {info.help}
                    </div>
                    {service.fields.map((field) => (
                      <div key={field.key}>
                        <label className="text-xs font-medium text-[var(--muted-foreground)] block mb-1">
                          {field.label}
                        </label>
                        <input
                          type={field.type}
                          value={formData[field.key] ?? ""}
                          onChange={(e) => setFormData({ ...formData, [field.key]: e.target.value })}
                          placeholder={field.placeholder}
                          className="w-full rounded border bg-[var(--input)] px-3 py-1.5 text-sm"
                        />
                      </div>
                    ))}
                    <div className="flex gap-2 justify-end">
                      <button onClick={() => { setEditing(null); setMessage(null); }}
                        className="text-xs text-[var(--muted-foreground)]">Cancel</button>
                      <button onClick={() => handleSave(service)} disabled={saving}
                        className="rounded bg-[var(--primary)] text-[var(--primary-foreground)] px-4 py-1.5 text-xs font-medium disabled:opacity-50">
                        {saving ? "Saving..." : "Save"}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <div className="mt-6 rounded border p-3 bg-[var(--secondary)]">
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
