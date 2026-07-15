import { useState } from "react";

type Section = "overview" | "watchlist" | "signals" | "portfolio" | "risk" | "scanner" | "settings" | "glossary";

const SECTIONS: { id: Section; title: string; icon: string }[] = [
  { id: "overview", title: "How It Works", icon: "📊" },
  { id: "watchlist", title: "Watchlist", icon: "👀" },
  { id: "signals", title: "Trading Signals", icon: "🚦" },
  { id: "portfolio", title: "Portfolio & Trades", icon: "💼" },
  { id: "risk", title: "Risk Management", icon: "🛡️" },
  { id: "scanner", title: "Scanner & Tools", icon: "🔍" },
  { id: "settings", title: "Configuration", icon: "⚙️" },
  { id: "glossary", title: "Glossary", icon: "📖" },
];

export default function HelpPanel() {
  const [active, setActive] = useState<Section>("overview");

  return (
    <div className="rounded-lg border bg-[var(--card)] p-4">
      <h2 className="text-lg font-semibold mb-3">Help & Documentation</h2>

      <div className="flex flex-wrap gap-1.5 mb-4">
        {SECTIONS.map((s) => (
          <button
            key={s.id}
            onClick={() => setActive(s.id)}
            className={`px-3 py-1.5 text-xs rounded-full transition-colors ${
              active === s.id
                ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                : "bg-[var(--secondary)] text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
            }`}
          >
            {s.icon} {s.title}
          </button>
        ))}
      </div>

      <div className="prose-sm">
        {active === "overview" && <OverviewSection />}
        {active === "watchlist" && <WatchlistSection />}
        {active === "signals" && <SignalsSection />}
        {active === "portfolio" && <PortfolioSection />}
        {active === "risk" && <RiskSection />}
        {active === "scanner" && <ScannerToolsSection />}
        {active === "settings" && <SettingsSection />}
        {active === "glossary" && <GlossarySection />}
      </div>
    </div>
  );
}

function DocSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <h3 className="text-base font-semibold">{title}</h3>
      <div className="text-sm text-[var(--muted-foreground)] space-y-3">{children}</div>
    </div>
  );
}

function Tip({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded border border-blue-600/30 bg-blue-600/10 p-3 text-xs">
      <strong className="text-blue-400">Tip:</strong>{" "}
      <span className="text-[var(--muted-foreground)]">{children}</span>
    </div>
  );
}

function OverviewSection() {
  return (
    <DocSection title="How the Platform Works">
      <p>
        This platform monitors financial markets and generates trading signals using
        <strong> quantitative analysis</strong> — math-based rules that remove emotion from trading decisions.
      </p>
      <div className="rounded border p-4 space-y-3">
        <h4 className="text-sm font-semibold text-[var(--foreground)]">The System in 4 Steps:</h4>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <StepCard num={1} title="You Add Tickers" desc="Choose which stocks or crypto to monitor (e.g., BTC, AAPL, SPY)" />
          <StepCard num={2} title="System Analyzes" desc="Checks price trends, momentum, and volatility using EMA, RSI, and ATR indicators" />
          <StepCard num={3} title="Signals Generated" desc="BUY or SELL signals only fire when strict mathematical criteria are met" />
          <StepCard num={4} title="You Get Notified" desc="Alerts sent to Telegram and Discord with exact entry, stop-loss, and target prices" />
        </div>
      </div>
      <Tip>
        The system is designed to be conservative — it would rather miss a trade than risk your capital.
        &quot;No trade&quot; is always preferred over an uncertain trade.
      </Tip>
    </DocSection>
  );
}

function WatchlistSection() {
  return (
    <DocSection title="Managing Your Watchlist">
      <p>
        The watchlist is your list of assets the system actively monitors.
        You control exactly which stocks and cryptocurrencies to track.
      </p>
      <h4 className="text-sm font-semibold text-[var(--foreground)]">Adding an Asset</h4>
      <ol className="list-decimal list-inside space-y-1 text-xs">
        <li>Type the <strong>ticker symbol</strong> (e.g., BTC, AAPL, ETH)</li>
        <li>Enter a <strong>name</strong> (e.g., Bitcoin, Apple Inc.)</li>
        <li>Select the <strong>type</strong> — Crypto or Stock</li>
        <li>Click <strong>+ Add</strong> (or press Enter)</li>
      </ol>
      <h4 className="text-sm font-semibold text-[var(--foreground)] mt-2">Analyzing an Asset</h4>
      <p>
        Click the <strong>Analyze</strong> button next to any ticker to run a full quantitative analysis.
        The system will calculate trend direction, momentum, and volatility, and may generate a BUY or SELL signal.
      </p>
      <Tip>
        Start with well-known, high-volume assets like BTC, ETH, SPY, or AAPL.
        These have the most reliable data for analysis.
      </Tip>
    </DocSection>
  );
}

function SignalsSection() {
  return (
    <DocSection title="Understanding Trading Signals">
      <p>
        The system generates three types of signals based on mathematical indicators:
      </p>
      <div className="space-y-2">
        <SignalCard
          type="BUY"
          color="text-green-400"
          desc="The asset is in an uptrend and momentum is favorable. The system suggests opening a position."
          criteria="Price above 200 EMA + 20 EMA crosses above 50 EMA + RSI between 45-55"
        />
        <SignalCard
          type="SELL"
          color="text-red-400"
          desc="The trend is weakening or reversing. The system suggests closing your position."
          criteria="Price crosses below 50 EMA OR RSI exceeds 75 (overbought)"
        />
        <SignalCard
          type="SUPPRESSED"
          color="text-yellow-400"
          desc="Conditions are too volatile or dangerous. No action is taken — this protects your capital."
          criteria="Asset is tanking OR volatility (ATR) is 2x above average"
        />
      </div>
      <Tip>
        Every signal includes a specific entry price, stop-loss, and target price.
        The stop-loss is your safety net — it limits how much you can lose on any single trade.
      </Tip>
    </DocSection>
  );
}

function PortfolioSection() {
  return (
    <DocSection title="Portfolio & Trade Tracking">
      <p>
        The portfolio tracks your <strong>actual trades</strong> — buys and sells that you
        report via the UI or Telegram bot. Signals generate alerts but do NOT auto-execute trades.
      </p>
      <h4 className="text-sm font-semibold text-[var(--foreground)]">How It Works</h4>
      <ul className="list-disc list-inside space-y-1 text-xs">
        <li><strong>Available Balance:</strong> Set this to your real trading capital</li>
        <li><strong>Log Trade:</strong> Record a buy or sell when you execute one</li>
        <li><strong>Close Position:</strong> Enter exit price to calculate realized P&L</li>
        <li><strong>Telegram:</strong> Use <code>/bought AAPL 150 10</code> or <code>/sold AAPL 160 10</code></li>
      </ul>
      <p>
        Gains and losses are calculated from your reported entry and exit prices.
        The system never assumes you acted on a signal — it only tracks what you tell it.
      </p>
      <Tip>
        Keep your balance updated so trade recommendations are correctly sized
        relative to your actual capital.
      </Tip>
    </DocSection>
  );
}

function RiskSection() {
  return (
    <DocSection title="How Your Capital is Protected">
      <p>
        This system is built around <strong>capital preservation</strong> — protecting your money
        is always the top priority, even if it means missing profitable trades.
      </p>
      <div className="space-y-3">
        <RiskCard
          title="Half-Kelly Position Sizing"
          desc="The system calculates exactly how much to invest in each trade using the Kelly Criterion (at half strength for extra safety). This means it invests less when the win rate is lower and more when conditions are favorable."
        />
        <RiskCard
          title="Hard Stop-Loss on Every Trade"
          desc="Every trade has a mandatory stop-loss price calculated from market volatility (1.5x ATR below entry). If the price hits this level, the position is closed immediately to limit losses."
        />
        <RiskCard
          title="1:3 Risk-Reward Ratio"
          desc="The system only takes trades where the potential profit is at least 3x the potential loss. This means even if only 1 in 3 trades wins, you can still be profitable."
        />
        <RiskCard
          title="Volatility Filter"
          desc="If a market becomes erratic (volatility 2x above its 30-day average), ALL buy signals are suppressed. The system waits for calm conditions before trading."
        />
        <RiskCard
          title="Tanking Detection"
          desc="If an asset falls below its 200-day trend line or shows bearish momentum, it's marked as 'tanking'. No buys are allowed, and open positions are flagged for immediate exit."
        />
        <RiskCard
          title="Portfolio Capital Guard"
          desc="If a suggested trade exceeds 5% of your available balance, it's flagged with a CAPITAL OVERSPEND warning — alerting you to avoid oversized positions."
        />
      </div>
      <Tip>
        These guardrails work together. During a market crash, position sizes shrink toward $0,
        buy signals are suppressed, and tanking assets are flagged for liquidation.
      </Tip>
    </DocSection>
  );
}

function SettingsSection() {
  return (
    <DocSection title="Configuring Services">
      <p>The platform connects to several external services. Here&apos;s what each one does:</p>
      <div className="space-y-3">
        <ServiceDoc
          name="Binance"
          purpose="Provides real-time crypto prices and historical candle data"
          setup="Create a free API key at binance.com (Read Only permission is enough)"
        />
        <ServiceDoc
          name="Alpaca"
          purpose="Provides real-time stock prices and historical data"
          setup="Sign up for a free paper trading account at alpaca.markets"
        />
        <ServiceDoc
          name="Telegram"
          purpose="Sends trading signal alerts to your Telegram chat"
          setup="Create a bot via @BotFather and get your chat ID"
        />
        <ServiceDoc
          name="Discord"
          purpose="Sends trading signal alerts to your Discord channel"
          setup="Create a webhook in your Discord server's Integrations settings"
        />
        <ServiceDoc
          name="Slack"
          purpose="Sends signal alerts to a Slack channel"
          setup="Create an incoming webhook in your Slack workspace settings"
        />
        <ServiceDoc
          name="Email (SMTP)"
          purpose="Sends signal alerts via email"
          setup="Use an SMTP server (e.g., Gmail with an app password). Port 587 with TLS."
        />
        <ServiceDoc
          name="SMS (Twilio)"
          purpose="Sends signal alerts via SMS text message"
          setup="Sign up at twilio.com and get your Account SID, Auth Token, and a phone number"
        />
      </div>
      <Tip>
        You don&apos;t need all services configured to use the platform. Market data APIs
        enable analysis; notification services are optional.
      </Tip>
    </DocSection>
  );
}

function ScannerToolsSection() {
  return (
    <DocSection title="Scanner, Backtesting & Tools">
      <h4 className="text-sm font-semibold text-[var(--foreground)]">Auto-Scanner</h4>
      <p>
        The <strong>Scanner</strong> tab lets you configure automatic scanning of your entire watchlist.
        When enabled, the system analyzes all tickers at a set interval (default: every 15 minutes)
        and sends notifications for any approved signals.
      </p>
      <ul className="list-disc list-inside space-y-1 text-xs">
        <li><strong>Scan Now:</strong> Trigger an immediate scan of all tickers</li>
        <li><strong>Auto-Scan toggle:</strong> Enable/disable the background scheduler</li>
        <li><strong>Interval:</strong> Choose 5m, 15m, 30m, or 60m between scans</li>
        <li><strong>Market Hours Only:</strong> Restrict stock scans to 9:30–16:00 ET (crypto scans 24/7)</li>
        <li><strong>Deduplication:</strong> Same signal won&apos;t re-alert within 24 hours</li>
      </ul>

      <h4 className="text-sm font-semibold text-[var(--foreground)] mt-3">Price Alerts</h4>
      <p>
        Set custom price thresholds on any ticker. Get notified when the price crosses above or below
        your target. Alerts are one-shot — once triggered, they&apos;re marked as complete.
      </p>

      <h4 className="text-sm font-semibold text-[var(--foreground)] mt-3">Backtesting</h4>
      <p>
        Click <strong>Backtest</strong> on any ticker in the watchlist to simulate how the signal model
        would have performed historically. Shows win rate, average P&L, drawdown, and individual trades.
      </p>

      <h4 className="text-sm font-semibold text-[var(--foreground)] mt-3">Historical Charts</h4>
      <p>
        Click <strong>Chart</strong> on any ticker to view a price chart with volume bars.
        Past signal prices are shown as reference lines. Choose 30, 60, 90, or 180 day ranges.
      </p>

      <h4 className="text-sm font-semibold text-[var(--foreground)] mt-3">Earnings Calendar</h4>
      <p>
        When you expand a ticker in the watchlist, the system checks for upcoming earnings dates.
        A yellow badge appears if earnings are scheduled, helping you avoid volatile events.
      </p>

      <Tip>
        Combine the scanner with price alerts for comprehensive coverage — the scanner catches
        pattern-based signals while price alerts watch for specific price levels.
      </Tip>
    </DocSection>
  );
}

function GlossarySection() {
  return (
    <DocSection title="Trading Terms Explained">
      <p>New to trading? Here are the key terms used in this platform:</p>
      <div className="space-y-2">
        <Term word="EMA (Exponential Moving Average)" def="A smoothed average of recent prices that gives more weight to newer data. The 200 EMA shows the long-term trend, 50 EMA shows medium-term, and 20 EMA shows short-term." />
        <Term word="RSI (Relative Strength Index)" def="A 0-100 score measuring if an asset is overbought (above 70) or oversold (below 30). The system buys when RSI is between 45-55 (neutral/recovering)." />
        <Term word="ATR (Average True Range)" def="Measures how much an asset's price typically moves in a day. Higher ATR = more volatile and risky." />
        <Term word="Stop-Loss" def="A pre-set price where you automatically sell to limit losses. Like a safety net — you decide the maximum you're willing to lose before entering a trade." />
        <Term word="Target Price" def="The price where you plan to take profits. Set based on the risk-reward ratio (typically 3x the distance to your stop-loss)." />
        <Term word="Kelly Criterion" def="A mathematical formula that calculates the optimal bet size based on your win rate and reward ratio. We use 'Half-Kelly' (half the suggested size) for extra safety." />
        <Term word="Position Size" def="How much money to put into a single trade. The system calculates this automatically based on your account size and market conditions." />
        <Term word="Drawdown" def="How much your portfolio value has dropped from its highest point. A 10% drawdown means your portfolio fell 10% from its peak." />
        <Term word="Win Rate" def="The percentage of trades that were profitable. A 40% win rate with a 1:3 risk-reward can still be very profitable." />
        <Term word="Profit Factor" def="Total money won divided by total money lost. Above 1.0 means you're making more than you're losing overall." />
        <Term word="Tanking" def="When an asset is in a severe downtrend. The system detects this automatically and stops all buying." />
        <Term word="Paper Trading" def="Simulated trading with virtual money. Lets you test strategies without risking real capital." />
        <Term word="Account Balance" def="The amount of capital available for new trades, configurable in Settings." />
      </div>
    </DocSection>
  );
}

function StepCard({ num, title, desc }: { num: number; title: string; desc: string }) {
  return (
    <div className="rounded border p-3 flex gap-3">
      <div className="h-6 w-6 rounded-full bg-[var(--primary)] text-[var(--primary-foreground)] text-xs flex items-center justify-center font-bold flex-shrink-0">
        {num}
      </div>
      <div>
        <div className="text-sm font-medium text-[var(--foreground)]">{title}</div>
        <div className="text-xs text-[var(--muted-foreground)]">{desc}</div>
      </div>
    </div>
  );
}

function SignalCard({ type, color, desc, criteria }: { type: string; color: string; desc: string; criteria: string }) {
  return (
    <div className="rounded border p-3">
      <div className={`text-sm font-bold ${color}`}>{type}</div>
      <p className="text-xs mt-1">{desc}</p>
      <p className="text-xs mt-1 opacity-60"><em>Triggers when: {criteria}</em></p>
    </div>
  );
}

function RiskCard({ title, desc }: { title: string; desc: string }) {
  return (
    <div className="rounded border p-3">
      <div className="text-sm font-semibold text-[var(--foreground)]">{title}</div>
      <p className="text-xs mt-1">{desc}</p>
    </div>
  );
}

function ServiceDoc({ name, purpose, setup }: { name: string; purpose: string; setup: string }) {
  return (
    <div className="rounded border p-3">
      <div className="text-sm font-semibold text-[var(--foreground)]">{name}</div>
      <p className="text-xs mt-1"><strong>Purpose:</strong> {purpose}</p>
      <p className="text-xs mt-0.5"><strong>Setup:</strong> {setup}</p>
    </div>
  );
}

function Term({ word, def }: { word: string; def: string }) {
  return (
    <div className="rounded border p-3">
      <div className="text-sm font-semibold text-[var(--foreground)]">{word}</div>
      <p className="text-xs mt-1">{def}</p>
    </div>
  );
}
