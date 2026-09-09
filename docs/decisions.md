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


- a missing key should abort in a second with a readable message, not halfway through a paid pipeline with a stack trace.

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