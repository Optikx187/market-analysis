import { useState } from "react";
import { saveCredentials, addAsset, lookupSymbol } from "@/lib/api";

interface Props {
  onComplete: () => void;
}

type Step = "welcome" | "market-data" | "notifications" | "watchlist" | "done";

const STEPS: Step[] = ["welcome", "market-data", "notifications", "watchlist", "done"];

export default function GettingStartedPanel({ onComplete }: Props) {
  const [step, setStep] = useState<Step>("welcome");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Market data
  const [binKey, setBinKey] = useState("");
  const [binSecret, setBinSecret] = useState("");
  const [alpKey, setAlpKey] = useState("");
  const [alpSecret, setAlpSecret] = useState("");

  // Notifications
  const [tgToken, setTgToken] = useState("");
  const [tgChat, setTgChat] = useState("");
  const [discordUrl, setDiscordUrl] = useState("");

  // Watchlist
  const [ticker, setTicker] = useState("");
  const [assetName, setAssetName] = useState("");
  const [assetType, setAssetType] = useState("crypto");
  const [addedTickers, setAddedTickers] = useState<string[]>([]);

  const stepIndex = STEPS.indexOf(step);

  const next = () => {
    const idx = STEPS.indexOf(step);
    if (idx < STEPS.length - 1) setStep(STEPS[idx + 1]);
  };

  const prev = () => {
    const idx = STEPS.indexOf(step);
    if (idx > 0) setStep(STEPS[idx - 1]);
  };

  const handleSaveCreds = async (creds: Record<string, string>, nextStep: Step) => {
    const filtered = Object.fromEntries(Object.entries(creds).filter(([, v]) => v));
    if (Object.keys(filtered).length === 0) {
      setStep(nextStep);
      return;
    }
    setSaving(true);
    setError("");
    setSuccess("");
    try {
      const res = await saveCredentials(filtered);
      setSuccess(`Saved: ${res.saved.join(", ")}`);
      setTimeout(() => {
        setSuccess("");
        setStep(nextStep);
      }, 1000);
    } catch {
      setError("Failed to save — check that the backend is running.");
    }
    setSaving(false);
  };

  const handleAddTicker = async () => {
    if (!ticker || !assetName) return;
    setError("");
    
    // Validate ticker exists via API before adding
    try {
      const lookup = await lookupSymbol(ticker, assetType);
      if (!lookup.recognized) {
        setError(`Ticker "${ticker}" not found on ${assetType === "crypto" ? "Binance" : "market data provider"}. Please check the symbol and try again.`);
        return;
      }
    } catch (e: any) {
      setError(`Unable to validate ticker: ${e?.response?.data?.detail || "API unavailable. Please check your market data API credentials and try again."}`);
      return;
    }
    
    try {
      await addAsset(ticker, assetName, assetType);
      setAddedTickers([...addedTickers, ticker.toUpperCase()]);
      setTicker("");
      setAssetName("");
    } catch {
      setError("Failed to add ticker.");
    }
  };

  return (
    <div className="min-h-screen bg-[var(--background)] text-[var(--foreground)] flex items-center justify-center p-4">
      <div className="w-full max-w-2xl">
        <div className="rounded-lg border bg-[var(--card)] p-6 shadow-lg">
          {/* Progress bar */}
          <div className="flex gap-1 mb-6">
            {STEPS.map((s, i) => (
              <div
                key={s}
                className={`h-1.5 flex-1 rounded-full transition-colors ${
                  i <= stepIndex ? "bg-[var(--primary)]" : "bg-[var(--secondary)]"
                }`}
              />
            ))}
          </div>

          {error && (
            <div className="mb-4 rounded border border-red-600 bg-red-600/10 p-3 text-sm text-red-400">
              {error}
            </div>
          )}
          {success && (
            <div className="mb-4 rounded border border-green-600 bg-green-600/10 p-3 text-sm text-green-400">
              {success}
            </div>
          )}

          {/* Step: Welcome */}
          {step === "welcome" && (
            <div className="text-center space-y-4">
              <div className="text-4xl">📊</div>
              <h1 className="text-2xl font-bold">Welcome to Market Analysis</h1>
              <p className="text-[var(--muted-foreground)] max-w-md mx-auto">
                This wizard will help you set up your market analysis platform in a few minutes.
                We&apos;ll connect market data sources and notification services for tracking stocks and crypto.
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-6 text-left">
                <InfoCard
                  icon="📈"
                  title="Market Data"
                  desc="Binance (crypto) & Alpaca (stocks)"
                />
                <InfoCard
                  icon="�"
                  title="Watchlist"
                  desc="Track your favorite tickers"
                />
                <InfoCard
                  icon="🔔"
                  title="Alerts"
                  desc="Telegram & Discord notifications"
                />
              </div>
              <p className="text-xs text-[var(--muted-foreground)]">
                You can skip any step and set it up later from Settings.
              </p>
            </div>
          )}

          {/* Step: Market Data */}
          {step === "market-data" && (
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                <span className="text-2xl">📈</span>
                <div>
                  <h2 className="text-lg font-semibold">Market Data APIs</h2>
                  <p className="text-xs text-[var(--muted-foreground)]">
                    Connect data providers for real-time prices and historical data.
                  </p>
                </div>
              </div>
              <h3 className="text-sm font-semibold mt-2">Binance (Crypto)</h3>
              <CredField label="API Key" value={binKey} onChange={setBinKey} placeholder="Binance API key" />
              <CredField label="Secret" value={binSecret} onChange={setBinSecret} placeholder="Binance API secret" type="password" />
              <HelpBox>
                <p>Get your Binance API keys at{" "}
                  <a href="https://www.binance.com/en/my/settings/api-management" target="_blank" rel="noreferrer"
                    className="text-[var(--primary)] underline">binance.com/api-management</a>.
                  Enable &quot;Read Only&quot; permissions — no trading permissions needed.
                </p>
              </HelpBox>

              <h3 className="text-sm font-semibold mt-4">Alpaca (Stocks)</h3>
              <CredField label="API Key" value={alpKey} onChange={setAlpKey} placeholder="Alpaca API key" />
              <CredField label="Secret" value={alpSecret} onChange={setAlpSecret} placeholder="Alpaca API secret" type="password" />
              <HelpBox>
                <p>Sign up at{" "}
                  <a href="https://app.alpaca.markets/signup" target="_blank" rel="noreferrer"
                    className="text-[var(--primary)] underline">alpaca.markets</a>
                  {" "}(free paper trading account). Go to API Keys in the dashboard.
                </p>
              </HelpBox>
            </div>
          )}

          {/* Step: Notifications */}
          {step === "notifications" && (
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                <span className="text-2xl">🔔</span>
                <div>
                  <h2 className="text-lg font-semibold">Notification Channels</h2>
                  <p className="text-xs text-[var(--muted-foreground)]">
                    Get instant alerts when trading signals are generated.
                  </p>
                </div>
              </div>
              <h3 className="text-sm font-semibold mt-2">Telegram</h3>
              <CredField label="Bot Token" value={tgToken} onChange={setTgToken} placeholder="123456:ABC-DEF..." />
              <CredField label="Chat ID" value={tgChat} onChange={setTgChat} placeholder="-1001234567890" />
              <HelpBox>
                <p>1. Message <a href="https://t.me/BotFather" target="_blank" rel="noreferrer" className="text-[var(--primary)] underline">@BotFather</a> on Telegram and send <code>/newbot</code><br />
                2. Copy the bot token<br />
                3. Add the bot to your group, then use <a href="https://t.me/userinfobot" target="_blank" rel="noreferrer" className="text-[var(--primary)] underline">@userinfobot</a> to get your chat ID</p>
              </HelpBox>

              <h3 className="text-sm font-semibold mt-4">Discord</h3>
              <CredField label="Webhook URL" value={discordUrl} onChange={setDiscordUrl} placeholder="https://discord.com/api/webhooks/..." />
              <HelpBox>
                <p>In Discord: Server Settings → Integrations → Webhooks → New Webhook → Copy URL</p>
              </HelpBox>
            </div>
          )}

          {/* Step: Watchlist */}
          {step === "watchlist" && (
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                <span className="text-2xl">👀</span>
                <div>
                  <h2 className="text-lg font-semibold">Add Your First Tickers</h2>
                  <p className="text-xs text-[var(--muted-foreground)]">
                    Add assets you want the system to monitor and generate signals for.
                  </p>
                </div>
              </div>

              <div className="flex gap-2">
                <input
                  className="w-24 rounded border bg-[var(--input)] px-2 py-1.5 text-sm"
                  placeholder="Ticker" value={ticker}
                  onChange={(e) => setTicker(e.target.value.toUpperCase())}
                  onKeyDown={(e) => e.key === "Enter" && handleAddTicker()}
                />
                <input
                  className="flex-1 rounded border bg-[var(--input)] px-2 py-1.5 text-sm"
                  placeholder="Name" value={assetName}
                  onChange={(e) => setAssetName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleAddTicker()}
                />
                <select className="rounded border bg-[var(--input)] px-2 py-1.5 text-sm"
                  value={assetType} onChange={(e) => setAssetType(e.target.value)}>
                  <option value="crypto">Crypto</option>
                  <option value="stock">Stock</option>
                </select>
                <button onClick={handleAddTicker} disabled={!ticker || !assetName}
                  className="rounded bg-[var(--primary)] text-[var(--primary-foreground)] px-3 py-1.5 text-sm disabled:opacity-50">
                  + Add
                </button>
              </div>

              {addedTickers.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {addedTickers.map((t) => (
                    <span key={t} className="rounded bg-[var(--secondary)] px-2 py-1 text-xs font-medium">{t}</span>
                  ))}
                </div>
              )}

              <div className="rounded border border-dashed p-4">
                <p className="text-sm font-medium mb-2">Popular examples to get started:</p>
                <div className="grid grid-cols-2 gap-2 text-xs text-[var(--muted-foreground)]">
                  <QuickAdd label="BTC" desc="Bitcoin" type="crypto" onAdd={() => { addAsset("BTC", "Bitcoin", "crypto").then(() => setAddedTickers(p => [...p, "BTC"])).catch(() => {}); }} />
                  <QuickAdd label="ETH" desc="Ethereum" type="crypto" onAdd={() => { addAsset("ETH", "Ethereum", "crypto").then(() => setAddedTickers(p => [...p, "ETH"])).catch(() => {}); }} />
                  <QuickAdd label="SPY" desc="S&P 500 ETF" type="stock" onAdd={() => { addAsset("SPY", "S&P 500 ETF", "stock").then(() => setAddedTickers(p => [...p, "SPY"])).catch(() => {}); }} />
                  <QuickAdd label="AAPL" desc="Apple Inc." type="stock" onAdd={() => { addAsset("AAPL", "Apple Inc.", "stock").then(() => setAddedTickers(p => [...p, "AAPL"])).catch(() => {}); }} />
                </div>
              </div>
            </div>
          )}

          {/* Step: Done */}
          {step === "done" && (
            <div className="text-center space-y-4">
              <div className="text-4xl">🎉</div>
              <h2 className="text-2xl font-bold">You&apos;re All Set!</h2>
              <p className="text-[var(--muted-foreground)] max-w-md mx-auto">
                Your trading platform is configured. The system will monitor your watchlist,
                generate signals using quantitative analysis, and alert you when opportunities arise.
              </p>
              <div className="rounded border p-4 bg-[var(--secondary)] text-left text-sm space-y-2">
                <p><strong>What happens next:</strong></p>
                <ul className="list-disc list-inside text-[var(--muted-foreground)] space-y-1 text-xs">
                  <li>The system fetches historical data for your tickers (up to 365 days)</li>
                  <li>Quantitative analysis runs continuously (EMA, RSI, ATR indicators)</li>
                  <li>Buy/sell signals are generated only when strict criteria are met</li>
                  <li>Paper trades execute automatically to track performance</li>
                  <li>Alerts are sent to your configured notification channels</li>
                </ul>
              </div>
              <p className="text-xs text-[var(--muted-foreground)]">
                You can always update settings and add more tickers from the dashboard.
                Check the <strong>Help</strong> tab for a complete guide.
              </p>
            </div>
          )}

          {/* Navigation */}
          <div className="flex justify-between mt-6 pt-4 border-t border-[var(--border)]">
            {stepIndex > 0 ? (
              <button onClick={prev} className="text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)]">
                ← Back
              </button>
            ) : <div />}

            {step === "welcome" && (
              <button onClick={next}
                className="rounded bg-[var(--primary)] text-[var(--primary-foreground)] px-6 py-2 text-sm font-medium">
                Get Started →
              </button>
            )}
            {step === "market-data" && (
              <div className="flex gap-2">
                <button onClick={() => setStep("notifications")} className="text-sm text-[var(--muted-foreground)]">Skip</button>
                <button onClick={() => handleSaveCreds({
                  BINANCE_API_KEY: binKey, BINANCE_API_SECRET: binSecret,
                  ALPACA_API_KEY: alpKey, ALPACA_API_SECRET: alpSecret,
                }, "notifications")} disabled={saving}
                  className="rounded bg-[var(--primary)] text-[var(--primary-foreground)] px-4 py-2 text-sm font-medium disabled:opacity-50">
                  {saving ? "Saving..." : "Save & Continue →"}
                </button>
              </div>
            )}
            {step === "notifications" && (
              <div className="flex gap-2">
                <button onClick={() => setStep("watchlist")} className="text-sm text-[var(--muted-foreground)]">Skip</button>
                <button onClick={() => handleSaveCreds({
                  TELEGRAM_BOT_TOKEN: tgToken, TELEGRAM_CHAT_ID: tgChat,
                  DISCORD_WEBHOOK_URL: discordUrl,
                }, "watchlist")} disabled={saving}
                  className="rounded bg-[var(--primary)] text-[var(--primary-foreground)] px-4 py-2 text-sm font-medium disabled:opacity-50">
                  {saving ? "Saving..." : "Save & Continue →"}
                </button>
              </div>
            )}
            {step === "watchlist" && (
              <button onClick={next}
                className="rounded bg-[var(--primary)] text-[var(--primary-foreground)] px-6 py-2 text-sm font-medium">
                {addedTickers.length > 0 ? "Continue →" : "Skip for Now →"}
              </button>
            )}
            {step === "done" && (
              <button onClick={onComplete}
                className="rounded bg-[var(--primary)] text-[var(--primary-foreground)] px-6 py-2 text-sm font-medium">
                Go to Dashboard →
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function InfoCard({ icon, title, desc }: { icon: string; title: string; desc: string }) {
  return (
    <div className="rounded border p-3 bg-[var(--secondary)]">
      <div className="text-xl mb-1">{icon}</div>
      <div className="text-sm font-medium">{title}</div>
      <div className="text-xs text-[var(--muted-foreground)]">{desc}</div>
    </div>
  );
}

function CredField({
  label, value, onChange, placeholder, type = "text",
}: {
  label: string; value: string; onChange: (v: string) => void;
  placeholder: string; type?: string;
}) {
  return (
    <div>
      <label className="text-xs font-medium text-[var(--muted-foreground)] block mb-1">{label}</label>
      <input
        type={type} value={value} onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded border bg-[var(--input)] px-3 py-1.5 text-sm"
      />
    </div>
  );
}

function HelpBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded bg-[var(--secondary)] p-3 text-xs text-[var(--muted-foreground)]">
      {children}
    </div>
  );
}

function QuickAdd({ label, desc, onAdd }: { label: string; desc: string; type: string; onAdd: () => void }) {
  return (
    <button onClick={onAdd}
      className="flex items-center gap-2 rounded border p-2 text-left hover:bg-[var(--accent)] transition-colors">
      <span className="font-medium text-[var(--foreground)]">{label}</span>
      <span>{desc}</span>
    </button>
  );
}
