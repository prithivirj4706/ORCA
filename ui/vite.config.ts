import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The production bundle is served by FastAPI at /ui/, so asset paths must be
// relative to that base and API calls must be same-origin. In development Vite
// proxies /v1 to the backend, which keeps the client free of any origin.
const API_TARGET = process.env.VITE_API_TARGET || 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  // maplibre constructs its worker with {type:'module'}; emit a real ES
  // module worker rather than the default IIFE so that stays honest.
  worker: { format: 'es' },
  base: '/ui/',
  // Build straight into the backend package so `uvicorn orca.api.main:app`
  // serves the real UI with no copy step to forget.
  build: { outDir: '../backend/orca/api/webui', emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      '/v1': { target: API_TARGET, changeOrigin: true }
    }
  }
});
