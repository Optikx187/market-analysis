import { useEffect, useState } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Settings, Plus, Trash2, Zap, ToggleLeft, ToggleRight } from "lucide-react";
import type { Webhook, WebhookTestResult } from "@/lib/api";
import {
  fetchWebhooks,
  addWebhook,
  removeWebhook,
  toggleWebhook,
  testWebhook,
} from "@/lib/api";

export default function SettingsPanel() {
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [whName, setWhName] = useState("");
  const [whUrl, setWhUrl] = useState("");
  const [whSecret, setWhSecret] = useState("");
  const [testResults, setTestResults] = useState<Record<number, WebhookTestResult>>({});
  const [loading, setLoading] = useState(false);

  const loadWebhooks = async () => {
    try {
      const data = await fetchWebhooks();
      setWebhooks(data);
    } catch { /* ignore */ }
  };

  useEffect(() => { loadWebhooks(); }, []);

  const handleAdd = async () => {
    if (!whName.trim() || !whUrl.trim()) return;
    setLoading(true);
    try {
      await addWebhook(whName.trim(), whUrl.trim(), whSecret.trim() || undefined);
      setWhName("");
      setWhUrl("");
      setWhSecret("");
      await loadWebhooks();
    } catch { /* ignore */ }
    setLoading(false);
  };

  const handleRemove = async (id: number) => {
    try {
      await removeWebhook(id);
      setWebhooks((prev) => prev.filter((w) => w.id !== id));
    } catch { /* ignore */ }
  };

  const handleToggle = async (id: number) => {
    try {
      const updated = await toggleWebhook(id);
      setWebhooks((prev) => prev.map((w) => (w.id === id ? updated : w)));
    } catch { /* ignore */ }
  };

  const handleTest = async (id: number) => {
    try {
      const result = await testWebhook(id);
      setTestResults((prev) => ({ ...prev, [id]: result }));
    } catch { /* ignore */ }
  };

  return (
    <div className="space-y-4">
      {/* API Webhooks Section */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Zap className="h-5 w-5" />
            API Webhook Endpoints
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Configure external API endpoints to receive trading signals as structured JSON payloads via POST requests.
            Each signal includes ticker, direction, prices, stop-loss, and target. An optional HMAC-SHA256 signature
            is sent in the <code>X-Signature-SHA256</code> header when a secret is set.
          </p>

          {/* Add Webhook Form */}
          <div className="flex flex-col gap-2 p-3 rounded-lg border bg-muted/30">
            <div className="flex gap-2">
              <Input
                placeholder="Webhook name (e.g. My Trading Bot)"
                value={whName}
                onChange={(e) => setWhName(e.target.value)}
                className="flex-1"
              />
              <Input
                placeholder="https://api.example.com/signals"
                value={whUrl}
                onChange={(e) => setWhUrl(e.target.value)}
                className="flex-[2]"
              />
            </div>
            <div className="flex gap-2">
              <Input
                placeholder="Signing secret (optional)"
                value={whSecret}
                onChange={(e) => setWhSecret(e.target.value)}
                type="password"
                className="flex-1"
              />
              <Button onClick={handleAdd} disabled={loading || !whName.trim() || !whUrl.trim()} size="sm">
                <Plus className="h-4 w-4 mr-1" /> Add Webhook
              </Button>
            </div>
          </div>

          {/* Webhook List */}
          {webhooks.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-4">
              No API webhooks configured yet. Add one above to start receiving signals via API calls.
            </p>
          ) : (
            <div className="space-y-2">
              {webhooks.map((wh) => (
                <div key={wh.id} className="flex items-center gap-2 p-3 rounded-lg border">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm">{wh.name}</span>
                      <Badge variant={wh.is_active ? "default" : "secondary"}>
                        {wh.is_active ? "Active" : "Disabled"}
                      </Badge>
                      {wh.secret && (
                        <Badge variant="outline" className="text-xs">Signed</Badge>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground truncate">{wh.url}</p>
                    {testResults[wh.id] && (
                      <p className={`text-xs mt-1 ${testResults[wh.id].success ? "text-green-600" : "text-red-600"}`}>
                        {testResults[wh.id].success
                          ? `Test passed (HTTP ${testResults[wh.id].status_code})`
                          : `Test failed: ${testResults[wh.id].error}`}
                      </p>
                    )}
                  </div>
                  <Button variant="ghost" size="icon" title="Test webhook" onClick={() => handleTest(wh.id)}>
                    <Zap className="h-4 w-4" />
                  </Button>
                  <Button variant="ghost" size="icon" title={wh.is_active ? "Disable" : "Enable"} onClick={() => handleToggle(wh.id)}>
                    {wh.is_active ? <ToggleRight className="h-4 w-4" /> : <ToggleLeft className="h-4 w-4" />}
                  </Button>
                  <Button variant="ghost" size="icon" title="Remove" onClick={() => handleRemove(wh.id)}>
                    <Trash2 className="h-4 w-4 text-destructive" />
                  </Button>
                </div>
              ))}
            </div>
          )}

          {/* Payload Example */}
          <div className="mt-4 p-3 rounded-lg border bg-muted/50">
            <p className="text-xs font-medium mb-2">Example JSON payload sent to your webhook:</p>
            <pre className="text-xs text-muted-foreground overflow-x-auto">{`{
  "event": "trading_signal",
  "timestamp": 1717462800,
  "signal": {
    "ticker": "BTC",
    "direction": "BUY",
    "trigger_price": 50000.0,
    "stop_loss": 48500.0,
    "target_price": 54500.0,
    "reason": "EMA Golden Cross + Stable RSI",
    "suppressed": false
  }
}`}</pre>
          </div>
        </CardContent>
      </Card>

      {/* Existing Notification Settings */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Settings className="h-5 w-5" />
            Notification Settings
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Configure these in the backend <code>.env</code> file. The values below are read-only indicators.
          </p>
          <div className="space-y-3">
            <div>
              <label className="text-sm font-medium">Telegram Bot Token</label>
              <Input disabled placeholder="Set TELEGRAM_BOT_TOKEN in .env" className="mt-1" />
              <Badge variant="outline" className="mt-1">
                Backend .env
              </Badge>
            </div>
            <div>
              <label className="text-sm font-medium">Telegram Chat ID</label>
              <Input disabled placeholder="Set TELEGRAM_CHAT_ID in .env" className="mt-1" />
            </div>
            <div>
              <label className="text-sm font-medium">Discord Webhook URL</label>
              <Input disabled placeholder="Set DISCORD_WEBHOOK_URL in .env" className="mt-1" />
            </div>
          </div>

          <div className="mt-4 p-3 rounded-lg border bg-muted/50">
            <p className="text-xs text-muted-foreground">
              <strong>How to configure:</strong><br />
              1. Create a <code>.env</code> file in the <code>backend/</code> directory<br />
              2. Add: <code>TELEGRAM_BOT_TOKEN=your_token</code><br />
              3. Add: <code>TELEGRAM_CHAT_ID=your_chat_id</code><br />
              4. Add: <code>DISCORD_WEBHOOK_URL=your_webhook_url</code><br />
              5. Restart the backend server
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
