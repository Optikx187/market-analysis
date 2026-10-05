import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

const dataIngestionUrl = process.env.VITE_DATA_INGESTION_URL ?? "http://localhost:8000";
const quantEngineUrl = process.env.VITE_QUANT_ENGINE_URL ?? "http://localhost:8001";
const portfolioEngineUrl = process.env.VITE_PORTFOLIO_ENGINE_URL ?? "http://localhost:8002";
const notificationGatewayUrl = process.env.VITE_NOTIFICATION_GATEWAY_URL ?? "http://localhost:8003";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api/assets": dataIngestionUrl,
      "/api/candles": dataIngestionUrl,
      "/api/data": dataIngestionUrl,
      "/api/symbols": dataIngestionUrl,
      "/api/quotes": dataIngestionUrl,
      "/api/status": dataIngestionUrl,
      "/api/price-alerts": dataIngestionUrl,
      "/api/earnings": dataIngestionUrl,
      "/api/analyze": quantEngineUrl,
      "/api/risk-profile": quantEngineUrl,
      "/api/regime": quantEngineUrl,
      "/api/scan-all": quantEngineUrl,
      "/api/scanner": quantEngineUrl,
      "/api/opportunities": quantEngineUrl,
      "/api/backtest": quantEngineUrl,
      "/api/attribution": portfolioEngineUrl,
      "/api/portfolio": portfolioEngineUrl,
      "/api/paper-orders": portfolioEngineUrl,
      "/api/live-orders": portfolioEngineUrl,
      "/api/live-trading": portfolioEngineUrl,
      "/api/dashboard-summary": portfolioEngineUrl,
      "/api/action-items": portfolioEngineUrl,
      "/api/dashboard-preferences": portfolioEngineUrl,
      "/api/auth": portfolioEngineUrl,
      "/api/trades": portfolioEngineUrl,
      "/api/alerts": portfolioEngineUrl,
      "/api/process-signal": portfolioEngineUrl,
      "/api/settings": portfolioEngineUrl,
      "/api/notify": notificationGatewayUrl,
    },
  },
});
