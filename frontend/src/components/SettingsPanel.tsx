import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Settings } from "lucide-react";

export default function SettingsPanel() {
  return (
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
  );
}
