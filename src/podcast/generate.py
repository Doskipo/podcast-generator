"""CLI entrypoint: uv run python -m podcast.generate --profile profiles/eudald.yaml

Thin wiring only — every entrypoint here loads/derives its inputs (a profile
from YAML, or a persisted artefact from disk) and delegates the actual stage
sequencing, DB bookkeeping, and event emission to podcast.service, the same
module the API uses for POST /episodes. A stage exception is not caught
here, so the CLI still crashes with a full traceback exactly like before
this module had a service layer behind it — see docs/decisions.md ("Backend:
SQLite + FastAPI + APScheduler") for why that's deliberate.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from podcast import db, service
from podcast.artefacts import load_episode_manifest, load_outline_output, load_rank_output
from podcast.models import CritiqueOutput, FetchOutput, OutlineOutput, Profile, RankOutput, ScriptOutput
from podcast.stages.fetch import ensure_interest_queries

logger = logging.getLogger(__name__)

# Pipeline order, for --until: stop right after the named stage runs.
STAGES = ["fetch", "rank", "outline", "script", "critique", "tts", "stitch"]


def run(profile_path: str, episode_id: str | None = None, until: str | None = None) -> str:
    profile = Profile.from_yaml(profile_path)
    if ensure_interest_queries(profile, profile_path):
        print(f"cached generated search queries into {profile_path}")

    with db.session_scope() as session:
        profile_row = service.upsert_profile_from_yaml(profile, session)
        profile_id = profile_row.id

    return service.run_episode(profile, profile_id, episode_id, until)


def run_from_articles(articles_path: str, until: str | None = None) -> str:
    """Re-run from a persisted articles.json: rank -> outline -> script ->
    critique -> tts -> stitch."""
    articles_file = Path(articles_path)
    episode_id = articles_file.parent.name
    episode = load_episode_manifest(articles_file.parent)
    fetch_output = FetchOutput.model_validate_json(articles_file.read_text(encoding="utf-8"))

    with db.session_scope() as session:
        profile_row = service.upsert_profile_from_yaml(episode.profile, session)
        record = service.get_or_create_episode_record(session, episode_id, profile_row.id)
        return service.resume_from_fetch(session, record, episode, fetch_output, until)


def run_from_ranked(ranked_path: str, until: str | None = None) -> str:
    """Re-run from a persisted ranked.json: outline -> script -> critique ->
    tts -> stitch."""
    ranked_file = Path(ranked_path)
    episode_id = ranked_file.parent.name
    episode = load_episode_manifest(ranked_file.parent)
    rank_output = RankOutput.model_validate_json(ranked_file.read_text(encoding="utf-8"))

    with db.session_scope() as session:
        profile_row = service.upsert_profile_from_yaml(episode.profile, session)
        record = service.get_or_create_episode_record(session, episode_id, profile_row.id)
        return service.resume_from_rank(session, record, episode, rank_output, until)


def run_from_outline(outline_path: str, until: str | None = None) -> str:
    """Re-run from a persisted outline.json: script -> critique -> tts ->
    stitch. Also needs the episode's ranked.json (same directory) for the
    selected articles' full text."""
    outline_file = Path(outline_path)
    episode_id = outline_file.parent.name
    episode = load_episode_manifest(outline_file.parent)
    outline_output = OutlineOutput.model_validate_json(outline_file.read_text(encoding="utf-8"))
    rank_output = load_rank_output(outline_file.parent)

    with db.session_scope() as session:
        profile_row = service.upsert_profile_from_yaml(episode.profile, session)
        record = service.get_or_create_episode_record(session, episode_id, profile_row.id)
        return service.resume_from_outline(session, record, episode, outline_output, rank_output, until)


def run_from_script(script_path: str, until: str | None = None) -> str:
    """Re-run from a persisted script.json: critique -> tts -> stitch. Also
    needs the episode's ranked.json and outline.json (same directory) — the
    articles' full text to re-check grounding, and the outline's per-segment
    word_budget/recurring-bit assignment for the critique's budget/line-length
    checks."""
    script_file = Path(script_path)
    episode_id = script_file.parent.name
    episode = load_episode_manifest(script_file.parent)
    script_output = ScriptOutput.model_validate_json(script_file.read_text(encoding="utf-8"))
    rank_output = load_rank_output(script_file.parent)
    outline_output = load_outline_output(script_file.parent)

    with db.session_scope() as session:
        profile_row = service.upsert_profile_from_yaml(episode.profile, session)
        record = service.get_or_create_episode_record(session, episode_id, profile_row.id)
        return service.resume_from_script(session, record, episode, script_output, rank_output, outline_output, until)


def run_from_critique(critique_path: str, until: str | None = None) -> str:
    """Re-run from a persisted critique.json: tts -> stitch."""
    critique_file = Path(critique_path)
    episode_id = critique_file.parent.name
    episode = load_episode_manifest(critique_file.parent)
    critique_output = CritiqueOutput.model_validate_json(critique_file.read_text(encoding="utf-8"))

    with db.session_scope() as session:
        profile_row = service.upsert_profile_from_yaml(episode.profile, session)
        record = service.get_or_create_episode_record(session, episode_id, profile_row.id)
        return service.resume_from_critique(session, record, episode, critique_output, until)


def serve() -> None:
    """`uv run podcast serve` — start the FastAPI app (uvicorn), which serves
    the built React UI (web/dist/, from `npm run build` — see docs/ui.md)
    as static files alongside the API when present, API-only otherwise.
    Builds nothing itself."""
    import uvicorn

    parser = argparse.ArgumentParser(prog="podcast serve", description="Serve the podcast-generator API (+ built UI)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="restart on code changes (development only)")
    args = parser.parse_args(sys.argv[2:])

    uvicorn.run("podcast.api.app:app", host=args.host, port=args.port, reload=args.reload)


def import_profile() -> None:
    """`uv run podcast import-profile <path>` — load a profile YAML and
    overwrite the DB's profiles row (id=1) with it, regardless of what's
    already there. Unlike the API's own startup seeding (which only fills
    an empty table — see api/app.py, service.seed_profile_from_yaml_if_empty),
    this is an explicit overwrite: the intended way to push a hand-edited
    profile file into a DB that already has one."""
    parser = argparse.ArgumentParser(
        prog="podcast import-profile", description="Import a profile YAML into the DB, overwriting any existing profile"
    )
    parser.add_argument("path", help="path to a profile yaml file")
    args = parser.parse_args(sys.argv[2:])

    load_dotenv()
    db.init_db()
    with db.session_scope() as session:
        row = service.import_profile_overwrite(session, args.path)
    print(f"imported profile {row.name!r} from {args.path} into the DB")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        serve()
        return

    if len(sys.argv) > 1 and sys.argv[1] == "import-profile":
        import_profile()
        return

    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    parser = argparse.ArgumentParser(description="Generate a personal podcast episode")
    parser.add_argument("--profile", default=None, help="path to a profile yaml file")
    parser.add_argument("--episode-id", default=None, help="reuse an existing episode id instead of creating one")
    parser.add_argument(
        "--from-articles",
        default=None,
        help="path to a persisted articles.json; skips fetch, re-runs rank -> outline -> script -> critique -> tts -> stitch",
    )
    parser.add_argument(
        "--from-ranked",
        default=None,
        help="path to a persisted ranked.json; skips fetch/rank, re-runs outline -> script -> critique -> tts -> stitch",
    )
    parser.add_argument(
        "--from-outline",
        default=None,
        help="path to a persisted outline.json; skips through outline, re-runs script -> critique -> tts -> stitch",
    )
    parser.add_argument(
        "--from-script",
        default=None,
        help="path to a persisted script.json; skips through script, re-runs critique -> tts -> stitch",
    )
    parser.add_argument(
        "--from-critique",
        default=None,
        help="path to a persisted critique.json; skips through critique, re-runs tts -> stitch",
    )
    parser.add_argument(
        "--until",
        choices=STAGES,
        default=None,
        help="stop the pipeline after this stage runs",
    )
    args = parser.parse_args()

    if args.from_critique:
        run_from_critique(args.from_critique, args.until)
        return

    if args.from_script:
        run_from_script(args.from_script, args.until)
        return

    if args.from_outline:
        run_from_outline(args.from_outline, args.until)
        return

    if args.from_ranked:
        run_from_ranked(args.from_ranked, args.until)
        return

    if args.from_articles:
        run_from_articles(args.from_articles, args.until)
        return

    if not args.profile:
        parser.error(
            "--profile is required unless --from-articles, --from-ranked, --from-outline, "
            "--from-script or --from-critique is given"
        )
    run(args.profile, args.episode_id, args.until)


if __name__ == "__main__":
    main()
