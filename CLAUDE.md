# Personal podcast generator (take-home, Prosper AI)

## Goal
Given a user profile of interests, gather recent news/papers, write a two-host
podcast script grounded in the fetched sources, synthesise it with ElevenLabs,
output an mp3. Later: FastAPI backend, scheduler, settings UI, metrics dashboard.

## Architecture rules
- Pipeline = pure stages in `podcast/stages/`: fetch -> rank -> script -> tts -> stitch.
  Each stage: typed input -> typed output (Pydantic), no hidden state.
- Every stage persists its output under `data/episodes/<episode_id>/`.
  Re-running a stage must be possible from the previous artefact.
- Script segments must reference `source_ids` from fetched articles. Never
  invent facts not present in the sources.
- Secrets only via `.env` (python-dotenv). Never log or commit keys.
- Keep dependencies minimal: feedparser, trafilatura, openai, elevenlabs,
  pydantic, pyyaml, pydub. Ask before adding others.

## Working style
- Propose a short plan and the file list before writing code; wait for OK.
- Small, reviewable diffs. One stage per task.
- Explain non-obvious trade-offs in one or two lines in `docs/decisions.md`.
- Tests: a smoke test per stage with a fixture, no network in tests.
- Never delete files or directories outside /tmp or paths you created this session; ask first. data/ is never deleted.

## Commands
- `uv run python -m podcast.generate --profile profiles/eudald.yaml`
- `uv run pytest`