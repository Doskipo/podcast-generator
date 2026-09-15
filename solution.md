# Solution

A condensed write-up of what was built and why. The full, dated reasoning behind every decision
below — including the ones that turned out wrong on the first try — lives in
[`docs/decisions.md`](docs/decisions.md); this file is the summary a reviewer would want before
diving into that log or the code.

## What it does

Given a profile of interests (topics, weights, optional curated feeds, per-interest search
queries), the app fetches recent news/papers, ranks and budgets them per interest, plans an
episode outline, writes a two-host dialogue script grounded in the fetched sources, reviews and
rewrites it for correctness and naturalness, rewrites it again for how it should sound spoken, and
synthesizes it with ElevenLabs into an mp3 — end to end, unattended, on a schedule or on demand.

## Architecture

Pipeline: **fetch → rank → outline → script → critique → perform → tts → stitch**
(`src/podcast/stages/`). Every stage is a pure function — typed Pydantic input in, typed Pydantic
output out — that persists its own artefact under `data/episodes/<episode_id>/` and never touches
state a later stage doesn't hand it explicitly. That buys two things directly: any stage can be
re-run in isolation from its predecessor's artefact (`podcast.generate --from-<stage>`, or `--until
<stage>` to stop early), and every intermediate decision (which articles were scored and why,
what the outline's narrative plan was, what critique flagged, what changed between content and
performance) is inspectable after the fact instead of only living in a model's ephemeral context.

`podcast/service.py` is the one place that sequences stages, does DB bookkeeping, and emits events
— both the CLI (`podcast/generate.py`) and the API (`POST /episodes`, background task) call into
it rather than duplicating orchestration logic. A FastAPI backend (SQLite via SQLModel,
APScheduler for the daily cron job) sits in front of the pipeline; a Vite + React SPA (served by
the same FastAPI app in production, no separate frontend server) provides a settings editor, an
episode list with playback, and a metrics dashboard.

## Grounding: the one rule enforced everywhere

"Never invent facts not in the sources" is enforced in code, not just asked for in a prompt, at
every layer that could otherwise drift: `script_stage`/`outline_stage` reject any `source_ids`
outside the known article pool; `critique_stage` and `perform_stage` re-validate the same
invariant on every rewrite, and `perform_stage` additionally overwrites each segment's
`source_ids`/`headline` from the original in code rather than trusting the model's echo — grounding
is structural, not requested. When rank selects zero articles for every interest, the pipeline
stops at a distinct `no_content` status rather than letting outline write around an empty source
list (the one real grounding failure that made it to a live run — see "Three fixes from the first
real run" in the decision log).

## Key trade-offs

- **Discovery.** Curated RSS feeds where available (arXiv category feeds); Bing News RSS for
  everything else, chosen over Google News RSS after a real extraction-rate comparison (93% vs.
  0% articles successfully extracted from 40 sampled). Per-interest freshness windows differ
  (48h for daily-publishing curated feeds, 168h for search-discovered ones) after discovering
  Bing search returns mostly evergreen explainers, not day-fresh news, for low-volume topics.
- **Never-empty episodes.** An interest with zero real candidates gets a Wikipedia-grounded
  "primer" segment instead of silently contributing nothing (`podcast/evergreen.py`) — still
  subject to the same grounding rule, just from a different source, and capped by a 7-day
  anti-repeat cache so the same primer doesn't play twice in a row.
- **Three-pass script writing, not one.** Outline (cheap model, narrative planning) → script
  (stronger model, prose) → critique (stronger model, targeted line-level review) → perform
  (stronger model, full-script rewrite for voice + a cheap-model fact-check pass) — split out
  after the first real script came back reading like an exam listening transcript. Each pass has
  a narrower, better-specified job than "write a good, natural, grounded, well-paced script" in
  one shot.
- **Content vs. performance, as separate stages.** Critique fixes what's wrong (robotic lines,
  invented details, ungrounded claims); perform is a distinct, later pass that rewrites for
  spoken delivery only (flow punctuation, spoken lists, emphasis, a delivery arc, inline v3 audio
  tags, cold-open chit-chat) — never touching meaning, checked by re-validating structure and a
  dedicated fact-drift pass. It's the last stage to touch line text before synthesis.
- **Dialogue-mode TTS, with a fallback.** ElevenLabs' `text_to_dialogue` endpoint synthesizes a
  whole chunk as one continuous conversation (better cross-turn prosody than independently
  synthesized clips), sliced back into per-line files via its own timestamps; falls back to
  per-line `text_to_speech` on any failure. Filenames are content-addressed (hash of the exact
  synthesized text) so a stale clip from an earlier script version can never be mistaken for the
  current run's line at the same position — a real bug caught mid-project.
- **Backend: SQLite + a background task, not Postgres + a queue.** Single-user take-home scope —
  no ops for a file DB, no worker infra for `BackgroundTasks`. Named, deliberate limitations: no
  crash recovery for a `status="running"` row, no concurrency control across simultaneous
  `POST /episodes` calls. What changes at real multi-user scale is called out explicitly in the
  decision log rather than left implicit.
- **Dashboard cost is computed from persisted manifests at request time**, not cached — matches
  "every stage's output is the source of truth on disk" and the task's own framing. Mocked demo
  data (`podcast seed-metrics`) is DB-only (no fake manifest files) and structurally distinct from
  real data (`mocked=True`, never lands on today's date), so the two can never be confused.

## Known simplifications / out of scope

- No cross-interest redistribution when one interest has fewer candidates than its rank budget.
- No retry/reconciliation for a background task killed mid-run (`status="running"` sticks).
- No concurrency control on simultaneous episode generation requests.
- Voice catalog is a hardcoded list, not a live ElevenLabs call (an early permissions check found
  the API key used during development lacks `models_read`).
- Cost figures (OpenAI token pricing, ElevenLabs per-character pricing) are illustrative
  order-of-magnitude constants, not measured against a real bill.

## Future work

- Suggest topics/interests from a listener's own previous episodes, rather than only hand-entered
  interests.
- Per-content-type source adapters (subreddit RSS, YouTube channel RSS) for topics that live
  outside news-shaped search results (e.g. calisthenics), flagged during the discovery-provider
  comparison as real volume gaps Bing News search alone can't close.
