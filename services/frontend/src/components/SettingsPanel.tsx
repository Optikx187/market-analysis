import { useEffect, useState } from "react";
import {
  fetchCredentialStatus,
  revealCredential,
  saveCredentials,
  fetchEnvSettings,
  updateEnvSetting,
  type CredentialStatus,
  type EnvSetting,
} from "@/lib/api";

interface EditingService {
  name: string;
  provider: string;
  fields: { key: string; label: string; type: string; placeholder: string }[];
}

const SERVICES: EditingService[] = [
  {
    name: "Binance",
    provider: "binance",
    fields: [
      { key: "BINANCE_API_KEY", label: "API Key", type: "text", placeholder: "Binance API key" },
      { key: "BINANCE_API_SECRET", label: "API Secret", type: "password", placeholder: "Binance API secret" },
    ],
  },
  {
    name: "Alpaca",
    provider: "alpaca",
    fields: [
      { key: "ALPACA_API_KEY", label: "API Key", type: "text", placeholder: "Alpaca API key" },
      { key: "ALPACA_API_SECRET", label: "API Secret", type: "password", placeholder: "Alpaca API secret" },
    ],
  },
  {
    name: "Telegram",
    provider: "telegram",
    fields: [
      { key: "TELEGRAM_BOT_TOKEN", label: "Bot Token", type: "password", placeholder: "123456:ABC-DEF..." },
      { key: "TELEGRAM_CHAT_ID", label: "Chat ID", type: "text", placeholder: "-1001234567890" },
    ],
  },
  {
    name: "Discord",
    provider: "discord",
    fields: [
      { key: "DISCORD_WEBHOOK_URL", label: "Webhook URL", type: "password", placeholder: "https://discord.com/api/webhooks/..." },
    ],
  },
];

const SERVICE_INFO: Record<string, { subtitle: string; help: string }> = {
  Binance: { subtitle: "Crypto market data", help: "Read-only API keys are recommended." },
  Alpaca: { subtitle: "Stock market data", help: "Use paper-trading/read-only credentials when possible." },
  Telegram: { subtitle: "Signal notifications", help: "Use bot token and chat ID." },
  Discord: { subtitle: "Signal notifications", help: "Paste the webhook URL." },
};

const isMasked = (value?: string) => Boolean(value && value.includes("•"));

export default function SettingsPanel() {
  const [status, setStatus] = useState<CredentialStatus | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [formData, setFormData] = useState<Record<string, string>>({});
  const [show, setShow] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  
  // Environment settings state
  const [envSettings, setEnvSettings] = useState<Record<string, EnvSetting> | null>(null);
  const [editingEnv, setEditingEnv] = useState<string | null>(null);
  const [envFormValues, setEnvFormValues] = useState<Record<string, string>>({});

  const loadStatus = () => fetchCredentialStatus().then(setStatus).catch(() => {});
  const loadEnvSettings = () => fetchEnvSettings().then(setEnvSettings).catch(() => {});

  useEffect(() => { loadStatus(); loadEnvSettings(); }, []);

  const handleEdit = (service: EditingService) => {
    const providerStatus = status?.[service.provider];
    const masked = providerStatus?.masked ?? {};
    setEditing(service.name);
    setFormData(Object.fromEntries(service.fields.map((f) => [f.key, masked[f.key] ?? ""])));
    setMessage(null);
  };

  const handleReveal = async (key: string) => {
    try {
      const res = await revealCredential(key);
      setFormData((p) => ({ ...p, [key]: res.value }));
      setShow((p) => ({ ...p, [key]: true }));
    } catch {
      setMessage({ type: "error", text: "Unable to reveal this credential." });
    }
  };

  const handleSave = async (service: EditingService) => {
    const creds: Record<string, string> = {};
    for (const f of service.fields) {
      const value = formData[f.key];
      if (value && !isMasked(value)) creds[f.key] = value;
    }
    if (Object.keys(creds).length === 0) {
      setMessage({ type: "error", text: "Enter or reveal at least one field before saving." });
      return;
    }
    setSaving(true);
    setMessage(null);
    try {
      const res = await saveCredentials(creds);
      setMessage({ type: "success", text: `${res.message}${res.skipped?.length ? ` Skipped existing verified: ${res.skipped.join(", ")}` : ""}` });
      setEditing(null);
      loadStatus();
    } catch {
      setMessage({ type: "error", text: "Failed to save or verify credentials." });
    }
    setSaving(false);
  };

  return (
    <div className="rounded-lg border bg-[var(--card)] p-4">
      <h2 className="text-lg font-semibold mb-1">Settings & Credentials</h2>
      <p className="text-xs text-[var(--muted-foreground)] mb-4">
        Credentials are stored locally, masked by default, and synced to the local <code>.env</code> after save or verification.
      </p>

      {message && (
        <div className={`mb-4 rounded border p-3 text-sm ${
          message.type === "success" ? "border-green-600 bg-green-600/10 text-green-400" : "border-red-600 bg-red-600/10 text-red-400"
        }`}>
          {message.text}
        </div>
      )}

      {!status ? (
        <div className="text-sm text-[var(--muted-foreground)]">Loading credential status...</div>
      ) : (
        <div className="space-y-3">
          {SERVICES.map((service) => {
            const providerStatus = status[service.provider];
            const connected = Boolean(providerStatus?.verified);
            const configured = Boolean(providerStatus?.configured);
            const info = SERVICE_INFO[service.name];
            const isEditing = editing === service.name;
            return (
              <div key={service.name} className="rounded border p-4">
                <div className="flex items-start justify-between">
                  <div className="flex items-start gap-3">
                    <div className={`mt-1 h-2.5 w-2.5 rounded-full flex-shrink-0 ${connected ? "bg-green-500" : configured ? "bg-yellow-500" : "bg-[var(--muted-foreground)]"}`} />
                    <div>
                      <div className="text-sm font-medium">{service.name}</div>
                      <div className="text-xs text-[var(--muted-foreground)]">{info.subtitle}</div>
                      <div className={`text-xs mt-0.5 ${connected ? "text-green-400" : configured ? "text-yellow-400" : "text-[var(--muted-foreground)]"}`}>
                        {connected ? "Verified" : configured ? "Configured  needs verification" : "Not configured"}
                      </div>
                    </div>
                  </div>
                  {!isEditing && (
                    <button onClick={() => handleEdit(service)}
                      className="rounded bg-[var(--secondary)] px-3 py-1 text-xs hover:bg-[var(--accent)]">
                      {configured ? "Review / Update" : "Configure"}
                    </button>
                  )}
                </div>

                {isEditing && (
                  <div className="mt-3 pt-3 border-t border-[var(--border)] space-y-3">
                    <div className="rounded bg-[var(--secondary)] p-2 text-xs text-[var(--muted-foreground)]">{info.help}</div>
                    {service.fields.map((field) => (
                      <div key={field.key}>
                        <label className="text-xs font-medium text-[var(--muted-foreground)] block mb-1">{field.label}</label>
                        <div className="flex gap-2">
                          <input
                            type={field.type === "password" && !show[field.key] ? "password" : "text"}
                            value={formData[field.key] ?? ""}
                            onChange={(e) => setFormData({ ...formData, [field.key]: e.target.value })}
                            placeholder={field.placeholder}
                            className="w-full rounded border bg-[var(--input)] px-3 py-1.5 text-sm"
                          />
                          {field.type === "password" && (
                            <button type="button" onClick={() => isMasked(formData[field.key]) ? handleReveal(field.key) : setShow((p) => ({ ...p, [field.key]: !p[field.key] }))}
                              className="rounded bg-[var(--secondary)] px-2 text-xs">
                              {show[field.key] ? "Hide" : "Show"}
                            </button>
                          )}
                        </div>
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

      {/* Environment Settings Section */}
      <div className="mt-6 pt-4 border-t border-[var(--border)]">
        <h3 className="text-sm font-medium mb-3">Environment Settings</h3>
        <p className="text-xs text-[var(--muted-foreground)] mb-3">
          Adjust risk parameters and simulation settings. These values are synced to your .env file.
        </p>
        {!envSettings ? (
          <div className="text-sm text-[var(--muted-foreground)]">Loading settings...</div>
        ) : (
          <div className="space-y-2">
            {Object.entries(envSettings).map(([key, setting]) => (
              <div key={key} className="flex items-center justify-between py-2 px-3 rounded bg-[var(--background)]">
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium">{key}</div>
                  <div className="text-xs text-[var(--muted-foreground)] truncate">{setting.description}</div>
                </div>
                <div className="flex items-center gap-3 ml-4">
                  <span className={`text-xs ${setting.value !== setting.default ? "text-yellow-400" : "text-[var(--muted-foreground)]"}`}>
                    Default: {setting.default}
                  </span>
                  {editingEnv === key ? (
                    <div className="flex items-center gap-2">
                      <input
                        type="number"
                        value={envFormValues[key] ?? setting.value}
                        onChange={(e) => setEnvFormValues({ ...envFormValues, [key]: e.target.value })}
                        className="w-24 rounded border bg-[var(--input)] px-2 py-1 text-xs"
                        step={setting.type === "float" ? "0.1" : "1"}
                      />
                      <button
                        onClick={async () => {
                          const val = parseFloat(envFormValues[key] ?? setting.value);
                          if (isNaN(val)) return;
                          try {
                            await updateEnvSetting(key, val);
                            setMessage({ type: "success", text: `${key} updated successfully` });
                            loadEnvSettings();
                          } catch (e: any) {
                            setMessage({ type: "error", text: e?.response?.data?.detail || "Failed to update" });
                          }
                          setEditingEnv(null);
                        }}
                        className="text-xs px-2 py-1 rounded bg-green-600 text-white"
                      >
                        Save
                      </button>
                      <button
                        onClick={() => setEditingEnv(null)}
                        className="text-xs text-[var(--muted-foreground)]"
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium w-16 text-right">{setting.value}</span>
                      <button
                        onClick={() => {
                          setEditingEnv(key);
                          setEnvFormValues({ ...envFormValues, [key]: setting.value.toString() });
                        }}
                        className="text-xs px-2 py-1 rounded bg-[var(--secondary)] hover:bg-[var(--accent)]"
                      >
                        Edit
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
