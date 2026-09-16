# UI

A Vite + React SPA in `web/`, served by the same FastAPI app as the API
(`src/podcast/api/app.py`) — no separate frontend server in production.

## Path namespaces: SPA routes vs. `/api`

The SPA's own client-side routes (`/`, `/settings`, `/episodes`,
`/dashboard`) and the backend's API routes live in two disjoint namespaces:
every API route is mounted under `/api` (`/api/profile`, `/api/episodes`,
`/api/metrics/summary`, `/api/schedule/next`, `/api/interests/suggest`,
`/api/voices`) — see docs/decisions.md ("API routes under /api") for why
this was introduced: `GET /episodes` used to collide with the SPA's
`/episodes` page, so a hard refresh or direct navigation to `/episodes`
served the JSON episode list instead of the app. `/docs` (FastAPI's own
interactive API docs) is unaffected — it's registered independently of any
router prefix and stays at the root.

## Routes

| Path         | Page                | What it does |
|--------------|---------------------|--------------|
| `/settings`  | `src/pages/Settings.jsx` | Edit the profile: podcast name, listener name, duration, tone; interests (topic, Low/Medium/High weight choice, description + "Suggest" button); hosts (name, voice picker, a "Choose a preset" picker — see below — persona); style sliders/toggles (humour, depth, tangents, banter); schedule (cron field + presets). Each section `Card` carries an icon (`lucide-react`). Save → `PUT /api/profile`. |
| `/episodes`  | `src/pages/Episodes.jsx` | Episodes as cards (title from the script — falls back to the date when there isn't one yet — duration, status, a labelled "Details" toggle), newest first. Mocked rows never appear (the backend excludes them — see "Episode list" below). Default view is `done`/`running`/`pending` only; `failed`/`no_content` sit behind a "Show failed / no content (N)" toggle, each set independently collapsing anything older than two weeks under its own "Older (N)" disclosure (`EpisodeList.jsx`). "Generate now" lives in the app shell, not this page — see "Generate now" below — and dispatches a `podcast:episode-created` window event this page listens for to refresh/restart its poll. A card's "Details" expands (lazy-loads `GET /api/episodes/{id}`) into the player (the card's hero — large play control, live time/progress bar, streaming `/api/episodes/{id}/audio`, see "Audio player" below) plus three collapsed-by-default disclosures, Transcript / Sources / About the hosts; a failed episode's card shows its human-readable `failure_reason` when one was recorded. |
| `/dashboard` | `src/pages/Dashboard.jsx` | Loads `GET /api/metrics/summary` (once per `includeMocked` toggle — see docs/decisions.md, "dashboard mocked-data toggle") and renders a KPI row (episodes done/total, completion rate, D7 retention, cost/episode), three recharts charts (episodes & plays per day, cost by stage, topic distribution), a recent-failures/no_content table, and a **Quality over time** card (`QualityChart` + `QualityTable` — see docs/decisions.md, "Quality metrics"; never mocked, since a mocked row can't have a `quality.json`). Shows a "mocked demo data" banner only in the opted-in mode. |

`/` renders the same component as `/episodes` (default landing page) —
`App.jsx` maps both paths to `<Episodes />` rather than issuing a redirect.

## How state flows

- **Settings**: `GET /api/profile` is loaded whole into one React state
  object on mount (404 → a blank scaffold with just the fields the form
  needs — see `blankProfile()` in `Settings.jsx`). Every field in the form
  mutates that same object immutably (nested spreads: `podcast`,
  `podcast.style`, `podcast.listener`, array updates for
  `interests`/`hosts`). Save sends the **entire** object back via
  `PUT /api/profile` — fields the UI never exposes (`tone` is exposed, but
  `recurring_bits`, `feeds`, `llm`, `tts`, `fetch` are not) round-trip
  untouched because they're still sitting in the same state object from the
  original `GET`. There is no per-field PATCH.
- **Interests → Suggest**: clicking "Suggest" on one interest row calls
  `POST /api/interests/suggest` with that row's `topic`/`description` and
  splices the response's `description` + `queries` back into that one row's
  local state — it does not touch the rest of the form or save anything
  itself (still requires clicking the page's own Save).
- **Hosts → Choose a preset**: `GET /api/hosts/presets` (`host_presets.py`,
  see docs/decisions.md, "Persona rigidity") is loaded once on mount
  alongside voices. Each host row in `HostEditor.jsx` gets its own preset
  `<select>`; picking one overwrites that row's name/voice_id/persona/
  home_turf in local state, then the select remounts itself (a `key` bump)
  back to its placeholder — it's a one-shot fill, not a binding, and every
  field stays editable afterward. Like the rest of the form, applying a
  preset doesn't save anything by itself.
- **Episodes list**: `Episodes.jsx` owns one `episodes` array in state,
  refreshed by `GET /api/episodes`. A `setTimeout`-based loop (`refresh()`
  in `Episodes.jsx`) re-fetches every 3s **only while at least one episode
  is `pending`/`running`**, and stops scheduling itself once everything has
  settled to `done`/`failed` — see docs/decisions.md for why polling, not
  websockets. "Generate now" (in the app shell — see `GenerateButton.jsx`)
  calls `POST /api/episodes` then dispatches a `podcast:episode-created`
  window event, which `Episodes.jsx` listens for to call `refresh()`, both
  to show the new row right away and to restart the poll loop if it had
  already stopped.
- **Episode detail**: expanding a card is a lazy `GET /api/episodes/{id}`
  fired once per card (cached in that card's own local state, not
  re-fetched on every list poll) — the parent list's periodic refresh only
  replaces the lightweight summary rows, never the heavier detail
  (script/show notes).
- **Playback events**: the `<audio>` element's `onPlay` (fired once per
  card, guarded by a ref so repeated pause/resume doesn't spam events) and
  `onEnded` handlers call `POST /api/episodes/{id}/events` with
  `type: "played"` / `type: "completed_playback"` respectively —
  fire-and-forget, no UI state depends on the response.

## Dev vs. production serving

- **Development**: `npm run dev` (from `web/`) starts Vite's dev server
  (default `http://localhost:5173`, though it may pick another free port).
  `web/vite.config.js` proxies `/api` to `http://localhost:8000` — run the
  backend separately (`uv run podcast serve`, or `uv run uvicorn
  podcast.api.app:app --reload`) alongside it. Same-origin `fetch()` calls
  in `src/api.js` (all under `/api/...`) work unchanged in both modes
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
  router. Since every API route now lives under `/api`, this catch-all no
  longer needs to out-race a same-named API route the way `GET /episodes`
  used to — the two namespaces simply don't overlap.
- Rebuild (`npm run build`) any time frontend source changes — there's no
  file-watching/rebuild-on-request in production.

## Design

A one-hour polish pass (2026-09-16) — mobile-first layout, one palette, one
primary action, episode cards, a restyled audio player, a coloured/avatared
transcript, section icons, a discrete interest-weight control, and empty
states. `lucide-react` is the only new dependency; everything else reuses
Tailwind v4 + recharts, already in place.

### Palette

Exactly four colours, defined once as CSS custom properties in
`src/index.css`'s `@theme` block (which makes Tailwind generate `bg-bg`,
`text-ink`, `bg-accent`, `border-accent/20`, etc., including arbitrary
opacity modifiers, for free):

| Variable | Role |
|---|---|
| `--color-bg` | dark app background (outside cards) |
| `--color-surface` | light content-card background (also used as light text on the dark background — sidebar/bottom-bar labels, avatar initials) |
| `--color-ink` | dark text/borders on a light `surface` card; also one of the two speaker colours |
| `--color-accent` | the one brand accent — primary actions ("Generate now"), active nav, links, "done"/"running" status, the other speaker colour, one chart series |

No other hex colour appears anywhere in the app (`grep -rnE
"slate-|red-|green-|blue-|amber-|violet-|purple-" web/src` — including chart
code — comes back empty). Two consequences worth naming:

- **Status badges are told apart by icon, not colour** (`StatusBadge.jsx`):
  `pending`/`running`/`done`/`failed`/`no_content` each get a distinct
  `lucide-react` icon (clock / spinning loader / check / x / triangle) plus
  ink-or-accent weight, rather than the previous slate/blue/green/red/amber
  set. This is a deliberate trade — a dedicated red for "failed" would read
  faster at a glance — made to honour "one palette of four colours, used
  everywhere" literally rather than smuggling in a fifth status hue.
- **Charts reuse the exact same variables** (`src/lib/chartColors.js`) —
  recharts' SVG `fill`/`stroke` props accept `var(--color-accent)` etc.
  directly (browsers resolve custom properties in presentation attributes),
  so there's no second, chart-only palette to keep in sync. Multiple series
  in one chart (the three lines in "Episodes & plays per day", the two
  providers in "Cost by stage") are told apart by opacity/dash rather than
  hue — `SERIES.strong` (`accent`), `SERIES.medium` (`accent` at ~55%
  opacity via `color-mix()`), `SERIES.soft` (`ink`, dashed).

### Navigation

`App.jsx` renders one of two shells depending on viewport, both built from
the same `SECTIONS` list (Episodes/Settings/Dashboard, each an icon +
label):

- **Desktop (`sm:` and up, ≥640px)**: a fixed left sidebar (`<aside>`),
  dark (`bg-bg`, matching the page canvas), holding the app name, the
  "Generate now" button, then the three nav links stacked vertically —
  icon + label each, active item filled `bg-accent`.
- **Mobile (below `sm:`, tested down to 390px)**: the sidebar is hidden;
  a bottom bar (`fixed inset-x-0 bottom-0`) holds the same three
  icon-over-label links, and a floating circular "Generate now" button
  sits just above it. Both respect `env(safe-area-inset-bottom)` so a
  device's home-indicator area doesn't overlap them.

Main content (`<main>`) always sits on the dark `bg` canvas; every actual
content block is its own light `surface` `Card` — that's the "dark
background with light content cards" instruction applied structurally, not
just as a colour choice on one page.

### Generate now

`GenerateButton.jsx` + `hooks/useGenerateEpisode.js` — lives in the app
shell (not the Episodes page), so it's the one prominent, always-visible
primary action from anywhere in the app, with an `AudioWaveform` icon and a
`Loader2`-spinner "Starting…" running state while `POST /api/episodes` is in
flight. Since it's now a sibling of `Episodes.jsx` rather than its parent,
cross-page refresh is a plain `window` `CustomEvent`
(`podcast:episode-created`) rather than a prop/callback — the simplest
thing that works for one signal, see `useGenerateEpisode.js`'s own comment
for why this wasn't wired up as a React context instead.

### Episode cards

`EpisodeCard.jsx` (replacing the old `EpisodeRow.jsx`): title is the
script's own title (`EpisodeSummary.title`, added server-side — see
`routes_episodes._load_title`, reading `critique.json`'s `revised_script`
or `script.json`, whichever exists), falling back to the raw `episode_id`
only when no script has been written yet (still pending, or failed before
scripting). Date, duration (`m:ss`, from `duration_s`), and a `StatusBadge`
sit under the title; a labelled **Details** button (chevron + text, not a
bare `▲`/`▼`) expands the card in place. `Episodes.jsx` splits the list at
`created_at` >= 14 days ago vs. older, rendering the older half under a
collapsed **Older (N)** disclosure, closed by default.

### Audio player

`AudioPlayer.jsx` is the hero of an expanded episode card: a large circular
play/pause button, the title and a `current / total` time readout next to
it, and a full-width `<input type="range">` progress bar/scrubber beneath
— an actual custom transport, not a restyle of the native `<audio
controls>` element (which has no CSS hooks for this layout; see
docs/decisions.md, "Episode card: hero player, collapsed sections", for
why that restyle-only approach was retired). A hidden `<audio>` element
(ref'd, not `controls`) does the real decoding/playback; the button/range
just drive its `play()`/`pause()`/`currentTime`. `durationSeconds` (from
the episode's own `duration_s`) seeds the displayed total before the
audio's `loadedmetadata` fires, so the readout doesn't start at "0:00".

### Transcript

`ScriptView.jsx` takes a `hosts` prop (the two entries from
`profile.podcast.hosts`, fetched once by `Episodes.jsx` and passed to every
card) purely to colour-match speakers: every line (cold open, each segment
under its own headline, outro) gets a small `HostAvatar`
(initial-in-a-circle, coloured `accent`/`ink` by host index — always
exactly two hosts, so two palette colours is exactly enough, see
`PodcastSettings`'s `_validate_hosts`) plus a colour-matched speaker name,
so a reader can follow who's talking without re-reading names.

Host bios (avatar + a one-line persona summary — the first non-empty line
of `persona`, the same "brief, not the full paragraph" convention
`outline.py`'s `_render_host_brief` already uses server-side) used to be
part of `ScriptView.jsx` itself; they're now `HostBios.jsx`, rendered in
their own "About the hosts" disclosure on the card, independent of whether
Transcript is open — see "Episode card" below.

### Episode card: hero player, collapsed sections

An expanded card's `AudioPlayer` (see above) is followed by four
`Disclosure.jsx` sections, collapsed by default: **Quality**
(`QualityPanel` — see below), **Transcript** (`ScriptView`), **Sources**
(`ShowNotes` — the episode's cited articles), and **About the hosts**
(`HostBios`). Each disclosure owns its own open/closed state, so opening
one doesn't affect the others, and every card starts fully collapsed —
the player is what the card leads with. See docs/decisions.md ("Episode
card: hero player, collapsed sections").

### Quality panel

`QualityPanel.jsx` renders `EpisodeDetail.quality` (`null` until the
episode has reached the `quality` stage — still earlier in the pipeline,
failed before reaching it, or predates this feature, in which case the
panel just says so). A visible disclaimer banner at the top — "these are
automated proxies, not a quality guarantee" — is not optional styling; the
metrics are genuinely limited in what they can measure (see
docs/decisions.md, "Quality metrics", for the full reasoning per metric).
Below it: the judge's two 1-5 scores with their one-line reasons
(`JudgeScoreBlock`), then two small stat grids — Grounding (sources,
evergreen share, critique flags/rewrite rate, fact-drift flags, retried
stages) and Naturalness proxies (audio-tag density, interjection/dash
share, host balance, catchphrase count) — each less-obvious stat carrying
a `MetricTooltip` "i" explaining what it measures and can't measure,
reusing the same tooltip component the dashboard's KPI cards already use.

### Settings

Each `Card` in `Settings.jsx` takes an `icon` prop (`Tv`/`Tags`/`Mic2`/
`Sparkles`/`Calendar` from `lucide-react`) rendered next to its title —
`Card.jsx` grew that prop generically, not just for Settings.

**Interest weight** (`WeightChoice.jsx`) replaced the 0–1 slider with three
buttons — Low/Medium/High — mapped to fixed `0.4`/`0.7`/`1.0` under the hood;
`podcast.models.Interest.weight` is still the same
`float(ge=0, le=1)` it always was, so nothing server-side changed. A weight
saved before this change (e.g. `0.5`) simply shows no button selected until
the user picks one — it isn't silently snapped to the nearest choice.

### Empty states

Every list that can be empty says what to do next in one sentence, not just
"nothing here": no episodes ("use the Generate now button…"), no interests
("add one below…"), no hosts ("this podcast needs exactly two…"), zero
dashboard data ("generate an episode… to see KPIs here"), on top of the
show-notes/script/recent-failures empty states that already existed.

### Screenshots

`docs/screenshots/` — `{settings,episodes,dashboard}-{desktop,mobile}.png`,
desktop captured at 1440×900 and mobile at 390×844 (iPhone 12/13-class
width), against a locally running `podcast serve` + built `web/dist`, using
whatever real episodes/profile already existed in `data/` (no synthetic
demo content staged for the screenshots). Linked from the README's
Screenshots section.

Captured via Chrome DevTools Protocol (`Emulation.setDeviceMetricsOverride`
+ `Page.captureScreenshot`), not the simpler `chrome --headless
--window-size=W,H --screenshot=file.png` one-liner — that flag silently
clamps the *layout* viewport to some OS-imposed minimum window width
(~500px on this machine) while still writing a PNG at the requested
dimensions, so a naive `--window-size=390,844` capture produces a
390px-tall-looking file that was actually laid out at ~504px and then
cropped, not a true mobile-width render (it manifested as every card's
right-edge padding silently vanishing off-frame). `Emulation.
setDeviceMetricsOverride` sets the CSS viewport directly over the CDP
connection, independent of the OS window, sidestepping that clamp
entirely. Worth remembering if this ever needs re-capturing.
