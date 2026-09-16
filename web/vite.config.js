import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev-only: the backend (uv run podcast serve, or uvicorn directly) runs on
// :8000; Vite's dev server proxies /api to it so the app can use plain
// same-origin fetch('/api/episodes') etc. in both dev and prod. In
// production there's no proxy at all — FastAPI serves this app's build
// (web/dist/) and the API (also under /api) from the same origin. See
// docs/ui.md.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
