# UI

A Vite + React SPA in `web/`, served by the same FastAPI app as the API
(`src/podcast/api/app.py`) — no separate frontend server in production.

## Routes

| Path         | Page                | What it does |
|--------------|---------------------|--------------|
| `/settings`  | `src/pages/Settings.jsx` | Edit the profile: podcast name, listener name, duration, tone; interests (topic, weight slider, description + "Suggest" button); hosts (name, persona, voice picker); style sliders/toggles (humour, depth, tangents, banter); schedule (cron field + presets). Save → `PUT /profile`. |
| `/episodes`  | `src/pages/Episodes.jsx` | List of episodes with status badges. "Generate now" → `POST /episodes`, then polls the list until nothing is `pending`/`running`. Each row expands (lazy-loads `GET /episodes/{id}`) into an audio player streaming `/episodes/{id}/audio`, show notes (source links), and the full script. |
| `/dashboard` | `src/pages/Dashboard.jsx` | Placeholder — metrics dashboard is tomorrow's work. |

`/` renders the same component as `/episodes` (default landing page) —
`App.jsx` maps both paths to `<Episodes />` rather than issuing a redirect.

## How state flows

- **Settings**: `GET /profile` is loaded whole into one React state object on
  mount (404 → a blank scaffold with just the fields the form needs — see
  `blankProfile()` in `Settings.jsx`). Every field in the form mutates that
  same object immutably (nested spreads: `podcast`, `podcast.style`,
  `podcast.listener`, array updates for `interests`/`hosts`). Save sends the
  **entire** object back via `PUT /profile` — fields the UI never exposes
  (`tone` is exposed, but `recurring_bits`, `feeds`, `llm`, `tts`, `fetch`
  are not) round-trip untouched because they're still sitting in the same
  state object from the original `GET`. There is no per-field PATCH.
- **Interests → Suggest**: clicking "Suggest" on one interest row calls
  `POST /interests/suggest` with that row's `topic`/`description` and splices
  the response's `description` + `queries` back into that one row's local
  state — it does not touch the rest of the form or save anything itself
  (still requires clicking the page's own Save).
- **Episodes list**: `Episodes.jsx` owns one `episodes` array in state,
  refreshed by `GET /episodes`. A `setTimeout`-based loop (`refresh()` in
  `Episodes.jsx`) re-fetches every 3s **only while at least one episode is
  `pending`/`running`**, and stops scheduling itself once everything has
  settled to `done`/`failed` — see docs/decisions.md for why polling, not
  websockets. "Generate now" calls `POST /episodes` then immediately calls
  `refresh()` once more, both to show the new row right away and to restart
  the loop if it had already stopped.
- **Episode detail**: expanding a row is a lazy `GET /episodes/{id}` fired
  once per row (cached in that row's own local state, not re-fetched on
  every list poll) — the parent list's periodic refresh only replaces the
  lightweight summary rows, never the heavier detail (script/show notes).
- **Playback events**: the `<audio>` element's `onPlay` (fired once per row,
  guarded by a ref so repeated pause/resume doesn't spam events) and
  `onEnded` handlers call `POST /episodes/{id}/events` with `type: "played"`
  / `type: "completed_playback"` respectively — fire-and-forget, no UI state
  depends on the response.

## Dev vs. production serving

- **Development**: `npm run dev` (from `web/`) starts Vite's dev server
  (default `http://localhost:5173`, though it may pick another free port).
  `web/vite.config.js` proxies every API path (`/profile`, `/episodes`,
  `/metrics`, `/schedule`, `/interests`, `/voices`) to
  `http://localhost:8000` — run the backend separately (`uv run podcast
  serve`, or `uv run uvicorn podcast.api.app:app --reload`) alongside it.
  Same-origin `fetch()` calls in `src/api.js` work unchanged in both modes
  because of this proxy.
- **Production**: build the frontend first —
  ```
  cd web
  npm install
  npm run build
  ```
  This writes static assets to **`web/dist/`** (`index.html` +
  `assets/*.js`/`*.css`). `uv run podcast serve` **does not run this build
  itself** — it only starts the FastAPI app, which serves `web/dist/` as
  static files if that directory exists (API-only if it doesn't).
  `src/podcast/api/app.py` mounts `web/dist/assets/` at `/assets` and adds a
  catch-all route that serves a real file if Vite emitted one at that exact
  path, else `web/dist/index.html` — that fallback is what lets a browser
  refresh on `/settings` or `/episodes` keep working (React Router takes
  over client-side once `index.html` loads), registered *after* every API
  router so it never shadows `/profile`, `/episodes`, etc.
- Rebuild (`npm run build`) any time frontend source changes — there's no
  file-watching/rebuild-on-request in production.
