# Decisions log

## Day 1 — 2026-09-09
- Pipeline as pure stages with persisted artefacts per episode (debuggability, dashboard later).
- RSS only for now; scraping deferred (paywalls, rate limits, legality) — revisit day 3.
- Structured output with source_ids per segment from day one: grounding hook.

## Fetch stage — 2026-09-09
- Dedupe by normalized URL (strip query/fragment/trailing slash) AND normalized
  title (lowercased, punctuation collapsed), across all feeds combined — cheap,
  catches syndicated duplicates across feeds without a similarity model.
- `source_id` = first 8 hex chars of sha1(url). Stable across re-runs, short
  enough for the script stage to cite inline.
- If trafilatura extraction fails or returns <200 chars, the article is dropped
  entirely rather than kept with empty/thin text — the "never invent facts"
  rule means a stage downstream must not be handed something to hallucinate
  around.
- `window_hours` (default 48) and `max_entries_per_feed` (default 30, newest
  first) are profile-configurable via `profile.fetch`, so per-feed noise
  doesn't force full-text extraction on more than needed.
- Each feed is parsed in its own try/except; a broken/unreachable feed is
  logged and skipped rather than failing the whole run.
- Extraction (the networked, expensive step) runs last, after date-window
  filtering, dedupe, and the per-feed cap — currently that's also "after
  everything else in this stage" since there's no ranking stage yet. Once a
  rank stage exists, extraction should move *after* it, so we only fetch full
  text for the top-N candidates instead of every deduped item in the window —
  revisit when rank is built.
- There's no ranking in "quality" of the data fetched.

## Script stage — 2026-09-09
- Host roles are assigned positionally from `profile.podcast.hosts`: `hosts[0]`
  drives each segment (narrates, introduces the news), `hosts[1]` asks questions
  and adds context/reactions. Simple, no extra profile schema needed; revisit if
  a profile ever wants the roles configurable independently of list order.
- Grounding (`segment.source_ids` ⊆ known article ids, no facts outside the cited
  texts) is enforced in code after generation, not via the response schema —
  OpenAI structured outputs can validate shape but can't express "subset of a
  dynamic id list known only at request time." The model is told the known ids
  in the prompt; `script_stage` then diffs every id actually used against that
  set and raises if anything doesn't match.
- Each article is truncated to ~1500 chars in the prompt (`ARTICLE_TEXT_CHARS`)
  — enough to ground several claims per article without blowing up prompt size/
  cost when a full fetch window's worth of articles are all included.
- The OpenAI call itself is isolated behind `_generate_script(client, model,
  system_prompt, user_prompt) -> Script`; tests monkeypatch this function
  directly rather than faking the OpenAI response-object shape, so no network
  call happens in the test suite.
- `--from-articles <path to articles.json>` re-runs script -> tts -> stitch: it
  skips fetch, derives the episode id from the artefact's parent directory, and
  loads that episode's `episode.json` for the profile snapshot — lets the script
  prompt/model be iterated on without re-fetching.


## Diagnosis on the first script — 2026-09-09
- The hosts have roles but no personas: no bio, no opinions, no verbal habits. "Nova drives, Max asks" produces an interview transcript, not a conversation.
- No angle per segment: it lists specs. A podcast segment has a beat structure: what happened → why it matters → what's the catch / what the host thinks → what to watch. The prompt never asked for opinion or tension, so there is none.
- No style knob: humour, depth, energy are exactly the "customise some aspects of the podcast" the assignment wants in the UI. That's your product hook: a style block in the profile (humour 0–3, depth 1–3, banter on/off, a "reference the listener's interests" flag) that changes the system prompt.
- Cheap model with no examples. gpt-4o-mini is fine for the skeleton; for the final episode you'll use a stronger model plus a few example lines showing the voice you want.

Tomorrow's plan for this stage is probably: outline step (LLM picks stories and an angle for each) → script step with personas and style → self-critique pass.

## TTS stage — 2026-09-09
- Voice mapping is `profile.tts.voices: dict[speaker -> voice_id]`, defaulting
  to placeholder ElevenLabs premade voice ids for the two demo hosts (Nova,
  Max) so the skeleton profile works without extra config. A speaker used in
  the script but missing from the map raises immediately rather than silently
  falling back to some default voice — a wrong voice for a named host is worse
  than a loud failure telling you to add it to the profile.
- One file per line, named `segments/<index>_<speaker>.mp3` (index zero-padded
  to 3 digits) in script order (cold_open, then each segment's lines, then
  outro) — the index is what stitch uses to reconstruct order, independent of
  any other Line metadata.
- If a line's file already exists, synthesis is skipped for that line — makes
  re-runs after a partial failure (or a script tweak affecting only some
  lines) cheap, since ElevenLabs calls are billed per character.
- `tts_manifest.json` records index, speaker, file path, and character count
  per line (not audio duration) — character count is what ElevenLabs bills on,
  so this is the hook for cost tracking later.
- The ElevenLabs call is isolated behind `_synthesize(client, voice_id,
  model_id, text) -> bytes`; tests monkeypatch this directly, so no network
  call happens in the test suite.

## Stitch stage — 2026-09-09
- Plain pydub concatenation, reading `tts_manifest.json` in order and joining
  each line's mp3 with a 400ms silence gap between lines (none before the
  first or after the last). No crossfade/normalization yet — revisit if the
  seams sound too abrupt once real ElevenLabs audio is in the mix.
- No monkeypatching needed here: pydub/ffmpeg run locally with tiny
  fixture clips (short silent mp3s generated on the fly in the test), so
  there's still no network call and no external service to fake.
- Output is `episode.mp3` (the deliverable) plus `stitch_manifest.json`
  (episode_id, audio_file, duration_ms) for the same debuggability/
  re-run-from-artefact reason every other stage persists its output.

## API key handling — 2026-09-09
- `script_stage` and `tts_stage` construct their client (`OpenAI`/`ElevenLabs`)
  explicitly with `api_key=os.environ[...]` rather than letting the SDK read
  the env var implicitly, and only when no `client` was passed in (tests keep
  injecting a fake). Added a tiny `podcast/env.py:require_env(name)` shared by
  both, so a missing key raises a clear `RuntimeError` naming the variable, at
  the very start of the stage — before building any prompt or paying for any
  synthesis — instead of surfacing as an opaque SDK auth error mid-run.

## Cleanup — 2026-09-09
- `[project.scripts]` now points `podcast` at `podcast.generate:main` instead
  of the skeleton placeholder, and `src/podcast/__init__.py` is back to just a
  module docstring — the placeholder `main()` had nothing pointing at it once
  the entry point was fixed.


## Observations on the first mp3 — 2026-09-09
- it feels more like an english listening as a cambridge exam. Same tone, very robotic, 
respecting always the time to speak... not human-like.
- the roles are too defined, the woman gives informaton, the man asks. The roles 
  should not be super rigid, it is a human conversation.
- very linear in explaining information: conversation ussually behave like a tree,
  where it sometimes lead to talking about things that do not have nothing in 
  common about the main topic. I know the purpose its to stick to the preferences,
  but sometimes do life analogies, or personal experiences, that are somehow related
  make the conversation very humanlike. I dont want pure information; I want naturality
  and dopamine. 
- also add the same diagnosis of the script without hearing it about the humor...



## Things to take into account first thing tomorrow — 2026-09-09
- a missing key should abort in a second with a readable message, not halfway through a paid pipeline with a stack trace.
- the arXiv SSL fix (httpx download → feedparser) is first on tomorrow's list
- pydub's audioop dependency is a known risk that pins you to Python 3.12.

## Fetch stage — arXiv SSL fix — 2026-09-10
- `feedparser.parse(url)` does its own HTTP(S) fetch via urllib, which on
  Windows uses the system cert store rather than certifi's CA bundle — arXiv's
  TLS chain fails verification against it, so both `rss.arxiv.org` feeds were
  silently dropped by the existing per-feed try/except every run.
- Fix: added `_download_feed(url)`, downloading each feed's body with httpx
  (15s timeout, a descriptive User-Agent, redirects followed,
  `raise_for_status()` on non-2xx) and passing the decoded text to
  `feedparser.parse()` instead of a URL — feedparser accepts a raw content
  string just as well as a URL, and httpx is certifi-backed and consistent
  across platforms. The per-feed try/except now wraps the download too, so a
  broken/unreachable feed is still logged and skipped rather than failing the
  run.
- `httpx` was already resolved transitively (via `elevenlabs`); promoted it to
  a direct dependency in `pyproject.toml` since `fetch.py` now imports it
  directly.
- Tests patch `fetch_module._download_feed` instead of `feedparser.parse`, so
  the real feedparser now runs against the (fixture) text in tests, closer to
  the real code path.

## Interest-driven discovery: Bing News RSS vs Google News RSS — 2026-09-10
- Product change: a profile's `interests` are now the unit of discovery, not
  just narrative flavor. `Interest` gained `query: str | None` (falls back to
  `topic` via a `search_query` property) and `feeds: list[HttpUrl]` (curated
  feeds for that interest — e.g. arXiv category feeds for "interpretability").
  `Profile.feeds` is now optional and just layers extra feeds on top, defaulting
  to empty — a user types interests, not RSS URLs.
- An interest with no curated `feeds` gets discovered via a generated news-search
  RSS feed built from its `search_query`, instead of requiring a hand-picked URL.
- Compared Bing News RSS (`bing.com/news/search?q=...&format=RSS`) against
  Google News RSS (`news.google.com/rss/search?q=...`) with a throwaway script
  against four real topics — calisthenics, League of Legends esports, "Olivia
  Dean and Sienna Spiro new music", mathematics of machine learning — sampling
  up to 10 entries per topic/provider, following each entry's link (httpx,
  redirects followed) to see if it resolves off the provider's own domain, then
  running the same trafilatura extraction the fetch stage uses:

  | topic (bing / google)                  | entries | resolved | extracted |
  |-----------------------------------------|---------|----------|-----------|
  | calisthenics — bing                     | 11      | 9        | 9         |
  | calisthenics — google                   | 100     | 10       | 0         |
  | LoL esports — bing                      | 12      | 10       | 9         |
  | LoL esports — google                    | 100     | 10       | 0         |
  | Olivia Dean / Sienna Spiro — bing        | 4       | 4        | 3         |
  | Olivia Dean / Sienna Spiro — google      | 55      | 10       | 0         |
  | maths of ML — bing                      | 4       | 4        | 4         |
  | maths of ML — google                    | 77      | 10       | 0         |

  Totals: **bing 25/27 sampled articles extracted (93%)**; **google 0/40**.
  Google's `<link>` redirects do resolve off `news.google.com` (httpx follows
  them to a 2xx on a different host), but wherever they land isn't the real
  publisher article — trafilatura extracted nothing usable from any of the 40
  sampled. Google has far more raw entries (55–100 vs. bing's 4–12) but that
  volume is useless if none of it extracts. Bing wins outright on the metric
  that matters (extraction success); implemented Bing only, per the "implement
  one" instruction — no Google News code path exists.
- `_build_search_feed_url(query)` in `fetch.py` builds the Bing URL;
  `_feed_plan(profile)` resolves each interest to its curated feeds (tagged
  `source="curated"`) or one generated search feed (tagged
  `source="bing_news_search"`), then appends `profile.feeds` as extra curated
  feeds — `fetch_stage` iterates this plan instead of `profile.feeds` directly.
  `_download_feed`, the per-feed try/except, and the dedupe/date-window/cap
  logic are all reused unchanged regardless of a feed's source.
- `Article` gained a `source` field (`"curated"` or `"bing_news_search"`) so
  downstream/debugging can tell where each article was found. Defaults to
  `"curated"` so pre-existing persisted `articles.json` files (from before this
  field existed) still validate through `--from-articles`.
- `profiles/eudald.yaml` rewritten around five interests: `interpretability`
  (curated arXiv `cs.LG`/`cs.AI` feeds, as before) plus four new
  search-discovered interests (`mathematics of machine learning`,
  `calisthenics`, `League of Legends esports`, `Olivia Dean and Sienna Spiro
  new music`). Dropped the old "dynamical systems"/"ML theory and
  generalisation"/"AI industry news" interests and the `hnrss`/`alignmentforum`
  top-level feeds — the new interest list replaces them; top-level `feeds` is
  left empty for now.
- **Caveat found running the real profile end-to-end (fetch stage only):** all
  60 surviving articles came from the two curated arXiv feeds; every
  search-discovered interest (all four new ones) produced 0 articles. Checked
  entry dates directly — Bing News search for generic/evergreen topics like
  "calisthenics" and "mathematics of machine learning" mostly returns
  explainer/reference articles months to years old, not day-fresh news, so
  `profile.fetch.window_hours` (default 48h, designed for feeds like arXiv/HN
  that actually publish daily) drops nearly all of them. "League of Legends
  esports" was the exception (frequent real news), but its one in-window entry
  failed extraction — Bing wraps some links in an `apiclick.aspx` redirect that
  landed on an MSN page trafilatura couldn't parse. Not fixed here (would mean
  a longer/per-interest window and/or steering `query` toward more
  event-shaped phrasing, e.g. "calisthenics competition results" — a product
  decision, not a bug in this change) — flagging for the next iteration.

## Freshness gap fix: per-interest window + generated event-shaped queries — 2026-09-10
- **(1) `window_hours` moved from `profile.fetch` to per-`Interest`.**
  `Interest.window_hours: int | None` overrides; unset falls back to
  `Interest.effective_window_hours`, which is `DEFAULT_CURATED_WINDOW_HOURS`
  (48h) when the interest has curated `feeds`, else `DEFAULT_SEARCH_WINDOW_HOURS`
  (168h) — both constants live in `models.py`. `FetchOutput.window_hours` was
  removed (it can no longer describe one number for the whole fetch);
  `_candidates_for_feed` now takes `(now, window_hours)` and computes its own
  cutoff instead of `fetch_stage` computing one global cutoff up front.
- **(2) Multi-query generation, cached in the profile file itself.**
  `Interest.queries: list[str] | None` replaces the single `query` field from
  the last change. When an interest has no curated feeds and `queries` is
  unset, `ensure_interest_queries(profile, profile_path)` (in `fetch.py`) asks
  `profile.llm.model` for `NUM_GENERATED_QUERIES` (3) event-shaped queries —
  one OpenAI structured-output call per interest that needs it, isolated
  behind `_generate_queries(client, model, topic)` the way `script_stage`
  isolates its own OpenAI call, so tests monkeypatch it and never hit the
  network. Generated queries are written straight into `interest.queries` and
  the whole profile is serialized back to `profile_path` (`Profile.to_yaml`,
  the write-side counterpart to `from_yaml`) — so the *next* `Profile.from_yaml`
  load already has them and skips the call entirely: one call per profile, not
  per run. `generate.py`'s `run()` calls this right after loading the profile,
  before fetch. `_feed_plan` now fetches one Bing feed per query (falling back
  to the bare topic if `ensure_interest_queries` was never called, e.g. tests
  calling `fetch_stage` directly) and dedup collapses overlap across them the
  same way it already collapses overlap across any other feeds.
- **Bug found and fixed along the way: `_normalize_url` was stripping the
  entire query string** before dedup, not just tracking params. That's fine
  for `?utm_source=...` noise on a direct article link, but Bing wraps most
  search results as `bing.com/news/apiclick.aspx?...&url=<the real target>&...`
  — the query string *is* the only thing that makes the link distinct. Blindly
  dropping it collapsed every Bing-wrapped link across every interest in the
  same run into one fake duplicate, silently discarding real in-window
  candidates (this alone was hiding most of the fix's benefit — League of
  Legends esports had 3 in-window candidates before this fix but only 1 ever
  reached extraction). Now only a fixed deny-list of tracking params
  (`utm_*`, `fbclid`, `gclid`) is stripped; everything else in the query
  string, including a wrapper's real-target param, survives into the dedup key.
- Also nudged `_generate_queries`'s prompt to include today's date and
  explicitly tell the model not to hardcode a year — the first generation
  (before this nudge) produced queries like "...2023" and "...schedule 2023"
  from a model with no notion that it's now 2026, which actively hurt
  freshness by anchoring searches to a stale year.
- **Re-ran fetch-only against the real, now-cached `profiles/eudald.yaml`**
  (dedup fix + date-aware prompt both in place):

  | interest                              | window | articles |
  |----------------------------------------|--------|----------|
  | interpretability (curated arXiv)       | 48h    | 60       |
  | mathematics of machine learning        | 168h   | 5        |
  | League of Legends esports              | 168h   | 4        |
  | calisthenics                           | 168h   | 0        |
  | Olivia Dean and Sienna Spiro new music | 168h   | 0        |

  69 total (was 60 — i.e. 0 from search — before this fix). `source` breakdown:
  60 `curated`, 9 `bing_news_search`.
- **This is the real, expected shape of the result, not a remaining bug.**
  Checked each zero directly: calisthenics' three generated queries had zero
  entries published inside the 168h window at all (the newest hit was ~2 weeks
  old); Olivia Dean/Sienna Spiro had exactly one in-window candidate across all
  three queries, and it happened to be an MSN page trafilatura couldn't extract
  from. Interests like this simply don't generate publish-worthy news every
  week — no amount of query rephrasing invents news that didn't happen, and
  scraping harder (retrying extraction, following more redirects, adding more
  providers) doesn't fix a volume problem. The two real levers are exactly what
  was built: **event-shaped queries** (so what little news exists surfaces
  instead of being buried under evergreen explainers) **and a longer window**
  for search-discovered interests than for daily-publishing curated feeds. If
  a specific interest still comes up consistently empty, the fix is profile
  config (a longer `window_hours` override, or hand-picked `queries`/`feeds`
  for that interest), not more code in the fetch stage.

## Day 2 — 2026-09-10 (evening notes)
- Search-discovered interests use Bing News RSS: returns outlet articles by relevance, not
  date, then our window filter applies. Zero results = no *news-shaped* coverage in window,
  not absence of content. Interpretability/esports are news-shaped; calisthenics lives on
  YouTube/Reddit/forums. Next step if pursued: per-content-type source adapters (subreddit
  RSS, YouTube channel RSS). Out of scope for v1; documented in solution.md.
- Profile fixes to apply: split the music interest into one per artist; calisthenics gets
  window_hours: 720 and event-shaped hand-picked queries.
- Article volume is imbalanced (60 arXiv vs 9 rest). Rank stage must select per interest
  (top-k proportional to weight) then order globally; a single global ranking would produce
  an all-arXiv episode regardless of weights.
- Dedup bug: stripping all query params collapsed every Bing redirect link into one URL.
  Caught because article counts didn't add up; fixed with a tracking-param denylist and a
  regression test. Lesson: normalisation must be provider-aware.