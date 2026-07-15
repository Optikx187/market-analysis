import { useEffect, useState } from "react";
import {
  fetchCredentialStatus,
  revealCredential,
  saveCredentials,
  fetchEnvSettings,
  updateEnvSetting,
  testNotifications,
  fetchChannelStatus,
  toggleChannel,
  fetchSystemStatus,
  type CredentialStatus,
  type EnvSetting,
  type ChannelStatus,
  type SystemStatus,
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
  {
    name: "Slack",
    provider: "slack",
    fields: [
      { key: "SLACK_WEBHOOK_URL", label: "Webhook URL", type: "password", placeholder: "https://hooks.slack.com/services/..." },
    ],
  },
  {
    name: "Email (SMTP)",
    provider: "email",
    fields: [
      { key: "SMTP_HOST", label: "SMTP Host", type: "text", placeholder: "smtp.gmail.com" },
      { key: "SMTP_USER", label: "SMTP User", type: "text", placeholder: "you@gmail.com" },
      { key: "SMTP_PASSWORD", label: "SMTP Password", type: "password", placeholder: "app password" },
      { key: "EMAIL_TO", label: "Send To", type: "text", placeholder: "you@gmail.com" },
    ],
  },
  {
    name: "SMS (Twilio)",
    provider: "sms",
    fields: [
      { key: "TWILIO_ACCOUNT_SID", label: "Account SID", type: "text", placeholder: "ACxxxxxxxx" },
      { key: "TWILIO_AUTH_TOKEN", label: "Auth Token", type: "password", placeholder: "auth token" },
      { key: "TWILIO_FROM_NUMBER", label: "From Number", type: "text", placeholder: "+1234567890" },
      { key: "SMS_TO_NUMBER", label: "To Number", type: "text", placeholder: "+1234567890" },
    ],
  },
];

const SERVICE_INFO: Record<string, { subtitle: string; help: string }> = {
  Binance: { subtitle: "Crypto market data", help: "Read-only API keys are recommended." },
  Alpaca: { subtitle: "Stock market data", help: "Use paper-trading/read-only credentials when possible." },
  Telegram: { subtitle: "Signal notifications", help: "Use bot token and chat ID." },
  Discord: { subtitle: "Signal notifications", help: "Paste the webhook URL." },
  Slack: { subtitle: "Signal notifications", help: "Create an incoming webhook in your Slack workspace settings." },
  "Email (SMTP)": { subtitle: "Email notifications", help: "Use an app password for Gmail. Port 587 with TLS is default." },
  "SMS (Twilio)": { subtitle: "SMS notifications", help: "Get credentials from twilio.com/console." },
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

  // Channel toggle state
  const [channels, setChannels] = useState<Record<string, ChannelStatus> | null>(null);

  // System health state
  const [sysStatus, setSysStatus] = useState<SystemStatus | null>(null);


  // Test notification state
  const [testingSending, setTestingSending] = useState(false);
  const [testResult, setTestResult] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // System health refresh state
  const [healthRefreshing, setHealthRefreshing] = useState(false);
  const [healthLastUpdated, setHealthLastUpdated] = useState<Date | null>(null);

  const loadStatus = () => fetchCredentialStatus().then(setStatus).catch(() => {});
  const loadEnvSettings = () => fetchEnvSettings().then(setEnvSettings).catch(() => {});
  const loadChannels = () => fetchChannelStatus().then(setChannels).catch(() => {});
  const loadSysStatus = () => fetchSystemStatus().then((d) => { setSysStatus(d); return true as const; }).catch(() => false as const);


  useEffect(() => {
    loadStatus(); loadEnvSettings(); loadChannels();
    loadSysStatus().then((ok) => { if (ok) setHealthLastUpdated(new Date()); });
    const interval = setInterval(() => {
      loadSysStatus().then((ok) => { if (ok) setHealthLastUpdated(new Date()); });
    }, 15000);
    return () => clearInterval(interval);
  }, []);

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

      {/* Test Notifications Section */}
      <div className="mt-6 pt-4 border-t border-[var(--border)]">
        <h3 className="text-sm font-medium mb-2">Test Notifications</h3>
        <p className="text-xs text-[var(--muted-foreground)] mb-3">
          Send a test message to verify your Discord and Telegram channels are working.
        </p>
        <button
          onClick={async () => {
            setTestResult(null);
            setTestingSending(true);
            try {
              const res = await testNotifications();
              if (res.success) {
                setTestResult({ type: "success", text: res.message });
              } else {
                const details: string[] = [];
                if (res.results.telegram.configured && !res.results.telegram.sent) details.push("Telegram failed");
                if (res.results.discord.configured && !res.results.discord.sent) details.push("Discord failed");
                if (!res.results.telegram.configured && !res.results.discord.configured) details.push("No channels configured");
                setTestResult({ type: "error", text: `${res.message} ${details.join(", ")}` });
              }
            } catch {
              setTestResult({ type: "error", text: "Failed to send test notification. Check that the notification service is running." });
            } finally {
              setTestingSending(false);
            }
          }}
          disabled={testingSending}
          className={`rounded px-4 py-2 text-sm font-medium transition-colors ${
            testingSending
              ? "bg-[var(--primary)] text-[var(--primary-foreground)] opacity-70 cursor-wait"
              : "bg-[var(--secondary)] hover:bg-[var(--accent)]"
          }`}
        >
          {testingSending ? "Sending..." : "Send Test Notification"}
        </button>
        {testResult && (
          <div className={`mt-2 rounded border p-2 text-xs ${
            testResult.type === "success" ? "border-green-600 bg-green-600/10 text-green-400" : "border-red-600 bg-red-600/10 text-red-400"
          }`}>
            {testResult.text}
          </div>
        )}
      </div>

      {/* Notification Channel Toggles */}
      {channels && (
        <div className="mt-6 pt-4 border-t border-[var(--border)]">
          <h3 className="text-sm font-medium mb-2">Notification Channels</h3>
          <p className="text-xs text-[var(--muted-foreground)] mb-3">
            Enable or disable individual notification channels. Toggles are runtime-only and reset on service restart.
          </p>
          <div className="space-y-2">
            {Object.entries(channels).map(([name, ch]) => (
              <div key={name} className="flex items-center justify-between py-2 px-3 rounded bg-[var(--background)]">
                <div className="flex items-center gap-2">
                  <div className={`h-2 w-2 rounded-full ${ch.configured && ch.enabled ? "bg-green-500" : ch.configured ? "bg-yellow-500" : "bg-[var(--muted-foreground)]"}`} />
                  <span className="text-sm font-medium capitalize">{name}</span>
                  {!ch.configured && <span className="text-xs text-[var(--muted-foreground)]">(not configured)</span>}
                </div>
                <button
                  onClick={async () => {
                    try {
                      await toggleChannel(name, !ch.enabled);
                      loadChannels();
                      setMessage({ type: "success", text: `${name} ${!ch.enabled ? "enabled" : "disabled"}` });
                    } catch {
                      setMessage({ type: "error", text: `Failed to toggle ${name}` });
                    }
                  }}
                  disabled={!ch.configured}
                  className={`px-3 py-1 text-xs rounded transition-colors ${
                    ch.enabled
                      ? "bg-green-600 text-white hover:bg-green-700"
                      : "bg-[var(--secondary)] text-[var(--muted-foreground)] hover:bg-[var(--accent)]"
                  } disabled:opacity-40 disabled:cursor-not-allowed`}
                >
                  {ch.enabled ? "Enabled" : "Disabled"}
                </button>
              </div>
            ))}
          </div>
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

      {/* System Health — Issue #12 */}
      <div className="mt-6 pt-4 border-t border-[var(--border)]">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-medium">System Health</h3>
          <div className="flex items-center gap-2">
            {healthLastUpdated && (
              <span className="text-[10px] text-[var(--muted-foreground)]">
                Updated {healthLastUpdated.toLocaleTimeString()}
              </span>
            )}
            <button
              onClick={async () => {
                setHealthRefreshing(true);
                const ok = await loadSysStatus();
                if (ok) setHealthLastUpdated(new Date());
                setHealthRefreshing(false);
              }}
              disabled={healthRefreshing}
              className="rounded bg-[var(--secondary)] px-2 py-0.5 text-[10px] hover:bg-[var(--accent)] disabled:opacity-50"
            >
              {healthRefreshing ? "Refreshing..." : "Refresh"}
            </button>
          </div>
        </div>
        <p className="text-xs text-[var(--muted-foreground)] mb-3">
          Live connectivity status for external data providers. Auto-refreshes every 15s.
        </p>
        {!sysStatus ? (
          <div className="text-sm text-[var(--muted-foreground)]">Loading system status...</div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-2 mb-3">
              <div className="rounded bg-[var(--background)] p-3">
                <div className="text-xs text-[var(--muted-foreground)]">Service Started</div>
                <div className="text-sm font-medium">{new Date(sysStatus.started_at).toLocaleString()}</div>
              </div>
              <div className="rounded bg-[var(--background)] p-3">
                <div className="text-xs text-[var(--muted-foreground)]">Uptime</div>
                <div className="text-sm font-medium">
                  {(() => {
                    const ms = new Date(sysStatus.current_time).getTime() - new Date(sysStatus.started_at).getTime();
                    const hrs = Math.floor(ms / 3600000);
                    const mins = Math.floor((ms % 3600000) / 60000);
                    return `${hrs}h ${mins}m`;
                  })()}
                </div>
              </div>
            </div>
            {sysStatus.connectivity && (
              <div className="space-y-2 mb-3">
                {Object.entries(sysStatus.connectivity).map(([provider, conn]) => (
                  <div key={provider} className="flex items-center justify-between py-2 px-3 rounded bg-[var(--background)]">
                    <div className="flex items-center gap-2">
                      <div className={`h-2 w-2 rounded-full ${conn.online ? "bg-green-500" : "bg-red-500"}`} />
                      <span className="text-sm font-medium capitalize">{provider}</span>
                    </div>
                    <div className="text-xs text-[var(--muted-foreground)]">
                      {conn.online ? "Online" : "Offline"}
                      {conn.last_checked && ` · checked ${new Date(conn.last_checked).toLocaleTimeString()}`}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {sysStatus.downtime_log && sysStatus.downtime_log.length > 0 && (
              <div className="mt-2">
                <div className="text-xs font-medium text-[var(--muted-foreground)] mb-1">Recent Downtime Events</div>
                <div className="space-y-1">
                  {sysStatus.downtime_log.slice(-5).map((d, i) => (
                    <div key={i} className="text-xs py-1 px-2 rounded bg-[var(--background)] flex justify-between">
                      <span className="capitalize">{d.provider}</span>
                      <span className="text-[var(--muted-foreground)]">
                        {new Date(d.went_offline).toLocaleTimeString()} → {new Date(d.came_online).toLocaleTimeString()}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Telegram trade replies moved to Portfolio panel */}
    </div>
  );
}
