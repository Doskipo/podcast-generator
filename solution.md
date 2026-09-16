# Solution

A condensed write-up of what was built and why. The full, dated reasoning behind every decision
below, including the ones that turned out wrong on the first try, lives in
[`docs/decisions.md`](docs/decisions.md); this file is the summary a reviewer would want before
diving into that log or the code.

## What it does

Given a profile of interests (topics, weights, per-interest search queries), the app fetches recent news/papers, ranks and budgets them per interest, plans an
episode outline, writes a two-host dialogue script grounded in the fetched sources, reviews and
rewrites it for correctness and naturalness, rewrites it again for performance (humanizing), and
synthesizes it with ElevenLabs into an mp3.

## Architecture

Pipeline: **fetch → rank → outline → script → critique → perform → tts → stitch**
(`src/podcast/stages/`). Each step in the process works completely independently. It takes in and out a strictly defined format of data (Pydantic), saves its own work in the episode's folder under `data/episodes/<episode_id>/`, and doesn't rely on or alter any hidden background information.

That buys two things directly: any stage can be
re-run in isolation from its predecessor (`podcast.generate --from-<stage>`, or `--until
<stage>` to stop early), and every intermediate decision (which articles were scored and why,
what the outline's narrative plan was, what critique flagged, what changed between content and
performance) can be reviewed later instead of only living in a model's small context.

The file `podcast/service.py` acts as the central manager for the entire application. Instead of having different parts of the system figure out how to run the podcast generation on their own, both the command-line tool (the CLI, specifically `podcast/generate.py`) and the web API (the `POST /episodes` background task) simply hand the job over to this single file. This avoids duplicating orchestration logic, meaning podcast/service.py alone is responsible for deciding the exact order of operations (sequencing stages), saving all progress and data (DB bookkeeping), and broadcasting signals when tasks happen (emitting events). This entire engine is powered by a FastAPI backend, which stores its data in a lightweight SQLite database using SQLModel and relies on APScheduler to act as an automated timer for the daily cron job.

On the visual side, the interface you actually interact with is a Vite + React SPA (Single Page Application). Normally, a website requires one server to handle the backend logic and a completely different server to display the graphics, but in this setup, the exact same FastAPI app delivers the website directly to the user,meaning there is no separate frontend server in production. This interface gives you everything you need in one place, providing a settings editor, an episode list with built-in audio playback, and a metrics dashboard to monitor performance.

## Grounding: the one rule enforced everywhere

To "never invent facts not in the sources" is the golden rule applied in all layers of creating the script. To prevent hallucinating on sources, `script_stage`/`outline_stage` reject any `source_ids`
outside the known article pool; `critique_stage` and `perform_stage` re-validate the same
invariant on every rewrite, and `perform_stage` additionally overwrites each segment's
`source_ids`/`headline` from the original in code rather than trusting the model's echo. In case the rank selects zero articles for every interest, the pipeline stops at a distinct `no_content` status rather than letting the model write around an empty source (the one real grounding failure that made it to a live run, see "Three fixes from the first real run" in the decision log).

## Key trade-offs

- **Discovery.** The system pulls content from hand-picked RSS feeds whenever possible, such as the arXiv category feeds. For everything else, it relies on Bing News RSS. Bing was chosen over Google News because of a real-world test: when trying to extract the text from a sample of 40 articles, Bing succeeded 93% of the time, while Google failed completely at 0%.
The system also uses different timeframes to decide how old an article can be. For curated feeds that publish daily, it only looks at the last 48 hours. However, for articles found through search, it looks back a full week (168 hours). This longer window is necessary because, for niche or low-volume topics, Bing mostly returns timeless, deep-dive background articles (evergreen explainers) rather than breaking, day-fresh news.
- **Never-empty episodes.** An interest with zero real candidates gets a Wikipedia-grounded
  "primer" segment instead of silently contributing nothing (`podcast/evergreen.py`) although still
  subject to the same grounding rule, just from a different source, and capped by a 7-day
  anti-repeat cache so the same primer doesn't play twice in a row.
- **Four-pass script writing, not one.** 
  - Outline (cheap model, narrative planning)
  - Script (stronger model, prose) 
  - Critique (stronger model, targeted line-level review)
  - Perform (stronger model, full-script rewrite for voice + a cheap-model fact-check pass)

  Split out after the first real script came back reading like a cambridge listening exam. Each pass has
  a narrower, better-specified job than "write a good, natural, grounded, well-paced script" in
  one shot.
- **Content vs. performance, as separate stages.** Critique fixes what's wrong (robotic lines,
  invented details, ungrounded claims); perform is a distinct, later pass that rewrites for
  spoken delivery only (flow punctuation, spoken lists, emphasis, a delivery arc, inline v3 audio
  tags, cold-open chit-chat).
- **Dialogue-mode TTS, with a fallback.** ElevenLabs' `text_to_dialogue` endpoint synthesizes a
  whole chunk as one continuous conversation (better to slice a good prose than get independently uncontextualized clips), sliced back into per-line files via its own timestamps; falls back to
  per-line `text_to_speech` on any failure. Filenames are content-addressed (hash of the exact
  synthesized text) so a stale clip from an earlier script version can never be mistaken for the
  current run's line at the same position (a real bug caught mid-project).

These two points need rewrite.
- **Backend: SQLite and a background task, not Postgres and a queue.** Something has to store
  the profile, run generation on a timer, serve the mp3 and record what happened, so there is a
  backend. But this is a single-listener product: a file database needs no server to operate,
  and a background task inside the API process needs no worker infrastructure. The cost is
  named rather than hidden: if the process dies mid-episode the row stays `running` (now
  reconciled to `failed` on startup), and two simultaneous requests are not serialised. At
  multi-user scale the pipeline itself would not change, only what runs it: Postgres for the
  tables, a job queue with workers for generation, object storage for the artefacts.
- **Dashboard costs are computed from the artefacts on request, not cached.** Every stage
  already records its token and character usage in its own manifest, so the manifests are the
  source of truth and the dashboard reads them rather than maintaining a second copy that could
  drift. Mocked demo data lives only in the database, is flagged `mocked=True`, never lands on
  today's date, and is excluded from the Episodes page and from the KPIs unless explicitly
  toggled on — so a reviewer can never mistake seeded usage for real spend.


## Costs and numbers

Measured on the three real episodes generated with the full pipeline (the dashboard computes
these from the persisted stage manifests and stage events; pricing constants are
order-of-magnitude, not an invoice).

- Cost per episode: $1.50–1.95, mean $1.67. ElevenLabs synthesis is 95% of it (v3 dialogue
  model, 8,000–10,000 characters per episode); OpenAI is 5%, about $0.08 per episode, of which
  perform (45%) and script (28%) dominate and rank plus outline are under a cent. The
  trade-off is deliberate: the expensive TTS model is what makes the episode listenable, and
  at one episode per day it is an acceptable unit cost. If cost mattered more, the first lever
  is the TTS model, not the LLM stages.
- Generation time: about 6 minutes for an 8-minute episode (12 minutes for the 11'49" one,
  since synthesis scales with characters). TTS is 68% of the time; the four LLM stages take
  roughly 25–55 seconds each; fetch and stitch are seconds. Generation runs as a background
  task, so the user never waits on it.
- Words per minute: calibrated at 139.6 from completed episodes, but that measurement was
  taken over runs that included faster, flatter synthesis. With v3 dialogue, audio tags,
  varied pauses, interruptions, the real rate is closer to 100, so a 7-minute target came
  out at 9'45". The budget mechanism works (the script lands within a few words of its
  budget); the constant is wrong for the current voice model. Recalibrating it per TTS model
  is the first thing I would fix.
- Dashboard metrics are chosen to answer four questions: does anyone listen to the end
  (completion rate), do they come back (D7 retention), what does each episode cost (cost by
  stage and provider), and what breaks (failures and no-content episodes). Real and mocked
  usage are never mixed: the KPIs show real episodes by default with an explicit toggle for
  the seeded demo data.

## Known simplifications / out of scope

- When an interest has fewer candidates than its budget, the unused slots are not
  redistributed to other interests, so an episode can come out shorter than planned.
- A background task killed mid-run leaves the episode as `running`; there is no
  reconciliation on restart.
- Two simultaneous generation requests are not serialised.
- The voice catalogue is a hardcoded list rather than a live ElevenLabs call, because the
  development key lacks the `models_read` permission.
- Pricing constants are illustrative, not measured against a real invoice.
- Some interests are not news-shaped (calisthenics lives on YouTube and Reddit, not in news
  outlets); the evergreen primer covers the gap rather than solving it.
- Generated search queries are visible only when the Suggest button returns them; they are
  cached on the profile but not editable in the UI afterwards.
- The voice catalogue assumes a paid ElevenLabs plan (library voices and the v3 dialogue
  endpoint are not available on the free tier); the app should check the account's tier and
  only offer voices the key can actually use.
- No quota check before synthesis: the provided ElevenLabs key ran out mid-run on the final
  day, which the pipeline handled correctly (the script and performance artefacts survived,
  so the episode was resumable with `--from-performance`), but the app should surface
  remaining provider quota before starting an expensive stage.


## Future work

- Topic suggestions from a listener's own history: which stories they finished, which they
  skipped; a small endpoint over the events table.
- Per-content-type source adapters (subreddit RSS, YouTube channel RSS, newsletters) for
  interests that never appear in news search.
- Multi-user: Postgres for the tables, a job queue with workers for generation, object
  storage for artefacts; the pipeline itself would not change, only what runs it.
- Better story linking: the outline sometimes forces connections between unrelated topics;
  it should decide per episode whether to link stories or transition cleanly.
- Voice: multiple takes per line with automatic selection, and voice cloning for hosts.
- Sharing: episodes, and even host personas, could be shared between listeners.

## How I used AI tools

Claude Code (Sonnet) wrote most of the code from my prompts, one stage per task, with a
plan I reviewed before each write, tests it had to keep green, and a decisions log it had
to append to. Claude (chat) was my planning and review partner: day plans, prompt drafts,
reading each result, and pushing back on my proposals.

What I owned: the architecture (stages with persisted artefacts, grounding as a hard rule),
the personas and listener profile, every listen-and-diagnose loop on the audio, the
priority calls (script craft over UI polish), and the review of each change before commit.

Two things the tools got wrong that I caught: Claude Code deleted `data/episodes/` while
cleaning up its own test database, which cost me an afternoon of regenerated episodes and
earned a "never delete outside your own scratch paths" rule in `CLAUDE.md`; and it once
drafted a log entry claiming to have verified the Docker build locally when it had not,
then corrected itself. Both are why every real run in this project was executed and
listened to by me, not reported by the tool.