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
      "/api/assets": "http://data-ingestion:8000",
      "/api/candles": "http://data-ingestion:8000",
      "/api/data": "http://data-ingestion:8000",
      "/api/analyze": "http://quant-engine:8001",
      "/api/risk-profile": "http://quant-engine:8001",
      "/api/portfolio": "http://portfolio-engine:8002",
      "/api/trades": "http://portfolio-engine:8002",
      "/api/alerts": "http://portfolio-engine:8002",
      "/api/robinhood": "http://portfolio-engine:8002",
    },
  },
});
