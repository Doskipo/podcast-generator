"""CLI entrypoint: uv run python -m podcast.generate --profile profiles/eudald.yaml"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from podcast.models import (
    Article,
    CritiqueOutput,
    Episode,
    FetchOutput,
    OutlineOutput,
    Profile,
    RankOutput,
    ScriptOutput,
)
from podcast.paths import episode_dir
from podcast.stages.critique import critique_stage
from podcast.stages.fetch import ensure_interest_queries, fetch_stage
from podcast.stages.outline import outline_stage
from podcast.stages.rank import rank_stage
from podcast.stages.script import script_stage
from podcast.stages.stitch import stitch_stage
from podcast.stages.tts import tts_stage

logger = logging.getLogger(__name__)

# Pipeline order, for --until: stop right after the named stage runs.
STAGES = ["fetch", "rank", "outline", "script", "critique", "tts", "stitch"]


def _new_episode_id(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def _load_episode(episode_dir_path: Path) -> Episode:
    manifest_path = episode_dir_path / "episode.json"
    return Episode.model_validate_json(manifest_path.read_text(encoding="utf-8"))


def _load_rank_output(dir_path: Path) -> RankOutput:
    ranked_path = dir_path / "ranked.json"
    return RankOutput.model_validate_json(ranked_path.read_text(encoding="utf-8"))


def _load_outline_output(dir_path: Path) -> OutlineOutput:
    outline_path = dir_path / "outline.json"
    return OutlineOutput.model_validate_json(outline_path.read_text(encoding="utf-8"))


def _tts_script_output(critique_output: CritiqueOutput) -> ScriptOutput:
    """The critique stage's revised script, wrapped back into a ScriptOutput
    shape so tts_stage's signature doesn't need to know about critique."""
    return ScriptOutput(
        episode_id=critique_output.episode_id,
        generated_at=critique_output.generated_at,
        model=critique_output.model,
        script=critique_output.revised_script,
    )


def _step_rank(episode: Episode, fetch_output: FetchOutput, until: str | None) -> RankOutput | None:
    rank_output = rank_stage(episode, fetch_output)
    print(
        f"episode {episode.episode_id}: ranked {len(rank_output.selected)} "
        f"of budget {rank_output.total_budget} articles ({rank_output.backfilled} backfilled)"
    )
    return None if until == "rank" else rank_output


def _step_outline(episode: Episode, rank_output: RankOutput, until: str | None) -> OutlineOutput | None:
    outline_output = outline_stage(episode, rank_output)
    print(f"episode {episode.episode_id}: outlined {len(outline_output.outline.stories)} stories")
    return None if until == "outline" else outline_output


def _step_script(
    episode: Episode, outline_output: OutlineOutput, articles: list[Article], until: str | None
) -> ScriptOutput | None:
    script_output = script_stage(episode, outline_output, articles)
    print(f"episode {episode.episode_id}: wrote script with {len(script_output.script.segments)} segments")
    return None if until == "script" else script_output


def _step_critique(
    episode: Episode,
    script_output: ScriptOutput,
    articles: list[Article],
    outline_output: OutlineOutput,
    until: str | None,
) -> CritiqueOutput | None:
    critique_output = critique_stage(episode, script_output, articles, outline_output)
    print(
        f"episode {episode.episode_id}: critique flagged {len(critique_output.critique.flags)} line(s), "
        f"{critique_output.total_words} words total, "
        f"{len(critique_output.over_budget_segments)} segment(s) over budget"
    )
    return None if until == "critique" else critique_output


def _run_outline_through_critique(
    episode: Episode, rank_output: RankOutput, until: str | None
) -> CritiqueOutput | None:
    """outline -> script -> critique, stopping early if `until` names one of
    those stages. Shared by every entrypoint that starts at or before
    outline."""
    outline_output = _step_outline(episode, rank_output, until)
    if outline_output is None:
        return None

    script_output = _step_script(episode, outline_output, rank_output.selected, until)
    if script_output is None:
        return None

    return _step_critique(episode, script_output, rank_output.selected, outline_output, until)


def _run_tts_and_stitch(episode: Episode, script_output: ScriptOutput, until: str | None = None) -> None:
    tts_output = tts_stage(episode, script_output)
    print(f"episode {episode.episode_id}: synthesized {len(tts_output.lines)} lines")
    if until == "tts":
        return

    stitch_output = stitch_stage(episode, tts_output)
    print(f"episode {episode.episode_id}: wrote {stitch_output.audio_file} ({stitch_output.duration_ms} ms)")


def run(profile_path: str, episode_id: str | None = None, until: str | None = None) -> str:
    profile = Profile.from_yaml(profile_path)
    if ensure_interest_queries(profile, profile_path):
        print(f"cached generated search queries into {profile_path}")
    episode_id = episode_id or _new_episode_id()

    manifest_path = episode_dir(episode_id) / "episode.json"
    if not manifest_path.exists():
        episode = Episode(episode_id=episode_id, created_at=datetime.now(timezone.utc), profile=profile)
        manifest_path.write_text(episode.model_dump_json(indent=2), encoding="utf-8")
    else:
        episode = _load_episode(episode_dir(episode_id))

    fetch_output = fetch_stage(profile, episode_id)
    print(
        f"episode {episode_id}: kept {len(fetch_output.articles)} candidate articles "
        f"from {fetch_output.feeds_count} feeds"
    )
    if until == "fetch":
        return episode_id

    rank_output = _step_rank(episode, fetch_output, until)
    if rank_output is None:
        return episode_id

    critique_output = _run_outline_through_critique(episode, rank_output, until)
    if critique_output is None:
        return episode_id

    _run_tts_and_stitch(episode, _tts_script_output(critique_output), until)
    return episode_id


def run_from_articles(articles_path: str, until: str | None = None) -> str:
    """Re-run from a persisted articles.json: rank -> outline -> script ->
    critique -> tts -> stitch."""
    articles_file = Path(articles_path)
    episode_id = articles_file.parent.name
    episode = _load_episode(articles_file.parent)
    fetch_output = FetchOutput.model_validate_json(articles_file.read_text(encoding="utf-8"))

    rank_output = _step_rank(episode, fetch_output, until)
    if rank_output is None:
        return episode_id

    critique_output = _run_outline_through_critique(episode, rank_output, until)
    if critique_output is None:
        return episode_id

    _run_tts_and_stitch(episode, _tts_script_output(critique_output), until)
    return episode_id


def run_from_ranked(ranked_path: str, until: str | None = None) -> str:
    """Re-run from a persisted ranked.json: outline -> script -> critique ->
    tts -> stitch."""
    ranked_file = Path(ranked_path)
    episode_id = ranked_file.parent.name
    episode = _load_episode(ranked_file.parent)
    rank_output = RankOutput.model_validate_json(ranked_file.read_text(encoding="utf-8"))

    critique_output = _run_outline_through_critique(episode, rank_output, until)
    if critique_output is None:
        return episode_id

    _run_tts_and_stitch(episode, _tts_script_output(critique_output), until)
    return episode_id


def run_from_outline(outline_path: str, until: str | None = None) -> str:
    """Re-run from a persisted outline.json: script -> critique -> tts ->
    stitch. Also needs the episode's ranked.json (same directory) for the
    selected articles' full text."""
    outline_file = Path(outline_path)
    episode_id = outline_file.parent.name
    episode = _load_episode(outline_file.parent)
    outline_output = OutlineOutput.model_validate_json(outline_file.read_text(encoding="utf-8"))
    rank_output = _load_rank_output(outline_file.parent)

    script_output = _step_script(episode, outline_output, rank_output.selected, until)
    if script_output is None:
        return episode_id

    critique_output = _step_critique(episode, script_output, rank_output.selected, outline_output, until)
    if critique_output is None:
        return episode_id

    _run_tts_and_stitch(episode, _tts_script_output(critique_output), until)
    return episode_id


def run_from_script(script_path: str, until: str | None = None) -> str:
    """Re-run from a persisted script.json: critique -> tts -> stitch. Also
    needs the episode's ranked.json and outline.json (same directory) — the
    articles' full text to re-check grounding, and the outline's per-segment
    word_budget/recurring-bit assignment for the critique's budget/line-length
    checks."""
    script_file = Path(script_path)
    episode_id = script_file.parent.name
    episode = _load_episode(script_file.parent)
    script_output = ScriptOutput.model_validate_json(script_file.read_text(encoding="utf-8"))
    rank_output = _load_rank_output(script_file.parent)
    outline_output = _load_outline_output(script_file.parent)

    critique_output = _step_critique(episode, script_output, rank_output.selected, outline_output, until)
    if critique_output is None:
        return episode_id

    _run_tts_and_stitch(episode, _tts_script_output(critique_output), until)
    return episode_id


def run_from_critique(critique_path: str, until: str | None = None) -> str:
    """Re-run from a persisted critique.json: tts -> stitch."""
    critique_file = Path(critique_path)
    episode_id = critique_file.parent.name
    episode = _load_episode(critique_file.parent)
    critique_output = CritiqueOutput.model_validate_json(critique_file.read_text(encoding="utf-8"))

    _run_tts_and_stitch(episode, _tts_script_output(critique_output), until)
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
