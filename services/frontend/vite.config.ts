import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

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
      "/api/assets": "http://localhost:8000",
      "/api/candles": "http://localhost:8000",
      "/api/data": "http://localhost:8000",
      "/api/symbols": "http://localhost:8000",
      "/api/quotes": "http://localhost:8000",
      "/api/status": "http://localhost:8000",
      "/api/price-alerts": "http://localhost:8000",
      "/api/earnings": "http://localhost:8000",
      "/api/analyze": "http://localhost:8001",
      "/api/risk-profile": "http://localhost:8001",
      "/api/scan-all": "http://localhost:8001",
      "/api/scanner": "http://localhost:8001",
      "/api/backtest": "http://localhost:8001",
      "/api/portfolio": "http://localhost:8002",
      "/api/dashboard-summary": "http://localhost:8002",
      "/api/auth": "http://localhost:8002",
      "/api/trades": "http://localhost:8002",
      "/api/alerts": "http://localhost:8002",
      "/api/process-signal": "http://localhost:8002",
      "/api/settings": "http://localhost:8002",
      "/api/notify": "http://localhost:8003",
    },
  },
});
