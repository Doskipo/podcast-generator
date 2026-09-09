"""CLI entrypoint: uv run python -m podcast.generate --profile profiles/eudald.yaml"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from podcast.models import Episode, FetchOutput, Profile, ScriptOutput
from podcast.paths import episode_dir
from podcast.stages.fetch import fetch_stage
from podcast.stages.script import script_stage
from podcast.stages.stitch import stitch_stage
from podcast.stages.tts import tts_stage

logger = logging.getLogger(__name__)


def _new_episode_id(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def _load_episode(episode_dir_path: Path) -> Episode:
    manifest_path = episode_dir_path / "episode.json"
    return Episode.model_validate_json(manifest_path.read_text(encoding="utf-8"))


def _run_tts_and_stitch(episode: Episode, script_output: ScriptOutput) -> None:
    tts_output = tts_stage(episode, script_output)
    print(f"episode {episode.episode_id}: synthesized {len(tts_output.lines)} lines")

    stitch_output = stitch_stage(episode, tts_output)
    print(f"episode {episode.episode_id}: wrote {stitch_output.audio_file} ({stitch_output.duration_ms} ms)")


def run(profile_path: str, episode_id: str | None = None) -> str:
    profile = Profile.from_yaml(profile_path)
    episode_id = episode_id or _new_episode_id()

    manifest_path = episode_dir(episode_id) / "episode.json"
    if not manifest_path.exists():
        episode = Episode(episode_id=episode_id, created_at=datetime.now(timezone.utc), profile=profile)
        manifest_path.write_text(episode.model_dump_json(indent=2), encoding="utf-8")
    else:
        episode = _load_episode(episode_dir(episode_id))

    fetch_output = fetch_stage(profile, episode_id)
    print(f"episode {episode_id}: kept {len(fetch_output.articles)} articles from {len(profile.feeds)} feeds")

    script_output = script_stage(episode, fetch_output)
    print(f"episode {episode_id}: wrote script with {len(script_output.script.segments)} segments")

    _run_tts_and_stitch(episode, script_output)
    return episode_id


def run_from_articles(articles_path: str) -> str:
    """Re-run from a persisted articles.json: script -> tts -> stitch."""
    articles_file = Path(articles_path)
    episode_id = articles_file.parent.name
    episode = _load_episode(articles_file.parent)
    fetch_output = FetchOutput.model_validate_json(articles_file.read_text(encoding="utf-8"))

    script_output = script_stage(episode, fetch_output)
    print(f"episode {episode_id}: wrote script with {len(script_output.script.segments)} segments")

    _run_tts_and_stitch(episode, script_output)
    return episode_id


def run_from_script(script_path: str) -> str:
    """Re-run from a persisted script.json: tts -> stitch."""
    script_file = Path(script_path)
    episode_id = script_file.parent.name
    episode = _load_episode(script_file.parent)
    script_output = ScriptOutput.model_validate_json(script_file.read_text(encoding="utf-8"))

    _run_tts_and_stitch(episode, script_output)
    return episode_id


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Generate a personal podcast episode")
    parser.add_argument("--profile", default=None, help="path to a profile yaml file")
    parser.add_argument("--episode-id", default=None, help="reuse an existing episode id instead of creating one")
    parser.add_argument(
        "--from-articles",
        default=None,
        help="path to a persisted articles.json; skips fetch and re-runs script -> tts -> stitch",
    )
    parser.add_argument(
        "--from-script",
        default=None,
        help="path to a persisted script.json; skips fetch and script, re-runs tts -> stitch",
    )
    args = parser.parse_args()

    if args.from_script:
        run_from_script(args.from_script)
        return

    if args.from_articles:
        run_from_articles(args.from_articles)
        return

    if not args.profile:
        parser.error("--profile is required unless --from-articles or --from-script is given")
    run(args.profile, args.episode_id)


if __name__ == "__main__":
    main()
