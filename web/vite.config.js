import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev-only: the backend (uv run podcast serve, or uvicorn directly) runs on
// :8000; Vite's dev server proxies API paths to it so the app can use plain
// same-origin fetch('/episodes') etc. in both dev and prod. In production
// there's no proxy at all — FastAPI serves this app's build (web/dist/) and
// the API from the same origin. See docs/ui.md.
const API_PATHS = ['/profile', '/episodes', '/metrics', '/schedule', '/interests', '/voices']

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: Object.fromEntries(API_PATHS.map((path) => [path, 'http://localhost:8000'])),
  },
})
