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

## Rank stage — 2026-09-11
- **Why per-interest selection, not a single global top-k.** Flagged in the Day 2 notes:
  article volume across interests is wildly imbalanced (60 arXiv vs 9 everything else for
  the real profile), so a plain global ranking by score alone would produce an all-arXiv
  episode regardless of `Interest.weight`. `rank_stage` instead buckets fetched candidates
  by `Article.interest`, computes a total story budget from `duration_minutes` (roughly one
  story per 1.5 minutes, `MINUTES_PER_STORY` in `rank.py`), and splits that budget across
  interests proportional to `weight` via the largest-remainder method (floor each interest's
  share, hand the leftover slots to the largest fractional remainders, so allocations sum
  exactly to the budget). Only *after* per-interest selection is the chosen set ordered
  globally, by `score × weight` — that ordering is for presentation, selection itself never
  compares scores across interests directly. Two known simplifications, left for later:
  no cross-interest redistribution when an interest has fewer candidates than its budget
  (e.g. an interest with 0 in-window articles just contributes nothing, rather than freeing
  its slots for other interests), and no backfill when a selected candidate fails extraction
  (see below) — it's simply dropped rather than replaced by the next-best scored candidate
  for that interest.
- **Why extraction moved out of fetch.** Fetch used to run trafilatura on every deduped,
  in-window candidate — for the real `eudald.yaml` profile that's 69 extractions (69 network
  fetches + parses) even though an 8-minute episode only ever uses ~5 articles. `Article.text`
  is now `str | None`, unset by fetch; `rank_stage` extracts full text only for the
  candidates it actually selects, after scoring and budgeting, reusing the same "drop if
  extraction fails or under `MIN_EXTRACTED_CHARS` (200) chars" rule fetch used to apply
  (moved verbatim into `rank.py:_extract_text`). This is the single biggest cost/latency win
  in this change: roughly a 90%+ cut in extraction work for a typical profile, for zero loss
  in what ends up in the episode.
- **Article now carries `interest: str | None`** — the `Interest.topic` a candidate was
  discovered for (fetch sets this per `_feed_plan` entry), `None` for top-level `profile.feeds`
  extras which aren't tied to any one interest. Rank buckets those extras under a synthetic
  `"_extra"` pseudo-interest, weighted as the mean of the real interests' weights (0.5 if
  there are none), so they still participate in budgeting/ordering without needing a real
  `Interest.weight` to borrow. `profiles/eudald.yaml` has no top-level feeds today, so this
  path is exercised only in tests — flagging in case it matters once a profile actually uses
  `feeds:` again.
- **The scoring call.** One structured-output OpenAI call per batch of up to `BATCH_SIZE`
  (20) candidates — title + summary only, never full text, since candidates aren't extracted
  yet at this point. Batches are chunked across *all* fetched candidates together rather than
  per interest (each candidate's line in the prompt names its own interest, e.g.
  `[id] (interest: interpretability) title — summary`), so small interests share a batch
  instead of each paying for a nearly-empty call of their own. For the real profile (69
  candidates) that's ceil(69/20) = 4 calls per run. Cost is genuinely cheap: per batch,
  ~20 short candidate lines plus a short system prompt is on the order of a few hundred to
  ~1,000 input tokens, and the structured output (20 scores + one-line reasons) is a few
  hundred output tokens; at gpt-4o-mini list pricing that's well under a cent per batch, so
  ~4 batches puts a full run's scoring cost at a fraction of a cent — an order-of-magnitude
  estimate, not a measured bill, but confirms "cheap" as intended. Isolated behind
  `rank.py:_score_batch(client, model, batch)`, the same seam pattern as
  `_generate_script`/`_generate_queries`, so tests monkeypatch it directly and never hit the
  network. A candidate the model doesn't return a score for gets a defensive
  `score=0.0, reason="not scored"` rather than crashing the stage.
- **`ranked.json`** persists every scored candidate (`scored`, with `score`/`reason`/`selected`
  and `text` populated only when selected) plus the final chosen subset in global order
  (`selected`) — the audit trail the task asked for, and what `--from-ranked` resumes from.
- **`script_stage` needed no changes.** It already just reads `Article.text` off whatever
  `FetchOutput` it's handed; `generate.py` now builds that `FetchOutput` from
  `RankOutput.selected` (via a small `_script_fetch_output` helper shared by `run`,
  `run_from_articles`, and the new `run_from_ranked`) instead of from the raw fetch output.
  `--from-articles` now re-runs rank too (fetch's output has no text to hand straight to
  script anymore); the new `--from-ranked` is the resume point that used to be
  `--from-articles`'s job.

## Rank stage backfill + redirects + description rubric — 2026-09-11
- **Backfill.** The two "known simplifications" flagged above (no backfill, no
  cross-interest redistribution) turned out to matter immediately: the first real run kept
  only 2 of a budget of 5 because 2 of the other 3 picks failed extraction outright. `rank.py`
  now walks each interest's candidates in score order (`_select_bucket`) and keeps trying
  the next-best-scored one until `k` are actually selected or the interest runs out of
  candidates — a failure (redirect resolution error, extraction failure, or an unextractable
  domain, see below) no longer shrinks that interest's selection, it just costs one extra
  attempt. `RankOutput.backfilled` counts how many selected articles ended up outside the
  original top-k window (i.e. only selected because something ahead of them failed) —
  logged via `logger.info` when nonzero and echoed in `generate.py`'s rank summary line.
  Cross-interest redistribution (giving an empty interest's slots to another interest) is
  still not implemented — a different kind of gap (no candidates at all, not a failure to
  extract one) — and is still flagged for later.
- **Redirect resolution.** Bing's `apiclick.aspx` wrapper was the single biggest cause of
  extraction failures in the first real run (a 403, since trafilatura's own fetch doesn't
  send a browser User-Agent and some publishers reject non-browser clients on the wrapped
  link). `rank.py:_resolve_and_download` now does that resolution itself, with `httpx`,
  `follow_redirects=True`, a real Chrome User-Agent, and a 15s timeout — in the same request
  that downloads the page body, so there's no separate trafilatura fetch afterward, just
  `trafilatura.extract()` on the html already in hand. The resolved landing URL is persisted
  as `Article.final_url` on every candidate extraction was *attempted* for (selected or not,
  succeeded or not) — `None` for candidates never attempted, `None` for candidates whose
  resolution itself failed outright. This is what makes the msn.com check below possible: we
  need to know where a link actually landed, not just its Bing-wrapped starting URL.
- **msn.com treated as unextractable.** The second real-run failure was an MSN page that
  resolved fine but trafilatura pulled nothing usable from (JS-rendered chrome around the
  real article). Rather than spend a trafilatura call finding that out every time,
  `_is_unextractable` checks `final_url`'s host against `UNEXTRACTABLE_DOMAINS` (`msn.com`
  today) and fails the candidate immediately — same backfill path as any other failure. This
  is a narrow, evidence-based denylist (one domain, seen failing twice), not a general
  aggregator detector; revisit if another domain shows the same pattern.
- **Interest.description + scoring rubric.** `Interest` gained an optional free-text
  `description`, passed to the scorer alongside the topic (`[id] (interest: topic —
  description) title — summary`) so relevance is judged against what the user actually means
  by the interest, not just a bare keyword match on the topic string. The scoring prompt now
  states an explicit rubric (1.0 squarely on-topic per the description, 0.7 clearly related,
  0.4 tangential, 0.0 unrelated) instead of leaving "relevance" undefined, and requires the
  one-line reason to name the specific concept in the candidate's title that justifies the
  score — makes `ranked.json`'s `reason` field spot-check-able against the title without
  opening the source. Not enforced in code (an LLM reliably naming a concept isn't something
  a cheap validator can check), so this is a prompt contract, not a guarantee.
- **New failure mode found on this re-run, not yet fixed: cross-interest reason bleed within
  a mixed batch.** All 4 League of Legends esports candidates and the 1 music candidate came
  back `score=0.0, reason="Unrelated to mathematics of machine learning or any relevant
  concepts."` — copy-pasted from the tail end of the same batch's mathematics-of-ML scoring
  (which itself was correct and well-differentiated: 1.0/1.0/1.0/0.7/0.7/0.4 with distinct,
  on-topic reasons). The model appears to lose track of per-item interest context toward the
  end of a batch that mixes several interests, defaulting to the last "real" reasoning it
  produced instead of actually scoring the remaining items. Consequence: the LoL pick that
  backfilled into this run's selection did so essentially arbitrarily (all 4 candidates tied
  at the same wrong 0.0), not because it was genuinely the next-best. This directly undermines
  the "flatten across interests, not per interest" batching choice made when the scoring call
  was first built ("Rank stage" → "The scoring call") — that choice optimized for fewer/
  cheaper calls but didn't account for this. Not fixed here (out of scope for this pass); the
  likely fix is one of: chunk batches per-interest instead of flattened, or add an `interest`
  echo field to `ArticleScore`'s structured-output schema so a mismatch against the id's
  actual interest can be detected and the candidate re-scored/dropped instead of trusted
  blindly.

## Per-interest batching + echo validation + stricter rubric — 2026-09-11
- **Both fixes flagged above, implemented.** `_score_articles` now groups fetched articles by
  `Article.interest` first and only *then* chunks to `BATCH_SIZE` — every `_score_batch` call
  scores exactly one interest, so there's no longer a "tail of the batch" for the model to
  drift into a previous interest's reasoning on. Cost trade-off, as expected: more calls for
  the same candidate count (the real profile went from ~4 flattened calls to ~6 per-interest
  ones — interpretability alone still needs 3 at `BATCH_SIZE=20`), still cheap in absolute
  terms (a handful of cents-fractions per run), and now correctness isn't hostage to the
  savings.
- **`ArticleScore.interest` echo field**, required on every structured-output score. The
  system prompt states the batch's single interest label once and tells the model to echo it
  back verbatim per item — "a consistency check, not a judgment call." `rank.py:
  _score_batch_with_validation` rejects any item whose echo doesn't match the expected label,
  re-scores just the rejected items (`MAX_SCORE_ATTEMPTS = 2`: one retry), and if an item still
  mismatches after that, falls back to `score=0.0, reason="interest echo mismatch"` rather than
  trusting a score that never proved it was scored against the right thing. With per-interest
  batching already in place this is now a belt-and-braces check more than the primary fix —
  worth keeping anyway, since it also catches a model just getting a single item's context
  confused independent of batch-mixing, and it's what makes a silent regression back to
  cross-interest bleed visible (as a burst of rejected-and-retried warnings) instead of
  invisible.
- **Rubric hardened: strictness + text-grounding.** The scoring prompt now explicitly says
  "most candidates are not a great fit... most scores should land below 0.7" (the first
  real run handed out 1.0 quite liberally — three separate 1.0s for mathematics-of-ML alone),
  and requires the named concept in the one-line reason to actually appear in the title or
  summary given, not a connection the model inferred. Same caveat as the original "name the
  concept" requirement: this is a prompt contract enforced by asking, not validated in code —
  a cheap validator can't reliably confirm a paraphrased concept is "actually" in the text,
  so this leans on the model rather than gating on it.
- The user types "interpretability"; at save time the backend already makes one LLM call to generate the queries, so the same call can draft a one-line description ("mechanistic interpretability of neural networks: circuits, features, probes, sparse autoencoders") which the UI shows for one-click accept or edit. Zero effort for the lazy user, precision for the careful one.

## PodcastSettings migration — 2026-09-11
- `PodcastSettings.hosts` is now `list[Host]` (`name`, `voice_id`, `persona`, `home_turf`), not
  `list[str]` — a breaking schema change, matching the richer profile shape already drafted in
  `profiles/eudald.yaml`. Added `Listener` (`name`), `RecurringBit` (`name`, `description`,
  `max_per_episode`), and `Style` (`humour: 0-3`, `depth: 1-3`, `tangents: bool`, `banter: bool`)
  as new `PodcastSettings` fields (`listener` and `style` required, `recurring_bits` defaults to
  `[]` — a profile need not have one). This is exactly the schema the Day 1 diagnosis called for
  ("no style knob... that's your product hook") and what the style/persona/listener blocks in
  `profiles/eudald.yaml` were already anticipating.
- **Voice ids moved onto `Host`, off `TTSSettings`.** `TTSSettings.voices: dict[speaker, voice_id]`
  is gone; `tts_stage` now builds `{host.name: host.voice_id for host in profile.podcast.hosts}`
  itself. Two voice-mapping mechanisms would've meant deciding which one wins on conflict for no
  benefit — a host's voice is intrinsically part of who that host is, not a separate TTS-stage
  concern. Same failure behavior as before: a script line whose speaker has no matching host
  raises immediately (`ValueError`), not a silent fallback voice.
- **`LLMSettings` gained `script_model` (default `"gpt-4o"`)**, alongside the existing `model`
  (default `"gpt-4o-mini"`, now doing scoring/query-generation/outline/critique work). The script
  stage's writing pass is the one place voice/persona quality is worth paying for — see "Script
  rebuild" below.
- **Test fallout:** every test file that constructs a `Profile` had a `PodcastSettings(...,
  hosts=["Nova", "Max"], ...)` call that no longer validates — `hosts` needed real `Host` objects,
  plus the new required `listener`/`style` fields. Fixed across `test_fetch.py`, `test_rank.py`,
  `test_stitch.py`, `test_tts.py`, `test_generate.py`, and the new `test_script.py`/
  `test_outline.py`/`test_critique.py` (a small local `_profile()`/`_podcast_settings()` helper
  per file, matching the existing no-conftest convention). `test_ensure_interest_queries_
  generates_and_caches` in `test_fetch.py` incidentally exercises the new nested models'
  YAML round-trip (`Profile.to_yaml` → `Profile.from_yaml`), since it already wrote/reloaded a
  profile file. `profiles/eudald.yaml` itself was left untouched — it already matched this target
  shape (with placeholder `voice_id: ...`/persona text, presumably mid-draft) — and loads clean
  against the new models (verified: `Profile.from_yaml("profiles/eudald.yaml")`).

## Script rebuild: outline → script → critique — 2026-09-11
Rebuilt the script stage as three separate LLM steps, each its own module
(`podcast/stages/outline.py`, `script.py`, `critique.py`) with its own persisted artefact and
its own `--from-X` resume flag, following the Day 1 diagnosis's own plan ("outline step... →
script step with personas and style → self-critique pass") and reading straight from the
personas/listener/style/recurring_bits now in `profiles/eudald.yaml`.

- **Why outline first.** Separates *narrative planning* (story order, angle, which host leads,
  where a recurring bit fits) from *prose generation*. Two benefits: (1) it lets a cheap model
  (`llm.model`, gpt-4o-mini) do the structural thinking, so the expensive model's whole budget
  goes to voice/style quality, not figuring out story order; (2) it makes the narrative decisions
  inspectable and independently re-runnable (`outline.json`, `--from-outline`) — iterate on which
  stories lead and what their angle is without burning a script-writing call, and iterate on prose
  without re-planning. Grounding is enforced here too: every story's `source_ids` must be a
  subset of the ranked articles (`outline.py:_validate_outline`), and any `recurring_bit` name
  must be real and stay within its `max_per_episode` — checked in code, not left to the prompt,
  the same pattern as the original `_validate_source_ids`.
- **Why critique as a separate pass.** A single generation pass juggling grounding rules,
  structure, personas, *and* naturalness tends to hedge toward safe, expository writing — exactly
  the Day 1 diagnosis ("robotic," "interview transcript," "very linear"). Asking a model to review
  a *finished* script against three concrete failure modes (robotic / expository / breaks persona)
  is a narrower, better-specified task than getting naturalness right on the first pass while also
  not breaking grounding. Concretely safer too: `critique.py:_apply_critique` never asks the model
  to regenerate the script — it deep-copies the original and splices `rewritten_text` into only
  the flagged lines' `.text` (via `flatten_lines`, the same line order `tts_stage` synthesizes in),
  so segments/`source_ids` are provably untouched. Grounding is re-validated on `revised_script`
  anyway (`validate_source_ids`, against the actual known article ids, not the tautological
  "same ids as before") — belt-and-braces, since the splice already guarantees it structurally.
  Both `original_script` and `revised_script` are kept in `critique.json` for audit/diff. An
  out-of-range `line_index` from the model is logged and skipped, not fatal — same defensive
  posture as `rank.py`'s unknown-id handling.
- **Critique uses the stronger model too**, though only told to for the script step explicitly.
  Judging whether a line sounds robotic or breaks a specific persona is a comparable quality bar
  to writing lines that don't — using the cheap model to grade the expensive model's prose risked
  a weak critic missing exactly the subtlety the expensive model was hired for. Easy to override
  per profile if this turns out to be overkill (`llm.model` for critique would still work, just
  isn't the default).
- **What moved where:** `podcast.stages.script:flatten_lines` (renamed from `tts.py`'s
  `_flatten_lines`, now public since both `critique.py` and `tts.py` need the exact same line
  order) and `validate_source_ids` (same source_ids check as before, now public so `critique.py`
  can re-run it) both now live in `script.py`, where `Script`/`Line` conceptually belong;
  `tts.py` imports `flatten_lines` from there instead of owning its own copy.
- **`Line` gained `pause_ms: int | None`** — an optional pause after a line, for a natural beat
  or reaction. Not consumed by `tts_stage` yet (ElevenLabs synthesis doesn't take a post-line
  pause today); it's there for the script/critique steps to express the beat, and for `stitch.py`
  to pick up later (today's fixed 400ms inter-line gap could become `pause_ms or DEFAULT_GAP_MS`)
  — flagging as a follow-up, not done here.
- **Resume flags:** `--from-outline` (outline.json → script → critique → tts → stitch) and
  `--from-script` (script.json → critique → tts → stitch, re-purposed — it used to skip straight
  to tts) as asked. Also added `--from-critique` (critique.json → tts → stitch) beyond what was
  named explicitly — CLAUDE.md's own architecture rule is "re-running a stage must be possible
  from the previous artefact," and leaving critique as the one stage boundary with no resume
  point (forcing a re-paid critique call just to retry tts/voice settings) would leave a gap in
  a pattern every other stage boundary already has. `run_from_outline`/`run_from_script` also
  load the episode's `ranked.json` from the same directory — outline/script/critique all need
  the selected articles' full text, which isn't in `outline.json`/`script.json` themselves.
  `generate.py`'s `run()`/`run_from_*` functions got small `_step_X` helpers (print the summary
  line, return `None` if `until` says stop there) so the five entrypoints chaining up to seven
  stages don't each hand-roll the same stop-early logic.
- **Cost per episode, in tokens (order-of-magnitude estimates for a ~5-story, 8-minute episode —
  not measured against a live bill, and OpenAI's list pricing can change, so treat these as
  illustrative):**

  | step | model | ~input tokens | ~output tokens | ~cost |
  |------|-------|---------------|-----------------|-------|
  | outline | gpt-4o-mini | ~900 | ~550 | <$0.001 |
  | script | gpt-4o | ~3,050 | ~2,100 | ~$0.03 |
  | critique | gpt-4o | ~2,150 | ~300 | ~$0.008 |

  Total ≈ **$0.04/episode** for these three steps, dominated by the script step's output tokens
  (a full ~1,200-word episode at gpt-4o's output rate). That's roughly 15-20x what the old
  single gpt-4o-mini script call cost (a fraction of a cent) — a deliberate trade: the whole
  point of `script_model` is spending real money exactly where it buys quality (the actual
  prose), while outline and critique's structural/judgment work stays on the cheap model.
  Combined with rank's per-episode scoring cost (a few cents at most, per its own entry above),
  a full episode's total LLM spend is still on the order of a few cents to ~$0.05 — cheap in
  absolute terms for a personal podcast, not "per-request-cheap" the way the pre-rebuild pipeline
  was.

## Recurring bit id fix, fetch feed count fix, quieter extraction-failure logs — 2026-09-12
- **Recurring bit mismatch.** The first real outline run failed: `_validate_outline` rejected
  `'logistical pin-drop'` against a profile bit named `'The logistical pin-drop'` — the model
  had paraphrased the free-text name back slightly differently (dropped "The", lowercased it).
  Asking a model to echo a string verbatim and then string-comparing the echo was always going
  to be fragile. Fix has two layers:
  1. `RecurringBit` gained `id: str | None` with an `effective_id` property (explicit `id`, else
     a slugified `name` — `_slugify` in `models.py`, same pattern as `Interest.
     effective_window_hours`). `OutlineStory.recurring_bit` now stores this id, never the name.
  2. `outline.py:_response_model(bit_ids)` builds a **request-specific structured-output
     schema** (via `pydantic.create_model`) where `recurring_bit` is `Literal[tuple(bit_ids)] |
     None` instead of a free `str | None` — verified the resulting JSON schema renders as
     `{"anyOf": [{"const": "the-logistical-pin-drop"}, {"type": "null"}]}`, so the model
     literally cannot return a value that isn't one of the profile's actual bit ids (or null).
     This is different from why `source_ids` stays a free `list[str]` validated only in code
     (docs/decisions.md, "Script stage"): `recurring_bit` is one scalar drawn from a small,
     fixed, request-time-known set (the profile's own bits, typically a handful), so it's
     genuinely enum-constrainable; `source_ids` is an arbitrary-length subset of a much larger,
     also request-time-known but less boundable candidate pool — same "can't express a dynamic
     subset in the schema" limitation as before still applies there.
  3. `_validate_outline`'s code-level check is kept as the backstop the task asked for, just
     re-keyed to `effective_id` instead of `name` — belt-and-braces in case the schema
     constraint is ever bypassed (a future refactor, a non-`.parse()` code path, etc.).
  4. `script.py:_render_outline_story` now resolves the id back to the bit's `name`/
     `description` (`recurring_bits_by_id` lookup) before showing it to the script-writing
     step — otherwise that step would see a bare slug instead of something to actually write
     dialogue around, a quality regression the id switch would've caused incidentally.
- **Fetch feed count.** `generate.py`'s summary line used `len(profile.feeds)` — the top-level
  extras list only, which is `0` for `eudald.yaml` since every one of its feeds comes from
  interests (`interests[].feeds` or generated/cached `queries`), not `profile.feeds`. Printed
  "from 0 feeds" after actually querying 13. Fix: `FetchOutput` gained `feeds_count: int = 0`
  (default so old persisted `articles.json` files still validate), set in `fetch_stage` from
  `len(feed_plan)` — the same `_feed_plan(profile)` result the stage already iterates over, so
  it's the real count of feed URLs queried (curated + generated/cached-query + top-level extras),
  not a guess derived from one piece of the profile.
- **Extraction-failure logging.** `rank.py:_resolve_and_download`'s `except httpx.HTTPError`
  logged `exc_info=True` at WARNING — a full traceback per failure, and redirect/extraction
  failures are common enough (403s, dead links) that this was mostly noise at the level a normal
  run actually shows (`generate.py` sets `logging.basicConfig(level=logging.INFO)`). Now WARNING
  gets one line — `status=<code or None> final_url=<url or the original>` — and the traceback
  moves to a separate `logger.debug(..., exc_info=True)` call, visible only if DEBUG logging is
  turned on. `status`/`final_url` come from `exc.response` when the error has one (an
  `HTTPStatusError`, i.e. a real HTTP response that just wasn't 2xx) and degrade to `None`/the
  original request url for errors below the HTTP layer (connection refused, timeout) that never
  got a response to read from.

## Script quality pass — 2026-09-12
Five changes, all aimed at the same complaint from the first real scripts: too long-winded, too
willing to invent, too free with the recurring bit and tangents crowding every segment.

- **(1) Per-story word budget, code-computed.** `OutlineStory.word_budget: int = 0` — deliberately
  *not* part of the LLM's structured output (an LLM summing to an exact total reliably is not a
  bet worth making; `rank.py`'s per-interest article budgets already established doing this kind
  of proportional allocation in code, not by asking). `outline_stage` computes it after the
  outline call: `total_words = duration_minutes*150 - 120` (the 120 is a rough cold-open/outro
  reserve), split across stories proportional to each story's rank score (the *average* score of
  its `source_ids`, for the rare merged-story case) via the same largest-remainder method as
  `rank.py:_per_bucket_budget` — falls back to an equal split if every story scored 0. `script.py`
  shows each story's budget in the outline block and is told to land within ~20% of it.
  Enforcement past that is a **report, not a rewrite**: `critique.py:_budget_flags` counts each
  revised segment's actual words (deterministic, no LLM needed) against `word_budget * 1.2` and
  records any overrun in `CritiqueOutput.over_budget_segments` — auto-shortening a segment that
  runs long would mean either dropping content or a fourth LLM pass, neither asked for here.
- **(2) Listener fact invention.** Took the cheaper of the two options offered: a script.py hard
  rule ("only state something about the listener if it's explicitly given in the Listener line —
  never invent hobbies, opinions, biography") plus a critique.py criterion
  (`invented_listener_detail`), rather than a dedicated validation LLM call. `Listener` itself
  stays untouched (just `name` today) — the guard scales automatically if it grows more fields
  later, since the rule says "given in the Listener line," not "given a name." Also tightened
  outline.py's own tangent instruction the same way, since a tangent suggested there ("from...
  the listener's interests") was the plausible *source* of an invented detail flowing downstream
  into dialogue — cheaper to stop it at the point it's suggested than only where it's spoken.
- **(3) One tangent per segment, never with the bit.** `Angle.tangent` became `str | None` (was
  required) so it can legitimately be empty. `outline.py:_validate_outline` now rejects any story
  with both `recurring_bit` set and a non-empty `tangent` — a segment carries one or the other,
  enforced in code as a hard constraint on the *plan*, not left to the writer to self-police.
  "At most one" tangent didn't need separate code: `Angle` only ever had one `tangent` field to
  begin with, so a second tangent within a segment can only come from the writing step going
  beyond the outline — covered by a new script.py hard rule ("at most one backstory tangent... and
  only if the outline's angle gives one") rather than a structural change.
- **(4) 35-word line cap, split into exchanges.** Word-length checking is deterministic
  (`len(line.text.split())`), but turning one long line into a natural back-and-forth isn't — that
  needs the model. So the split of labor: `critique.py:_over_length_line_indices` finds every line
  over `MAX_LINE_WORDS` (35) outside the segment carrying the recurring bit (code), and the
  critique prompt is handed those exact indices with an instruction to flag them `too_long` and
  split each into 2+ shorter lines (model). This required generalizing the critique's own fix
  mechanism: `CritiqueFlag.rewritten_text: str` became `rewritten_lines: list[Line]` — usually
  length 1 (an ordinary rewrite), but length 2+ for a split. `critique.py:_apply_critique` was
  rewritten from a simple 1:1 splice to a rebuild that walks cold_open/each segment/outro once,
  substituting each flagged position's *list* of replacement lines in place — later positions
  shift naturally since the rebuild is positional, not index-arithmetic. script.py also states the
  35-word cap as a writing-time rule, so critique is the backstop, not the primary defense.
- **(5) "The article" phrasing.** Pure prompt rule in both script.py (writing time: "never say
  'the article' or 'the paper' — attribute to the actual actor or call it 'the report'") and
  critique.py (`the_article_phrasing` criterion, review time) — no code check, since judging
  whether a claim is actually attributed to an actor vs. genuinely has none is a language-
  understanding call, not a string match (`grep`-ing for "the article" would false-positive on a
  line that's *correctly* calling it "the article" in a context that already named the actor).
- **Cross-cutting: `critique_stage` gained an `outline_output` parameter** (for word budgets and
  which segment carries the bit) and `CritiqueOutput` gained `total_words`/`over_budget_segments`.
  `run_from_script` now also loads the episode's `outline.json` alongside `ranked.json`, since
  critique needs both. `generate.py`'s critique summary line now reports total words and
  over-budget segment count alongside the existing flagged-line count.
- Solves the demo problem (an interest with no news this week still gets airtime). Keep news as the core, because the assignment says news, but add a second segment type: when an interest has zero fresh candidates, fetch a grounded evergreen source (the Wikipedia API is reliable and extracts cleanly) tagged source: evergreen, and the outline can turn it into a "primer" or "did you know" segment. Grounding rule still holds: the facts come from the fetched text, so no hallucinated trivia. It's maybe an hour of work; do it after the script rebuild if today allows, otherwise tomorrow morning. Log it now as a decision: "news first; evergreen fallback so every interest can appear; never ungrounded".

## Voice and dynamics pass — 2026-09-12
- **`tts.model_id` before this change: `"eleven_turbo_v2_5"`** (the cheap/fast default set on Day 1,
  reused unchanged through the PodcastSettings migration). No audio-tag support, no dialogue mode
  — that's what this pass replaces.
- **Text-to-dialogue: tested live, works with our key.** `client.text_to_dialogue.convert_with_timestamps(inputs=[...], model_id="eleven_v3")`
  against a real 2-turn payload (Alice/Bob voice ids from `profiles/eudald.yaml`) returned 200 with
  real audio — confirmed both that the endpoint is enabled for this key and that `model_id=
  "eleven_v3"` is the one that works with it (the installed `elevenlabs` SDK, 2.67.0, exposes
  `text_to_dialogue` at all, which itself isn't guaranteed across SDK/plan versions). A separate
  live check via `client.models.list()` failed with 401 `missing_permissions` (the key lacks
  `models_read`) — unrelated to text_to_dialogue access, just means model capability can't be
  introspected, only tried. Later validated the real `tts_stage` code (not just the raw SDK) end
  to end against a throwaway 2-line dialogue script: dialogue mode succeeded, both lines' audio
  files were sliced out and written correctly, sizes were non-trivial and distinct per line.
- **Architecture: dialogue mode primary, per-line v3 fallback, both used, one always ends up
  file-per-line.** `text_to_dialogue.convert_with_timestamps` returns one combined audio blob for
  a whole chunk, not separate files — but its `voice_segments` (per turn: `dialogue_input_index`,
  `start_time_seconds`, `end_time_seconds`) give exactly enough to slice it back apart.
  `tts.py:_slice_dialogue_audio` cuts each line's clip from the *end of the previous turn* (0 for
  the first) to *this turn's own end* (or the full clip length, for the chunk's last turn, to
  catch any trailing tail) — so whatever natural pause ElevenLabs left between turns stays
  embedded as trailing silence on the earlier line's clip rather than being trimmed away, and the
  clips concatenated back-to-back exactly reconstruct the original combined audio. This is what
  lets dialogue mode keep the existing one-file-per-line `TTSLine`/`tts_manifest.json`/stitch
  contract unchanged, instead of needing a parallel "whole-chunk audio" artefact shape. On any
  failure (permission, transient error, a length error) the whole episode falls back to per-line
  `text_to_speech.convert`, one call per line, same v3 model family for tag support. Chunks
  already written from a partially-successful dialogue attempt are left on disk and simply
  reused by the per-line pass's own skip-if-exists check — cheap to reason about, at the cost of
  `synthesis_mode` sometimes reading "per_line" for an episode that's actually a mix; flagged as
  a known simplification, not fixed here.
- **Chunking:** `DIALOGUE_CHUNK_CHAR_LIMIT = 2500`, picked conservatively — ElevenLabs doesn't
  document an exact character/turn cap for text_to_dialogue as of this writing, and finding the
  real one empirically would mean deliberately triggering (and paying for) failures. Revisit if
  a length-shaped failure actually shows up in `_synthesize_dialogue`'s fallback logs.
- **Per-host `voice_settings` (stability/style/similarity) only take effect in the per-line
  fallback.** `DialogueInput` (the text_to_dialogue request shape) has exactly two fields, `text`
  and `voice_id` — no per-turn settings — confirmed by inspecting the installed SDK's type
  directly, not assumed. So a host's `voice_settings` is real but conditional: it does nothing
  while dialogue mode is succeeding, and only shapes the voice once/if the pipeline falls back.
  Documented prominently (`HostVoiceSettings`'s docstring, this entry) rather than routing around
  it — forcing fallback just to honor voice_settings would fight the whole point of preferring
  dialogue mode (better cross-turn coherence, fewer requests) for a knob that mostly matters for
  character consistency, which dialogue mode's own inter-turn coherence partly substitutes for
  anyway. `profiles/eudald.yaml`: Alice (expressive) got `stability=0.3, style=0.6`, Bob (stable)
  got `stability=0.75, style=0.15`, both `similarity=0.75` — illustrative defaults, not tuned
  against real audio.
- **`delivery: str | None` on `Line`**, mapped to a bracketed tag prefix ("[laughs] ...") by
  `tts.py:_tagged_text` in both synthesis paths, gated on `script.py:supports_audio_tags(model_id)`
  (a `"v3" in model_id.lower()` check — there's no capability-lookup API, so this is the same
  heuristic tts.py itself would need). Caught one real issue running this against the actual
  profile: the writer sometimes opens a line with its own literal `[laughs]` *and* sets
  `delivery="laughs"` — `_tagged_text` now skips prefixing when the line's text already starts
  with `[`, so it doesn't double up.
- **`pause_ms` guidance, not just a field.** The script prompt states concrete ranges (~100-200ms
  for a quick exchange, ~600-900ms for a beat, the long end reserved for right before the
  recurring bit or a reveal) rather than leaving the value to the model's judgment alone — a bare
  "set pause_ms appropriately" instruction is exactly the kind of vague ask this project has
  found doesn't reliably produce a deliberate result. `stitch.py` reads `TTSLine.pause_ms` (now
  copied onto `TTSLine` from the source `Line`, not just living on the script) as the gap after
  that line — `DEFAULT_PAUSE_MS = 200` when unset — but **only outside dialogue mode**
  (`TTSOutput.synthesis_mode`): dialogue-mode clips already have their natural pacing baked in
  from the slicing above, so stitching them with an *additional* gap would double up pauses that
  are already there. The old fixed `SILENCE_MS = 400` constant is gone.
- **Laughter: tag when supported, spoken word when not, never standalone.** `script.py`'s
  laughter instruction is conditional on `supports_audio_tags` — `[laughs]` inline when true, a
  spoken reaction word inside a longer line when false — and either way, never a standalone line
  like "Ha." on its own (the literal ask). Verified in a real run: `[laughs]`, `[deadpan]`,
  `[amused]`, `[excited]` tags all showed up appropriately, none as bare lines.
- **Script rules (interjections/em dash/emotional reactions/"Correct." cap).** All four are
  prompt-level writing rules in `script.py`; the "Correct." cap is also code-checked, the same
  pattern as the word-budget and line-length checks: `critique.py:_repeated_correct_line_indices`
  finds every occurrence past the first (exact match on `"Correct."` as a stripped line, not a
  substring) and feeds those indices to the critique prompt as a `repeated_correct` fix target,
  reusing the same flag-and-rewrite mechanism as `too_long`. The other three (interjections
  cutting in, em-dash trailing lines, emotional-not-evaluative reactions) are prompt-only —
  there's no reliable code-level way to judge "is this reaction emotional or evaluative" the way
  there is for counting words or matching an exact phrase.
- **`podcast.name` + one-breath cold-open identification.** New required `PodcastSettings.name`
  field — required because the whole point is a real spoken identification, not a fallback empty
  string. **Breaking for old persisted `episode.json` snapshots** (the profile is embedded there
  in full) the same way the `hosts: list[str] -> list[Host]` migration was — one existing test
  episode (`20260912T152431Z`) needed its `episode.json` hand-patched with `podcast.name` to keep
  resuming from it; no general migration path built, matching how that earlier breaking change
  was handled. **Picked `"Two Angles"` as the actual name in `profiles/eudald.yaml`** — a
  placeholder invented to unblock testing the cold-open rule for real, not a considered creative
  choice; flagged for the user to rename. Verified in the same real run: cold open was `"This is
  Two Angles, with Alice and Bob."` — one line, both names, the show's name, nothing else.
- **Per-episode character cost — computed, not measured against a real synthesis bill.** Character
  count is exactly `sum(len(_tagged_text(line)) for line in flatten_lines(script))` — deterministic
  from the persisted script text, no need to actually call ElevenLabs to know it (unlike the
  earlier per-LLM-call token/cost estimates, which were genuinely estimates). `TTSLine.characters`
  now counts the tagged text actually sent to the API (including a delivery-tag prefix), not just
  `line.text` — it wasn't before, a small accuracy bug caught while building this entry, fixed
  before it shipped. Re-ran outline → script → critique on the existing `ranked.json` with today's
  new writing rules (personas/pauses/tags/cold-open all in effect) to get a real number: **60
  lines, 1087 words, 6,877 characters** for one real 8-minute episode — `generate.py`'s tts
  summary line now prints `synthesis_mode` and `total_characters` (`TTSOutput.total_characters`)
  every run, so this is visible per-episode going forward, not just this one measurement.

## Humanisation pass — 2026-09-13
One iteration: outline gives each host an emotional stance and arc per story, the script leans
into spoken texture and dense emotion tags, and the line-length cap relaxes with a per-host
brevity check. Also found and fixed a real bug in the course of re-running this end to end.

- **(1) Stance + arc, schema-constrained the same way as recurring bits.** New `HostStance`
  (`host`, `attitude`, `why`, `arc`) on `OutlineStory.stances` — one per host, required. Reused
  the exact `Literal`-via-`create_model` technique from the recurring-bit id fix: `_response_model`
  now also takes `host_names` and constrains each stance's `host` to a `Literal` enum of the
  profile's actual host names, so a stance can't reference a host that doesn't exist (verified:
  `model_validate` rejects `"Carol"` when only Nova/Max are configured). `_validate_outline` still
  backstops it — every story needs exactly one stance per known host, no fewer, no more, no
  duplicates. `script.py`'s hard rules now say to write every line from that host's *current*
  stance for the story, and — if the stance has an arc — to let the shift actually show across
  the segment's dialogue rather than just asserting the ending feeling on the last line.
- **(2) Spoken texture + `written_not_spoken`.** Prompt-only, like the em-dash/interjection rules
  from the script quality pass: connectors and disfluencies at a natural rate ("I mean", "no?",
  "okay so", "look", "wait"), occasional self-correction/restarts, ellipsis-or-colon for a mid-line
  tone shift, CAPS for vocal emphasis, "read like something said, not written." No code check is
  possible here (same reasoning as the em-dash/interjection rules already in place — this is a
  style judgment, not a countable property), so the backstop is a new critique criterion,
  `written_not_spoken`, added alongside the existing LLM-judged ones.
- **(3) Tag density, verified against real output.** Prompt asks for at least one audio tag every
  two or three lines, and — the more specific ask — tags "where the emotion shifts, not only at
  line starts": `delivery` still places one tag at a line's start, but the prompt now tells the
  writer to embed a tag directly in the line's own text when the shift happens mid-line instead,
  leaving `delivery` null for that line. No architecture change needed for this — whatever's in
  `line.text` already reaches ElevenLabs verbatim, `delivery` was always just a start-of-line
  convenience on top of that. Re-ran the real episode's outline→script→critique to check: **55
  tags across 58 lines (≈1 tag per 1.1 lines)** — denser than the "every two or three lines" ask,
  not under it — drawn from a real vocabulary spread (`[excited]` ×10, `[deadpan]` ×8, `[laughs]`
  ×7, `[amused]` ×5, plus 17 more distinct tags used once or twice each — `[warmly]`, `[curious]`,
  `[dry]`, `[firm]`, `[skeptical]`, `[teasing]`, ...). Not visibly clustered at line-starts only,
  spot-checked several lines with an inline tag mid-sentence.
- **(4) Line cap relaxed to 45, host brevity flag.** `MAX_LINE_WORDS` moved from `critique.py` to
  `script.py` (35 → 45) — it's a writing-time rule first, critique backstop second, so it now
  lives where it's authored and `critique.py` imports it, instead of two files each hardcoding the
  number and risking drift. Real output check: the one line over 45 words (63 words, Alice) fell
  inside the recurring-bit segment — exactly the carve-out the rule allows, not a violation (it's
  the bit's own "20-30 second escalating scenario" per the bit's description). New
  `HostBrevityFlag`/`critique.py:_host_brevity_flags` — same code-detected, LLM-facing-schema-free
  pattern as `SegmentBudgetFlag`: for each host, the fraction of their lines at or under 8 words;
  flagged past 60%. Deliberately generic (no host hardcoded) even though the ask specifically named
  Bob as the "economical" persona in this profile — the rule and the check apply to whichever host
  a profile's persona makes terse, not a fixed name. Real check: Alice 20% short lines, Bob 29% —
  both comfortably under the 60% flag threshold, and Bob's own longer lines (up to the low 40s)
  confirm "economical on average" rather than "short on every line" came through.
- **Bug found and fixed while re-running this end to end: segment filenames weren't
  content-addressed, so a script regeneration could silently reuse a *different* script's audio.**
  `tts_stage`'s per-line filenames were `{index}_{speaker}.mp3` — stable across runs whenever the
  index→speaker shape happened to match (common, since cold-open-then-alternating-hosts is a
  stable pattern across independent regenerations). Re-running outline→script→critique against
  the same episode dir during this pass produced a new 58-line script, but the "re-run through
  stitch" request's first attempt silently reused 56 of those 58 lines' audio from an *earlier,
  different* script version already sitting in `segments/` from prior testing — confirmed by file
  mtimes (hours earlier than the run that supposedly produced them) and by two dialogue-mode
  chunks logging "already synthesized, skipping" when nothing in *this* run should have existed
  yet. The resulting `episode.mp3` didn't actually match the script being reported on. Fixed by
  making filenames content-addressed: `{index}_{speaker}_{sha1(tagged_text)[:8]}.mp3`
  (`tts.py:_content_key`) — a line whose text changes between runs gets a different filename, so a
  stale file simply can't be found and gets resynthesized, while an unchanged line across a
  same-script re-run (the actual point of skip-if-exists: cheap recovery from a partial failure)
  still matches and is still skipped. Cleaned the affected episode's `segments/` and re-ran
  tts→stitch fresh to confirm: 58 files, 58/58 fresh (no skips), correct duration. This is the
  kind of bug that stays invisible unless someone actually listens to or inspects the audio
  against the script — worth remembering next time an episode dir gets reused across many
  regenerations during iteration.

## Backend: SQLite + FastAPI + APScheduler — 2026-09-13
Added a persistence/orchestration layer around the existing pipeline, without touching the
stages themselves: SQLite via SQLModel (`podcast/db.py`), a shared `podcast/service.py` that
both the CLI and a new FastAPI app (`podcast/api/`) call to trigger/resume a run, and an
APScheduler-driven daily job. New dependencies (`fastapi`, `uvicorn`, `sqlmodel`,
`apscheduler`) added directly rather than asked-about first, since the user's own request named
this exact stack.

- **Why SQLite.** This is a single-user take-home — one profile, one person's episodes. A file
  DB needs no ops (no server process, no connection pooling, nothing to provision) and SQLModel
  gives typed rows for free on top of it. What changes at scale: `profiles` stops being a
  get-or-create singleton (id=1) and becomes a real table keyed by an auth identity; SQLite's
  single-writer lock stops being fine once concurrent users are actually writing at the same
  time, so that's also the point multi-user would force a move to Postgres.
- **Why a background task, not a queue.** `BackgroundTasks` needs zero worker infrastructure —
  no Redis, no Celery/RQ process, nothing to deploy alongside the API for a take-home. Named
  limitations, deliberately not solved here: no retry on crash (a killed API process leaves any
  `status="running"` row stuck forever, with nothing to reconcile it), and no concurrency control
  (two `POST /episodes` calls run two full pipelines — real LLM/TTS calls, real money — at once;
  SQLite's write lock serializes the *DB writes* between them, not the expensive work itself). A
  real queue (Celery/RQ + Redis, or a hosted equivalent) is the fix at the point either of these
  actually bites.
- **What `events` is for.** `episodes` is a mutable current-state row — one row, overwritten in
  place as a run progresses. `events` is append-only: `generated` once at the start, `stage_done`
  once per successful stage (with per-stage metrics in its `metadata` JSON — article/segment
  counts, character counts, timings), `failed` at most once, `completed` once at the end, plus
  dashboard-posted `played`/`completed_playback`. This is the only place "what happened, in what
  order, including things the current-state row has since overwritten" can be reconstructed —
  the audit trail / dashboard timeline the task asked for.
- **`call_stage` always records a failure before re-raising; only the API catches it.** Discussed
  explicitly: the alternative (swallow the exception inside `run_episode` itself, log, return
  normally) would mean the CLI silently "succeeds" on a failed run instead of crashing with a
  traceback — a real behavior change from before this backend existed. Instead, `call_stage`
  updates `status="failed"`/`stage_reached`/emits a `failed` event and then **re-raises**
  unconditionally, so the DB is correct regardless of caller; `generate.py`'s CLI functions don't
  catch it (same crash-with-traceback UX as always), and only `podcast/api/routes_episodes.py`'s
  background-task wrapper catches it, so a failed episode doesn't crash/spam the ASGI server's
  own unhandled-exception logging.
- **`Profile.schedule: str = "0 7 * * *"`** — one cron-string field, not a wrapping settings
  model; upgrade only if a timezone/enabled flag etc. is ever needed. Parsed only by
  `apscheduler.triggers.cron.CronTrigger.from_crontab` (in `scheduler.py`) — no second cron
  parser/validator added at the Pydantic layer.
- **Profile YAML vs. DB: one-directional sync.** The YAML file stays the CLI's actual input
  (`Profile.from_yaml`/`to_yaml` untouched); every CLI run additionally upserts the loaded
  profile into the single DB row so CLI-created episodes have a `profile_id` and the API/
  dashboard can see current config. `PUT /profile` writes only to the DB row, never back to
  YAML — an API-driven edit silently reformatting/clobbering a hand-authored YAML file (the same
  lossiness `to_yaml`'s own docstring already flags: "does not preserve hand-written comments/
  formatting") would be worse than the two simply being allowed to diverge until the next CLI run
  re-syncs from YAML.
- **JSON columns via SQLAlchemy's native `JSON` type**, storing `model_dump(mode="json")` dicts
  directly (`ProfileRecord.data`, `EventRecord.metadata_json`) — no benefit to hand-rolling
  `json.dumps`/text columns instead.
- **Cost estimate is a placeholder.** `COST_PER_1K_CHARS_USD = 0.18` applied to
  `TTSOutput.total_characters` — illustrative, not a real ElevenLabs price (no pricing data lives
  anywhere in this codebase), same spirit as the existing illustrative OpenAI token-cost
  estimates in this log. Don't treat `cost_estimate_usd` as an accurate bill.
- **`GET /schedule/next` reads the live scheduler job's `next_run_time`** rather than adding
  `croniter` as a second dependency — `CronTrigger` already has to parse the cron string to
  schedule the job in the first place, so asking the same job object for its own next fire time
  is free.
- **`EventRecord.metadata_json`, not `metadata`.** `metadata` is reserved on SQLAlchemy's
  declarative base; the API still exposes it under the JSON key `"metadata"` in responses
  (`podcast/api/schemas.py:EventOut`), just not as the SQLModel column's Python attribute name.
- **Episode id collision fix, found while writing the metrics API tests.**
  `artefacts.new_episode_id` (moved from `generate.py`'s old `_new_episode_id`, unchanged until
  now) formatted only to second resolution — fine for the CLI's one-run-per-invocation usage, but
  two `POST /episodes` calls within the same second now silently collide on the same episode_id
  and become one episode (`get_or_create_episode_record` finds the "existing" row). Fixed by
  appending a short random suffix (`secrets.token_hex(3)`) to the timestamp — keeps the id
  sortable/human-readable while making same-second collisions practically impossible.
- **DB test isolation**: every test file monkeypatches `podcast.db.engine` to a temp-file SQLite
  engine (module-global lookup at call time, mirroring the existing `episode_dir` monkeypatch
  pattern this codebase already used for filesystem isolation) — never a real
  `data/podcast.db`. Episode artefact isolation for the new API tests uses the same trick one
  level up: monkeypatching `podcast.paths.EPISODES_DIR` itself (rather than each module's own
  imported `episode_dir` name) so every caller — `service.py`, `routes_episodes.py`, a test's own
  fake pipeline — agrees on one temp directory regardless of which module holds the reference.

## User-facing UI: Vite + React, served by FastAPI — 2026-09-13
Added `web/` (Vite + React + Tailwind v4 + react-router-dom), three routes (`/settings`,
`/episodes`, `/dashboard`), a small backend addition (`POST /interests/suggest`, `GET /voices`),
and `uv run podcast serve` to run the API (+ built UI). Full routes/state-flow writeup in
`docs/ui.md`; this entry is the trade-offs.

- **Why React over server-rendered.** The three pages are genuinely stateful client-side:
  sliders that update live, a settings form with per-row "Suggest" calls that shouldn't reload
  the page, an episode list that polls and an audio player wired to fire events on play/end.
  Hand-rolled DOM updates or a template-per-request server-rendered approach would mean
  re-implementing most of what a component model gives for free (local component state,
  re-render-on-change, event handlers) with more code, not less — and the app is small enough
  (3 routes, no SEO/first-paint requirement, single user) that SSR/Next.js-style complexity buys
  nothing here. A static SPA served by the same FastAPI app that already serves the API was the
  simplest thing that actually fits the interactivity the task asked for.
- **Why polling over websockets** (`Episodes.jsx`'s `refresh()` loop). Episode generation takes
  minutes (real LLM + TTS calls), there's exactly one user, and the UI only needs a status board,
  not sub-second updates — a 3-second re-fetch of `GET /episodes`, running only while something is
  actually `pending`/`running` (stops scheduling itself once everything settles), is simpler than
  standing up a websocket endpoint, connection lifecycle, and a server-side pub-sub to push
  `stage_done` events to a browser tab — infrastructure this app's own background-task/no-queue
  design (see the SQLite/FastAPI/APScheduler entry above) doesn't have anywhere to plug into
  cheaply anyway. Trade-off: up to ~3s of staleness and one wasted request per tick once idle for
  as long as any episode is in flight — acceptable at this scale, revisit if the dashboard ever
  needs live per-stage progress or there's more than one concurrent viewer.
- **Interest suggestions reuse the fetch-stage seam, not a new call site.** `stages/fetch.py`
  already isolates its OpenAI calls behind small functions (`_generate_queries`); `suggest_interest`
  / `_generate_suggestion` follow the same pattern (structured output, same seam tests
  monkeypatch) and share `InterestSuggestion`/`NUM_GENERATED_QUERIES` rather than duplicating the
  prompt-building logic — one LLM call drafts both the description and the queries, at the same
  cost the queries-only call already had (noted as a "day 3" idea in this log's "Per-interest
  batching" entry, now built).
- **Voice catalog is a hardcoded list (`api/voices.py`), not a live ElevenLabs call.** No
  `voices.list()`-shaped call exists anywhere in this codebase, and the "Voice and dynamics pass"
  entry above already found this API key lacks some read permissions (`models_read`) — adding an
  unverified live call for a picker with a handful of options isn't worth the risk of a runtime
  surprise. Edit `VOICE_CATALOG` directly to add more voices.
- **`PUT /profile` always sends the whole `Profile` object, no partial-update endpoint.** The
  settings form loads the full profile into React state on mount and mutates it in place, so
  fields the UI doesn't expose (`recurring_bits`, `feeds`, `llm`, `tts`, `fetch`) round-trip
  untouched automatically — simpler than a PATCH endpoint plus client-side diffing, and correct
  as long as the UI always loads before it saves (true here: there's no "blind" write path).
- **CORS is permissive (fixed dev-origin allowlist, all methods/headers)** — needed only so the
  Vite dev server (a different origin, `:5173`) can call the API during development; production
  serves the SPA and the API from the same FastAPI origin, where none of this applies. Fine for a
  single-user take-home; would need tightening (or dropping entirely, since same-origin serving
  is already the production story) if this were ever exposed beyond localhost.
- **SPA fallback verified against Starlette's actual `StaticFiles` behavior, not assumed.**
  `StaticFiles(html=True)` alone does *not* serve `index.html` for an arbitrary unmatched path
  like `/settings` — checked its `get_response` source directly: it only serves `index.html` for
  `/` and real directories, 404 (or `404.html`) otherwise. `app.py` instead mounts `/assets`
  separately and adds an explicit catch-all route (`@app.get("/{full_path:path}")`, registered
  after every API router) that serves a real file when Vite emitted one there, else
  `index.html` — the actual mechanism that lets a browser refresh on a client-side route work.

## Three fixes from the first real run through the API — 2026-09-13

### Grounding guard: caught by a dead placeholder feed
The first real end-to-end run through the API — profile PUT'd via the new settings UI, "Generate
now" clicked for real — produced an episode whose script talked around its topic in generic,
made-up specifics instead of citing anything real. Cause, once traced: one of that profile's
interests still had `feeds: ["https://example.com/feed.xml"]` (`example.com` is the internet's own
canonical placeholder domain — this codebase's own test fixtures use it constantly, e.g.
`tests/test_outline.py`'s `_article()` helper), so fetch found zero candidates for it, and it was
in fact the *only* interest in the profile — rank therefore selected **zero articles overall**.
Nothing in the pipeline stopped there: `outline_stage` happily ran with `rank_output.selected ==
[]`, and the model, given no sources at all, filled the gap with invention. This is exactly the
"never invent facts" rule (CLAUDE.md, day one) failing in the one case nobody had tried yet — a
completely empty source list, as opposed to a merely thin one.

Fix, two layers:
1. **`podcast.service._check_grounding`**, called right after rank succeeds in `run_episode`,
   `resume_from_fetch`, and `resume_from_rank` (every entrypoint that could reach outline with a
   freshly- or previously-empty `rank_output`): if `rank_output.selected` is empty, the pipeline
   stops *there* — episode `status="no_content"` (a new, non-error terminal status alongside
   pending/running/done/failed), `EpisodeRecord.no_content_interests` set to the profile's
   interest topics that had zero *scored* candidates (computed from `rank_output.scored`, not
   `fetch_output`, so the same check works uniformly whether or not a `FetchOutput` is even in
   scope — `resume_from_rank` only ever has the `RankOutput`), and a `no_content` event emitted.
   Deliberately distinguishes "this interest had literally nothing this run" from "this interest
   had candidates that just didn't make the cut" — a second no-content test in
   `tests/test_service.py` locks in that a scored-but-unselected interest is *not* listed as
   empty, since that's a different (and much less concerning) situation.
2. **`outline_stage` itself now refuses to run with zero sources (raises `ValueError`)** — a
   defensive backstop for any direct/bypassed call to the stage that doesn't go through
   `service.py`'s guard, so "the pipeline can produce an episode grounded in nothing" is now
   structurally impossible from either direction, not just prevented by the orchestration layer
   remembering to check first.

`no_content` surfaces in the Episodes UI as a distinct amber badge (not the red `failed` one) with
the empty-interests list shown inline, plus a one-line explanation and a nudge toward the fix
(widen that interest's window/queries in Settings) — this is an expected, sometimes-normal
outcome (an interest genuinely having no fresh news this week, extensively documented earlier in
this log), not a bug to alarm the user about.

### Profile validation: exactly two hosts, each with a voice id
Same first run also surfaced that nothing stopped a profile from being saved with a host missing a
`voice_id`, or with one host, or three — `tts_stage` would only discover the problem deep in a
paid pipeline run (raising on the first line whose speaker has no matching host), and the
pipeline's whole design already assumes exactly two (script.py's `hosts[0]`-drives/`hosts[1]`-asks
positional convention). Fixed with a `model_validator(mode="after")` directly on
`PodcastSettings`, not a route-level check: enforced everywhere a `Profile` gets constructed or
parsed — `PUT /profile` (FastAPI turns the raised `ValueError` into a 422 with the message intact,
e.g. `"Value error, exactly two hosts are required, got 1"`), profile YAML loads (`Profile.from_yaml`,
so `import-profile` and the CLI's own `run()` inherit it for free), and DB seeding (below). One
model-level rule instead of duplicating the same two checks at every one of those call sites.

### Seeding the profiles table from YAML on startup
Before this, a fresh `data/podcast.db` left `GET /profile` 404-ing until someone PUT a profile by
hand — fine for tests, awkward for actually running the app the first time. `api/app.py`'s
lifespan now calls `service.seed_profile_from_yaml_if_empty(session)` right after `db.init_db()`:
if the `profiles` table is empty, it loads `$PODCAST_PROFILE_PATH` (default
`profiles/eudald.yaml`) and upserts it — **never** overwrites an existing row, so this only ever
fires once, on a genuinely empty table. A missing/invalid default file is logged and skipped
(`OSError`/`yaml.YAMLError`/`pydantic.ValidationError` all caught) rather than crashing the whole
app at startup — a bad default shouldn't take down the API.

`uv run podcast import-profile <path>` (new CLI subcommand, dispatched in `generate.py:main()` the
same way `serve` is) is the explicit, always-overwrite counterpart —
`service.import_profile_overwrite` loads and upserts unconditionally and lets any error (missing
file, failed validation) propagate straight to the CLI, since an explicit user action should fail
loudly rather than silently no-op like the startup path does. Both funnel through the same
`service.upsert_profile`, so both get the host-count/voice-id validation above for free.

**Test isolation fallout, worth flagging.** Every existing `tests/api/*.py` file's
`_configure_test_db` helper now also sets `PODCAST_PROFILE_PATH` to a nonexistent path — without
it, the new startup seeding was picking up the *real* `profiles/eudald.yaml` from the repo's
working directory during test runs (it exists and validates fine), silently turning every "no
profile yet" test assumption false the moment `TestClient(app)`'s lifespan ran. Caught immediately
by 4 failing tests the same run this feature was added in; not a subtle bug, but a reminder that
"seed from whatever's on disk by default" and "tests run from the real repo checkout" interact by
default unless a test explicitly opts out.

## Outline stance bug: structural schemas over post-hoc validation, and one-retry-with-feedback — 2026-09-13
A real outline run failed `_validate_outline`'s "exactly one stance per host" check — the model
had returned two stances for one host and none for the other. This was always a schema gap:
`stances` was `list[HostStanceResponse]` with a `host: Literal[...]` field *inside* each item — the
same shape already used, and already known to be fragile, for the pre-fix `recurring_bit` string
match (see "Recurring bit id fix" above) and worse here, since nothing stopped the model
duplicating one host's entry and dropping the other's. A `Literal` on `host` only constrains which
*names* are legal per item; it says nothing about how many items, or which combination, the list
as a whole contains.

- **Fix: `stances` is no longer a list at all.** `outline.py:_response_model` now builds an object
  with one *required* field per host name (`create_model("StancesResponse", **{name: (stance_model,
  ...) for name in host_names})`) — `stance_model` itself drops the `host` field entirely (a
  `{attitude, why, arc}` triple). A JSON object can't have a duplicate key and can't omit a
  required one, so "exactly one stance per host" is now true by construction, the same way the
  existing `Literal[tuple(bit_ids)]` on `recurring_bit` makes an invented bit id unrepresentable —
  this fix just applies that same idea one level up, to a whole list's cardinality/coverage rather
  than one field's value set. `outline.py:_convert_story` does the one bit of translation work this
  needs: reading `story_response.stances.<host_name>` back into a `list[HostStance]` for the
  canonical `Outline` (everything else in a response story still round-trips via dump/revalidate,
  since only `stances`' shape actually changed). `_validate_outline`'s existing check is **kept as
  the backstop**, per the same reasoning as every other schema-constrained field in this file
  (recurring bit id, host name) — belt-and-braces against a future refactor or a non-`.parse()`
  code path, not because the schema is expected to fail.
- **General lesson, worth stating plainly since it's now paid off twice:** whenever a response
  shape can be checked for validity by *construction* (a fixed enum, a fixed set of required
  object keys) rather than only by inspecting the value afterward, prefer the structural fix. It
  moves a whole class of model mistakes from "possible, then caught downstream" to "impossible to
  express in the first place" — cheaper for the model to get right (there's no wrong shape to
  accidentally produce) and cheaper for us to reason about (no code path where the bad shape
  reaches validation and has to be rejected).

**One-retry-with-feedback: the cheap resilience policy for outline/script/critique.** Structural
schemas close off "wrong shape" mistakes, but they don't stop the model from producing a
well-shaped response that still fails a check only inspectable after the fact — an outline citing
a source_id that doesn't exist, a script segment citing an article it wasn't given, critique output
that (hypothetically) breaks grounding. Before this, any such `ValueError` from `_validate_outline`/
`validate_source_ids` crashed the stage outright, discarding an otherwise-almost-right response and
forcing a full manual re-run. Added `podcast/llm_retry.py:generate_with_retry(generate, validate,
user_prompt, stage_name)` — generic, shared by all three LLM-backed stages (not three copies of the
same try/except): call `generate`, run `validate` (raises `ValueError` on a bad result), and on
failure, retry **exactly once** with the error message appended to the prompt as corrective
feedback; a second failure propagates unchanged, exactly as before this existed.
- **Why exactly one retry, not N or exponential backoff.** This is a cheap, targeted fix for "the
  model almost got it right" — telling it precisely what was wrong (the same `ValueError` message
  a human debugging this would read) is usually enough to fix a source_id/coverage mistake in one
  more try. A second failure is much more likely a deeper prompt/data problem that another retry
  won't fix — better to surface it (crash/log, per the existing failure-handling policy) than mask
  it behind silent retries that just burn more LLM cost for the same outcome.
- **Only a `ValueError` from `validate()` triggers a retry** — not any exception `generate()` itself
  raises (an OpenAI API error, a network blip). Those are a different failure class entirely (the
  call itself didn't succeed, so there's no "response" to give feedback about) and should surface
  immediately, not be retried with a nonsensical "your previous response was invalid" prompt.
- **Wiring per stage**: `outline_stage`/`script_stage`/`critique_stage` each wrap their existing
  `_generate_X` + validate call in two small closures (`generate(prompt)`, `validate(result)`)
  capturing everything else (client, model, system_prompt, fixed context) so `generate_with_retry`
  only ever needs to know about the one thing that changes on a retry: the user prompt.
  `critique_stage`'s `validate` closure is the one wrinkle — grounding can only be checked on the
  script the critique would *produce*, so it applies the critique first (`_apply_critique`, pure
  and cheap) before validating, and `critique_stage` reapplies it once more after a validated
  result comes back to build the real `revised_script`, rather than threading the applied script
  back out of the generic retry helper.



## Dashboard metrics: extended GET /metrics/summary, seed-metrics, /dashboard — 2026-09-14
Extended the existing `GET /metrics/summary` (episode counts, ElevenLabs cost/characters only)
into the full dashboard payload, added `podcast seed-metrics` (mocked-but-plausible usage data
so the dashboard isn't empty on a fresh DB), and built the actual `/dashboard` React page
(recharts) against it.

- **Which metrics, and why (product framing).** KPI row: **episodes done/total** (is the
  pipeline/scheduler actually producing content, not just configured to); **completion rate**
  (plays vs. finished plays — separates "nobody's listening" from "people start and bail," a
  script-quality signal, not a generation-pipeline one); **D7 retention** (the clearest signal a
  personal podcast has earned a place in someone's routine vs. being tried once); **cost/episode**
  (the number that decides whether this scales past a personal project, both providers combined).
  Charts: **episodes & plays per day** (supply vs. demand — episodes with no plays is a discovery/
  distribution problem); **cost by stage** (confirms the deliberate expensive-model trade-off on
  `script`/`critique` — see "Script rebuild" — is actually where the money goes, not silently
  ballooning elsewhere); **topic distribution** (which interests get real airtime — distinct from
  `no_content_interests`, which tracks the opposite: zero candidates); **recent failures/no_content**
  (the exact stage + reason each broken run stopped at, to separate "the feed was empty this week"
  from "the API key expired").
- **OpenAI token usage wasn't tracked anywhere before this** — only ElevenLabs characters were
  (`TTSOutput.total_characters`). "Cost per episode... from the persisted manifests" needed
  `completion.usage` captured at each `chat.completions.parse(...)` boundary (rank/outline/script/
  critique) and persisted onto that stage's own existing manifest as a new `usage: list[TokenUsage]`
  field (a list, not one value — a rejected-then-retried attempt in `llm_retry.generate_with_retry`,
  or a rescored echo-mismatch batch in `rank.py`, both cost real, billed tokens and both are counted,
  not just the attempt that ultimately validated). This is the one place this change touches pipeline
  internals rather than just the API/frontend layer, and it broke the return contract every stage
  test monkeypatches directly (`_score_batch`, `_generate_outline`, `_generate_script`,
  `_generate_critique` now return `(domain_object, TokenUsage)`) — all four stage test files updated
  accordingly. Fetch's own two LLM calls (`_generate_queries`, `_generate_suggestion`) are
  deliberately out of scope: one-off/cached-to-YAML or settings-UI calls, not part of any per-episode
  manifest.
- **Illustrative OpenAI pricing table** (`podcast.metrics.OPENAI_PRICING_PER_1K_TOKENS`), same
  "not a real bill" caveat as the pre-existing `service.COST_PER_1K_CHARS_USD` — rough list-price
  order of magnitude for `gpt-4o-mini`/`gpt-4o`, a flat fallback for any other model string a
  profile might set.
- **Cost is computed from persisted manifests at request time, not cached in the DB.** Matches the
  task's explicit ask ("from the persisted manifests") and this app's existing philosophy (every
  stage's output is the source of truth on disk). Re-reads every episode's manifest files on every
  `GET /metrics/summary` call — a real scale tradeoff, fine at this app's size (a personal, single-
  profile, dozens-to-hundreds-of-episodes app), revisit (cache onto `EpisodeRecord` at `_finish`
  time) if this endpoint is ever called often against a much larger episode history.
- **Mocked data is DB-only, not fabricated manifest files.** A mocked `EpisodeRecord` has no
  `data/episodes/<id>/` directory at all — its cost/topic breakdown lives directly on two new JSON
  columns (`mock_cost_by_stage`, `mock_topic_counts`) instead. Considered writing full fake
  rank.json/outline.json/etc. per mocked episode so one code path (`podcast.metrics.
  episode_cost_breakdown`) would serve real and mocked episodes identically with zero branching —
  rejected: meant fabricating plausible fake articles/scripts at volume (up to ~90 episodes) for no
  benefit the dashboard actually needs (it never renders a mocked episode's script/show notes), and
  blurred "every stage persists its output" into also meaning "...even a stage that never ran."
  `episode_cost_breakdown`/topic tallying just branch once on `record.mocked` instead.
- **`events`/`episodes` both gained a `mocked: bool` column** (additive migration, following the
  existing `_migrate_add_missing_columns` pattern — generalized it from one hardcoded column to a
  small `(table, column, sql_type)` list, since it now needs four). `podcast/seed_metrics.py` writes
  every mocked row directly via `db.EpisodeRecord`/`db.EventRecord`, never through
  `service.run_episode`/`service.emit_event` — "real events are never mocked" holds structurally
  (there's exactly one writer of `mocked=True` rows in the whole codebase), not just by convention.
  `service.py` itself needed zero changes for this feature.
- **The mocked window is `[today - days, today - 1]` — strictly before today.** So mocked and real
  data never land on the same calendar day, which makes each `DailyPoint.mocked` flag unambiguous
  (a day is either fully real or fully mocked, in practice) with no need to merge/flag at the
  individual-row level within a day.
- **40 "users" are synthetic `user_id` strings in event metadata, not a real users table** — this
  is a single-profile app with no listener-identity concept today, and a real per-user schema is out
  of scope for a demo-data seeder. Each gets a staggered join day and a decaying return probability
  (~90% day-of-join, tapering toward a ~10% floor) tuned so aggregate D7 retention lands in a
  plausible ~30-45% range. Consequence: **D7 retention and per-user metrics are `None`/absent until
  either mocked data exists or a real per-listener identity is built** — real `played`/
  `completed_playback` events carry no `user_id` today (`EventIn` doesn't ask for one), so
  `podcast.metrics._d7_retention` has nothing to compute from until then. Raw play/completion
  *counts* and completion *rate* don't need user identity and do reflect real events immediately.
- **`--force` on `seed-metrics` only ever deletes rows it previously marked `mocked=True`** — never
  files/directories, and never a row it didn't write itself — per CLAUDE.md's "data/ is never
  deleted." A reseed with the same `(users, days, seed)` is byte-for-byte reproducible (verified in
  `tests/test_seed_metrics.py`).
- **Dashboard**: recharts (as directed), colors from the dataviz skill's validated default
  categorical palette used verbatim (no brand substitution needed — this app has no existing chart
  palette to match). Provider identity is fixed everywhere it appears: OpenAI is always the palette's
  slot-1 blue, ElevenLabs always slot-2 orange. Chart entrance animations are disabled
  (`isAnimationActive={false}`) — a monitoring dashboard should show its numbers immediately, not
  animate them in; also sidesteps a real headless-screenshot artifact hit while verifying this (bars/
  lines invisible mid-animation).
- **`GET /metrics/summary`'s original fields are byte-for-byte unchanged** (`total_episodes`, `done`,
  `failed`, `total_characters`, `total_cost_estimate_usd` — ElevenLabs-only, unchanged semantics —
  `avg_duration_s`); the extended fields are new, additive keys on the same response, computed in a
  new `podcast/metrics.py` (pure, no FastAPI/pydantic dependency, unit-tested directly in
  `tests/test_metrics_aggregation.py` — named `_aggregation` rather than `test_metrics.py` only to
  avoid a same-basename collision with the pre-existing `tests/api/test_metrics.py` under pytest's
  default rootdir-relative import mode) rather than folded into the existing route handler's own
  queries.

## Evergreen fallback for empty interests — 2026-09-14
An interest with zero real (fetched) selected candidates after rank's normal selection +
backfill used to just contribute nothing — and if *every* interest came up empty in the same
run, the grounding guard (`service._check_grounding`) stopped the episode at `no_content`
entirely (see "Grounding guard"). Now `rank_stage` tries one Wikipedia primer per empty
interest before giving up on it, via a new `podcast/evergreen.py`.

- **Why news always wins.** `_fill_empty_interests_with_evergreen` (rank.py) only ever
  considers an interest that (a) has zero real selected candidates *and* (b) got a nonzero
  budget slot this run — it never runs for an interest that already has real content, and
  never competes with real news within that interest's own bucket (there's nothing to compete
  with by construction: it only fires when the bucket is empty). Zero budget is left alone on
  purpose too — that's the profile's own weighting saying this interest shouldn't get airtime
  this episode at all, a deliberate choice evergreen has no business overriding; it only fills
  a genuine content gap, never a weighting gap.
- **Why the fixed 0.5 score.** `evergreen.EVERGREEN_SCORE` sits between the rank scoring
  rubric's "tangential" (0.4) and "clearly related" (0.7) tiers (see "Rank stage"'s scoring
  call). It never needs to outrank a real candidate in the same bucket (there isn't one, per
  above) — what it actually controls is the *global* cross-interest ordering
  (`selected.sort(key=lambda a: scores[a.source_id].score * weight...)`, unchanged): a primer
  sits in the middle of the pack, so a good news day from other interests (scoring 0.7-1.0)
  still leads the episode, and a primer only edges out another interest's own weak/tangential
  real news if that interest's best candidate scored under 0.5 — which is roughly the point
  where a human would call that candidate a stretch anyway.
- **Why a flat-file cache, not the existing profile-YAML query cache.** `Interest.queries`
  caches into the profile YAML via `Profile.to_yaml` (see "Freshness gap fix"), but that only
  ever runs from the CLI (`generate.py:run()` calls `ensure_interest_queries` before
  `service.run_episode`) — `POST /episodes` (the API path) never touches the profile file at
  all. A YAML-based evergreen cache would silently never engage for API-triggered runs, which
  is now the primary path (the scheduler, the settings UI). `paths.EVERGREEN_CACHE_PATH` (a
  flat `data/evergreen_cache.json`, keyed by interest topic) works identically from either
  entrypoint, at the cost of being one more small file outside the per-episode manifest
  convention — justified since this state is explicitly cross-episode (anti-repetition memory),
  not something any single episode owns.
- **Why 7 days, and why "reuse anyway" as the fallback.** The cache's job isn't performance
  (search+extract is cheap and not on any per-request hot path) — it's remembering the last
  page used per topic so the *next* empty-interest attempt within a week picks the next-best
  search result instead of repeating verbatim (a listener hearing the identical primer two
  days running reads as broken). Past 7 days, repeating is fine/expected — a slow-news topic's
  best-matching Wikipedia page doesn't stop being the best match just because it was used a
  week ago. If the only search candidate *is* the recently-used page, `_pick_title` reuses it
  anyway rather than returning `None` — a repeated primer still beats the alternative
  (`no_content`), so the anti-repeat rule yields to the "no_content only when even evergreen
  fails" requirement rather than the other way around.
- **`OutlineStory.is_primer`** is code-derived after the outline LLM call
  (`bool(source_ids & evergreen_ids)`), the same "compute it, don't ask the model to
  self-report it" reasoning already established for `word_budget`. The outline prompt still
  gets an explicit `[EVERGREEN PRIMER]` marker + framing instruction (own story, "introduce or
  deepen the topic... no urgency" — see `_EVERGREEN_INSTRUCTION`) so the *model* writes the
  right angle in the first place; `_validate_outline` backstops it in code by rejecting a story
  that mixes an evergreen source with a real-news source, the same structural-schema-first,
  code-check-as-backstop pattern as the existing recurring-bit/tangent mutual-exclusion check.
  `script.py:_render_outline_story` restates the no-news-framing instruction per-story (a
  separate LLM call with no memory of outline's own prompt) exactly where tangent/recurring-bit
  framing already lives, rather than a global system-prompt rule that would apply to every
  segment regardless of whether it's actually a primer.
- **`RankOutput.evergreen_count`** is its own field, not folded into `backfilled` — a primer is
  a different *source* substituting for an empty interest, not a within-pool reorder after a
  same-source candidate failed, so conflating the two would blur two genuinely different signals
  in the audit trail (`ranked.json`, the print summary, and the `stage_done` event metadata all
  report it alongside `backfilled`).
- **`service.py` needed zero changes.** `_check_grounding` already only checks
  `if rank_output.selected: return True` — since evergreen fills `selected` *inside*
  `rank_stage` before it ever returns, `no_content` already only fires once every interest's
  evergreen attempt has also come back empty, exactly as asked, for free.

## Listener.facts — 2026-09-14
Added `Listener.facts: list[str] = []` and render it into the script prompt in place of the old
bare-name-only line (`_render_listener`, `script.py`).

- **This was a latent bug, not just a new field.** `profiles/eudald.yaml` already had a
  `listener.facts:` list (four real facts — piano, League of Legends, calisthenics, an MSc) that
  pydantic's default `extra="ignore"` behavior on `Listener` was silently dropping on every load,
  since the model only declared `name`. The script prompt's "Listener: {name}." line — and every
  comment in outline.py/script.py saying "currently just their name, unless more is listed" — was
  quietly stale the whole time the YAML had more listed. Loading the real profile after this
  change confirms all four facts now reach `Profile.podcast.listener.facts`.
- **Scoped to the script prompt only, per the ask.** `outline.py`'s tangent instruction ("may
  only build on facts actually given above") and `critique.py`'s `invented_listener_detail` check
  ("everything actually known about them") both still show only `listener.name` — so a script-
  stage tangent can now reference a real fact, but outline can't suggest one from it, and critique
  can't tell a genuine fact from an invention if it ever saw one. Flagging as a follow-up, not
  fixed here: `_render_listener` (script.py) would need to move somewhere both stages can share,
  or each grows its own equivalent render call.
- The existing "never invent hobbies, opinions, preferences, or biography" hard rule is unchanged
  in spirit — it still bounds the model to exactly what's rendered, now just a short list instead
  of always a bare name.

## Perform stage: content vs. performance separation — 2026-09-15
Added a fourth script-related LLM stage, `perform` (`podcast/stages/perform.py`), between
`critique` and `tts`. Takes critique's grounded `revised_script`, rewrites every line for voice
performance — punctuation for flow, spoken (not enumerated) lists, one capitalised emphasis word
per line where it matters, a delivery *arc* plus inline v3 audio tags, varied sentence contours,
and a few lines of cold-open chit-chat before the existing show/host identification — and
persists `performance.json`. `tts_stage` now reads `performance.json` instead of
`script.json`/`critique.json`.

- **Why a separate stage from critique, not one more critique criterion.** `critique.py` fixes
  *what's wrong* by splicing rewrites into only the flagged lines — a narrow, targeted
  intervention. Performance is a full-script rewrite (every line's text changes, not just flagged
  ones) with a different risk profile (it must never touch a fact, only how the line sounds) —
  different enough in scope and blast radius that it earns its own artefact, validation, and
  retry cycle instead of overloading critique's flagged-line model.
- **Why this is the last audio-shaping change before synthesis.** `tts_stage` now sends
  `PerformedLine.text` straight to ElevenLabs, verbatim — the old `Line.delivery`-as-bracket-
  prefix mechanism (`tts.py:_tagged_text`, from "Voice and dynamics pass") is gone entirely. Any
  v3 audio tag lives inline in the text itself, written by the perform stage at the point the
  emotion actually shifts, not bolted on as a line-start prefix afterward. Nothing downstream of
  `perform` touches line text again, so this is deliberately the final place text/delivery is
  decided — a future stage inserted between `perform` and `tts` would be a considered decision,
  not something that could happen by accident.
- **Grounding stays structural, not re-derived.** `PerformedSegment.headline`/`.source_ids` are
  always overwritten from the matching original segment by index in code
  (`perform.py:_apply_grounding`), never trusted from the model — the same "safe by construction"
  philosophy as `critique.py:_apply_critique`. This only works because the performance-writing
  model is held to a strict structural contract, enforced via `generate_with_retry`'s one-retry-
  with-feedback: every segment and the outro must end up with the same line count *and* speaker
  sequence as the original (only text/delivery/pause_ms may change); the cold_open may grow, but
  only by *prepending* new chit-chat lines — the original cold_open's own speakers must still
  appear, in order, as the tail of the performed cold_open (`_validate_cold_open_suffix`). A
  cold_open of length 0 passes trivially, since there's nothing for chit-chat to be prepended to.
- **Fact-check is a second, cheap-model call, not embedded in the writer's own self-report.**
  `_generate_fact_check` (`profile.llm.model`, not `script_model`) compares the original and
  performed text of every 1:1-aligned segment/outro line pair and flags any pair whose meaning
  drifted (`FactChangeFlag`, `PerformOutput.fact_flags`) — informational, like critique's
  `over_budget_segments`/`terse_hosts`, not retried or auto-corrected. New cold-open chit-chat
  lines are excluded from this check — they have no original counterpart to compare against, and
  they're host banter, not sourced content.
- **Cost.** One `script_model`-tier call (comparable to or larger than critique's, since it
  rewrites the whole script rather than just flagged lines) plus one cheap `model`-tier call for
  the fact-check. `seed_metrics.py`'s `_STAGE_TOKEN_RANGES["perform"]` only covers the primary
  call — the fact-check call's cost isn't separately mocked, the same convention already used for
  rank's unmocked echo-mismatch rescoring.
- **`artefacts.tts_script_output` (critique → ScriptOutput wrapper) is removed** — dead now that
  `tts_stage` reads `PerformOutput` directly via the new, public `perform.py:
  flatten_performed_lines`. `resume_from_critique` gained an `articles` parameter (needed by
  `perform_stage`'s grounding re-check) — `generate.py:run_from_critique` now also loads the
  episode's `ranked.json`, the same pattern `run_from_script`/`run_from_outline` already use for
  auxiliary artefacts.
- **Explicit scope boundary.** `routes_episodes.py`'s `_load_script_and_notes` (the API's
  displayed script) still reads `critique.json`, unchanged — `Performance`'s line shape (arc-
  description `delivery`, inline audio tags, capitalised emphasis) isn't meant for a "read the
  script" display and wasn't asked to be wired in.

## Packaging: Dockerfile, docker-compose, README/solution.md — 2026-09-15
Added a multi-stage `Dockerfile`, a single-service `docker-compose.yml`, a filled-in `README.md`,
and `solution.md`.

- **Why one container, not one for the API and one for the built SPA.** `podcast/api/app.py`
  already serves the built React SPA (`web/dist/`) as static files from the same FastAPI process
  in production — that decision was made when the UI was added (see "User-facing UI"), precisely
  so there'd be no separate frontend server to deploy or CORS-configure at runtime. Splitting
  packaging into two containers would reintroduce exactly the origin-split problem that design
  avoided, for no benefit: the SPA has no independent runtime (no server-side rendering, no API
  of its own), it's just static files the same process already knows how to serve. One container
  matches the one-process architecture the app already has.
- **Multi-stage build.** Stage 1 (`node:22-bookworm-slim`) runs `npm ci && npm run build` to
  produce `web/dist/`; stage 2 (`python:3.12-slim-bookworm`, matching `.python-version`) never
  sees Node, npm, or `web/node_modules` — only the built `dist/` output is copied across. Keeps
  the shipped image to Python + the app's own dependencies + `ffmpeg` (pydub's only system
  dependency, for `stitch_stage`'s mp3 concatenation) + the uv binary itself (copied directly from
  astral's own distroless image, no pip/curl bootstrap needed). `uv sync --frozen --no-dev` in the
  final stage installs exactly `pyproject.toml`'s runtime dependency set — the `dev` group
  (`pytest`) never reaches the image, per the task's own "no dev dependencies in the final stage."
  Dependencies are installed in a layer before app source is copied in, so an app-only code change
  doesn't invalidate the (comparatively slow) dependency-resolution layer on rebuild.
- **Why a named volume for `data/`, not baking it into the image or a bind mount.** `podcast/
  paths.py`'s `DATA_DIR`/`podcast/db.py`'s `DB_PATH` are both relative (`data/podcast.db`,
  `data/episodes/<id>/...`) — the sqlite database *and* every episode's persisted artefacts/audio
  live there, and CLAUDE.md's own architecture rule is explicit: "data/ is never deleted." Baking
  `data/` into the image would mean every `docker compose build` (or any image rebuild) silently
  resets the database and discards every episode ever generated — the opposite of what a
  from-scratch pipeline that persists every stage's output for exactly this reason wants. A named
  volume (`podcast-data`, declared in `docker-compose.yml`) persists across container recreation
  and image rebuilds alike, without depending on a specific host path the way a bind mount would
  (matters for reproducibility across machines/CI, not just this one setup). `VOLUME ["/app/data"]`
  in the `Dockerfile` documents the mount point even for someone running the image directly,
  without compose.
- **`solution.md`** is a condensed write-up (architecture, key trade-offs, known simplifications,
  future work) distilled from this decision log, for a reviewer who wants the summary before the
  full dated history. **`sample.mp3`** (repo root) is a real generated episode's audio, copied
  from an actual pipeline run (`data/episodes/20260914T165224Z-4bec4c/episode.mp3`) rather than a
  fabricated demo clip — `data/` itself stays gitignored (per-user, per-run state), so this one
  file was deliberately promoted to a tracked, committed asset instead.
- **Verification: CI, not a local run.** Docker wasn't available in the environment this was
  authored in (no Docker Desktop, no WSL) — rather than ship the Dockerfile/compose file unverified
  or claim a check that didn't happen, added `.github/workflows/docker.yml`: on every push/PR
  touching the Dockerfile, compose file, or app source, it builds the image, runs it with dummy
  (non-functional) API keys — the app boots and serves `/`/`/docs` without needing real keys,
  those are only read lazily inside pipeline stage functions — and asserts `/` returns the built
  SPA's HTML (checked for Vite's `<div id="root">` mount point, not just a 200) and `/docs`
  returns FastAPI's Swagger UI. Real, repeatable verification on every future change, not a
  one-off manual check that goes stale.
- **`sample.mp3` refreshed mid-task.** While writing this entry, noticed the episode this was
  copied from (`20260914T165224Z-4bec4c`) had been regenerated (script.json/performance.json/
  tts_manifest.json/episode.mp3 all newer than the copy) — presumably a real run through the new
  perform stage, done separately from this session. Re-copied so `sample.mp3` reflects the current
  audio, not a stale pre-perform-stage version.

## Measured words-per-minute — 2026-09-15
`outline_stage`'s and `script_stage`'s word budgets used a flat, guessed `duration_minutes * 150`
— never measured against anything the pipeline actually produced. Replaced with
`LLMSettings.words_per_minute: float`, profile-overridable, defaulting to a real measurement.

- **Measurement: `podcast.metrics.measured_words_per_minute()`.** Scans `data/episodes/*/` for
  every directory with BOTH `performance.json` and `stitch_manifest.json` — a real, fully
  synthesized episode, not a partial run — sums `performance.json`'s word count (the text
  actually sent to ElevenLabs) and `stitch_manifest.json`'s measured audio duration across all of
  them, and returns `total_words / total_minutes` (aggregated, not an average of per-episode
  rates, so a longer episode weighs proportionally more). Not called automatically anywhere in
  the pipeline — a one-time-by-hand measurement to calibrate the default, re-run as more real
  episodes accumulate. Verified self-consistent before trusting it: the one qualifying episode's
  `performance.json` per-line character counts matched `tts_manifest.json`'s exactly, and
  `generated_at` timestamps show a coherent perform → tts → stitch sequence a few minutes apart —
  real synthesized audio, not stale or mismatched artefacts.
- **The measured number: 135.8 wpm** (1,607 words / 11.832 minutes, `episode_count=1`). Below the
  150 wpm this replaces, but above this project's own prior ~100-110 guess — dialogue-mode
  ElevenLabs synthesis with inter-turn pacing and audio tags apparently runs a bit slower than a
  flat human-speech-rate assumption, but not as slow as guessed. **Single real data point** — the
  perform stage only just shipped, so exactly one completed episode has a `performance.json` to
  measure from. Worth re-running this measurement (and revisiting the default) once several more
  real episodes exist; noted directly in `LLMSettings.words_per_minute`'s docstring so it isn't
  mistaken for a settled number.
- **Why a profile field, not a hardcoded constant.** `words_per_minute` already sits alongside
  `model`/`script_model` on `LLMSettings` — a profile-level knob, not a pipeline-wide constant —
  since actual spoken pace is a property of the configured hosts/voices/synthesis mode
  (dialogue vs. per-line fallback, v3 audio tag density, a specific host's persona-driven
  delivery), not a universal constant every profile should share. The measured 135.8 is this
  profile's own real rate; a differently-configured profile (different voices, no audio tags,
  per-line fallback) would plausibly measure differently.
- **The +10% perform-stage word cap.** `perform.py:MAX_WORD_OVERRUN = 0.10` — the performed
  script's total word count (cold_open + segments + outro, including any added cold-open
  chit-chat) may not exceed the critique script's own word count by more than 10%, checked by
  `_validate_word_cap` and wired into the existing `_validate_performance`/`generate_with_retry`
  pipeline (see "Perform stage" entry) — so a violation is flagged and fed back for exactly one
  retry, the same mechanism every other structural check in that stage already uses, not new
  machinery. Directly protects the word-budget/wpm chain: `outline_stage` sizes segments from
  `words_per_minute`, and a performance pass that quietly inflated word count on top of that
  would silently blow the episode's target duration back out regardless of how well-calibrated
  the budget was. The cap is also stated to the writing model up front (the original word count
  and the exact max, in the system prompt) so a violation is the exception, not the common case,
  not something the retry is expected to catch routinely.
- **Test fixture fallout.** `tests/test_perform.py`'s `_valid_performance()` "happy path" fixture
  was, it turned out, already over the new 10% cap once its optional cold-open chit-chat line was
  included (22 words performed vs. 19 original, cap 20) — shortened that one fixture line
  ("Hey, welcome back." → "Hey.") to land at exactly the cap boundary; every other test asserting
  its exact text was updated to match. `tests/test_outline.py`'s word-budget test pinned
  `profile.llm.words_per_minute = 150` explicitly rather than depending on whatever the measured
  default happens to be — decouples that test's round-number arithmetic from a value this entry
  says outright isn't settled yet.

## Outline transitions — 2026-09-16
The outline used to hand the script step a bare story order with no guidance on how to bridge
between stories — script.py improvised the segue every time, with nothing stopping it from
implying a connection between two unrelated stories just because they happened to be adjacent.

- **`Transition{kind, text_hint}` on `OutlineStory`.** `kind` is `"link"` (a genuine connection —
  shared mechanism, same person/organization, same underlying tension) or `"clean_transition"` (a
  short, neutral handoff, no claimed connection). `text_hint` is a short phrase, not dialogue, for
  script.py to build the actual connecting/handoff lines from. `None` only for the first story —
  nothing precedes it; every later story requires one, enforced in `outline._validate_outline`
  (same one-retry-with-feedback path as every other structural check in that stage).
- **Default to `clean_transition`, never invent a link.** Stated directly in the outline prompt:
  pick `link` only when the connection is real, default to `clean_transition` when unsure. This is
  a prompt-level instruction, not something code can verify (whether a connection is "genuine" is
  a judgment call) — the structural guarantee is only that every non-first story picks *one* of
  the two kinds, not which one is correct.
- **script.py renders and acts on it**, not just outline.py producing it: `_render_outline_story`
  adds a `Transition in from the previous story: ...` line, and the system prompt tells the
  writing model to actually build the opening lines from a `link`'s hint (not just assert
  relatedness) or use a short neutral pivot for `clean_transition` — the same "never manufacture a
  link that isn't in the hint" rule restated at the writing step, since that's a separate LLM call
  with no memory of the outline prompt.
- **Logged, not just persisted.** `outline_stage` logs each story's chosen transition (kind +
  hint) at INFO after a valid outline is produced — visible in a normal run without opening
  `outline.json`.

## Re-measured words-per-minute, dashboard mocked-data toggle — 2026-09-16
`LLMSettings.words_per_minute`'s default was pinned from a single real episode (see "Measured
words-per-minute" above, 135.8 wpm, `episode_count=1`) with an explicit note to re-run
`measured_words_per_minute()` once more real episodes existed. Two more have completed since
(both through the `perform` stage) — re-running it now gives **140.0 wpm** (2,900 words / 20.715
minutes, `episode_count=2`), the new default. Still not a large sample; the same re-run-later note
stays on the field's docstring.

- **Dashboard KPIs now default to real episodes only.** `metrics.aggregate_summary` gained an
  `include_mocked: bool = False` parameter — `has_mocked_data` is still computed over *every*
  episode/event row (so the frontend knows whether a toggle is worth showing at all), but the
  actual KPI numbers, cost/stage breakdowns, topic distribution, stage durations, and daily series
  are computed only from real (non-mocked) rows unless the caller opts in. `GET /metrics/summary`
  takes the same `include_mocked` as a query param (default `false`) and echoes it back on the
  response, so the frontend has one source of truth for which mode produced the numbers it's
  showing rather than tracking it only in local component state.
- **Why default real-only, not combined.** The dashboard's whole point is judging the pipeline's
  actual production behavior (see each KPI's `why` copy in `Dashboard.jsx`) — `podcast
  seed-metrics`' plausible-but-fake rows exist only so the dashboard has something to render on a
  fresh install, and silently blending them into the default view understates how thin the real
  data still is. Kept as an opt-in toggle, not removed outright, since demoing the dashboard's
  full shape (retention curves, daily series with real volume) still needs the seeded rows when
  there's only a handful of real episodes.
- **Frontend**: `Dashboard.jsx` holds `includeMocked` state (default `false`), refetches on
  toggle, and always labels the active mode ("Real episodes only" / "Real + mocked demo data")
  next to the toggle — never lets the numbers change without the label changing too. The toggle
  itself is only rendered when `has_mocked_data` is true (nothing to switch to otherwise); the
  existing violet "some numbers include seeded demo data" banner now only shows in the
  `includeMocked` mode, since in real-only mode that's no longer true.

## API routes under /api — 2026-09-16
`GET /episodes` was both the API's "list episodes" endpoint and (via `App.jsx`'s React Router
config) the SPA's `/episodes` page. FastAPI matches routes in registration order and the API
router was registered before the SPA's catch-all fallback, so a hard refresh or direct navigation
to `/episodes` always hit the API handler — the browser rendered raw JSON instead of the app. `/`
and `/settings`/`/dashboard` happened not to collide with any API route, so this only ever showed
up on `/episodes` specifically, easy to miss.

- **Fix: every API router now mounts under `/api`** (`app.include_router(routes_x.router,
  prefix="/api")` in `api/app.py`) — `GET /api/episodes`, `PUT /api/profile`,
  `GET /api/metrics/summary`, `GET /api/schedule/next`, `POST /api/interests/suggest`,
  `GET /api/voices`. The SPA's own path namespace (`/`, `/settings`, `/episodes`, `/dashboard`)
  and the API's (`/api/...`) are now structurally disjoint — no amount of adding SPA routes or API
  routes can collide again, which is a stronger guarantee than "the catch-all is registered last"
  ever was.
- **`/docs` stays at the root.** FastAPI's interactive docs (`docs_url`, default `/docs`) are
  independent of router prefixes — they list whatever paths the included routers actually expose
  (now the `/api/...` ones), but the docs UI itself is still served at `/docs`, unaffected by this
  change.
- **Frontend**: `web/src/api.js` prefixes every request with a single `const API = '/api'`;
  `web/vite.config.js`'s dev-server proxy collapsed from a per-endpoint path list to one `/api`
  proxy entry, which is also simpler to keep correct as endpoints are added.
- **Tests**: every `TestClient` call in `tests/api/*.py` updated to the `/api/...` paths — this is
  a route-shape change, not a behavior change, so no test assertions beyond the URLs themselves
  needed touching.
- **CI**: `.github/workflows/docker.yml` gained a direct regression check for the bug this fixes —
  `curl` a hard-refresh-style `GET /episodes` and assert it returns the SPA's `<div id="root">`
  HTML (not JSON), alongside a check that `GET /api/episodes` still returns a JSON array at its
  new address.

## Future work.
- Add **suggest topics from previous episodes** from the previous podcasts. So it generatos a topic 
(or a bunch of topics) for a podcast for you.