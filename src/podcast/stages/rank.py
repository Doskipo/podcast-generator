"""Rank stage: score fetched candidates for relevance, select per-interest
top-k within a duration-derived budget, order globally, extract full text
only for what was selected — backfilling from the next-best-scored candidate
whenever a selected one turns out unextractable.

Typed input: Episode (for Profile) + FetchOutput. Typed output: RankOutput,
persisted as data/episodes/<episode_id>/ranked.json. See docs/decisions.md
("Rank stage") for why selection is per-interest and why extraction moved
here from the fetch stage, and ("Rank stage backfill + redirects") for the
backfill/redirect-resolution/description-rubric changes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
import trafilatura
from openai import OpenAI

from podcast.env import require_env
from podcast.models import (
    Article,
    ArticleScore,
    Episode,
    FetchOutput,
    Interest,
    RankedArticle,
    RankOutput,
    ScoreBatch,
)
from podcast.paths import episode_dir

logger = logging.getLogger(__name__)

# Below this many extracted characters we treat the extraction as failed
# rather than risk grounding the script in a near-empty article. Same
# threshold fetch.py used to apply before extraction moved here.
MIN_EXTRACTED_CHARS = 200

# Articles per scoring call. Flattened across all interests (not chunked per
# interest) so small interests share a call instead of each paying for its
# own — fewer, cheaper calls for the same total candidate count.
BATCH_SIZE = 20

# Roughly one story per 1.5 minutes of episode — the total selection budget.
MINUTES_PER_STORY = 1.5

# Bucket key for candidates from top-level profile.feeds extras, which have
# no Interest of their own to weight/budget by.
EXTRA_BUCKET = "_extra"

# Resolving redirects (Bing's apiclick.aspx wrapper, etc.) is a separate,
# smaller request than a feed download, but the same idea as fetch.py's
# _download_feed: a browser-like User-Agent, since some publishers — and
# Bing's own wrapper — reject non-browser clients on the wrapped link.
REDIRECT_TIMEOUT_SECONDS = 15
REDIRECT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Aggregator domains whose pages consistently yield nothing trafilatura can
# parse (seen in practice: MSN wraps the real article in JS-rendered chrome).
# Treated as an immediate extraction failure — no point spending a trafilatura
# call on it — so the candidate backfills right away.
UNEXTRACTABLE_DOMAINS = {"msn.com"}


def _render_candidate(article: Article, interest_topic: str | None, interest_description: str | None) -> str:
    summary = article.summary or "(no summary)"
    topic_label = interest_topic or "general"
    if interest_description:
        topic_label = f"{topic_label} — {interest_description}"
    return f"[{article.source_id}] (interest: {topic_label}) {article.title} — {summary}"


def _score_batch(
    client: OpenAI, model: str, model_batch: list[tuple[Article, str | None, str | None]]
) -> ScoreBatch:
    """Boundary around the OpenAI call — the seam tests monkeypatch."""
    known_ids = ", ".join(a.source_id for a, _topic, _description in model_batch)
    candidates_block = "\n".join(_render_candidate(a, topic, description) for a, topic, description in model_batch)

    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You score how relevant each news/paper candidate is to the interest "
                    "it was found for. Use this rubric:\n"
                    "  1.0 = squarely about the topic as described\n"
                    "  0.7 = clearly related to the topic\n"
                    "  0.4 = tangential to the topic\n"
                    "  0.0 = unrelated to the topic\n"
                    "When an interest has a description (after the em dash), judge relevance "
                    "against that description, not just the topic name. Your one-line reason "
                    "must name the specific concept in the candidate's title that justifies "
                    "the score.\n"
                    f"Score every one of these ids exactly once: {known_ids}"
                ),
            },
            {"role": "user", "content": candidates_block},
        ],
        response_format=ScoreBatch,
    )
    return completion.choices[0].message.parsed


def _chunks(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _score_articles(
    client: OpenAI, model: str, articles: list[Article], descriptions: dict[str, str | None]
) -> dict[str, ArticleScore]:
    """Score every article, chunked into batches of BATCH_SIZE. Any id the
    model doesn't return a score for gets a defensive 0.0/"not scored" entry
    rather than crashing the stage."""
    known_ids = {a.source_id for a in articles}
    scores: dict[str, ArticleScore] = {}

    for batch in _chunks(articles, BATCH_SIZE):
        model_batch = [(a, a.interest, descriptions.get(a.interest)) for a in batch]
        result = _score_batch(client, model, model_batch)
        for score in result.scores:
            if score.source_id not in known_ids:
                logger.warning("scorer returned unknown source_id %r, ignoring", score.source_id)
                continue
            scores[score.source_id] = score

    for article in articles:
        if article.source_id not in scores:
            logger.warning("scorer never returned a score for %s, defaulting to 0.0", article.source_id)
            scores[article.source_id] = ArticleScore(source_id=article.source_id, score=0.0, reason="not scored")

    return scores


def _total_budget(duration_minutes: int) -> int:
    return max(1, round(duration_minutes / MINUTES_PER_STORY))


def _bucket_weights(interests: list[Interest], has_extra: bool) -> dict[str, float]:
    weights = {interest.topic: interest.weight for interest in interests}
    if has_extra:
        # No Interest to weight top-level profile.feeds extras by — use the
        # mean of the real interests' weights (0.5 if there are none at all).
        mean_weight = (sum(weights.values()) / len(weights)) if weights else 0.5
        weights[EXTRA_BUCKET] = mean_weight
    return weights


def _per_bucket_budget(weights: dict[str, float], total_budget: int) -> dict[str, int]:
    """Proportional-to-weight allocation summing to total_budget, via the
    largest-remainder method (floor each share, hand out the leftover budget
    to the largest fractional parts)."""
    total_weight = sum(weights.values()) or 1.0
    raw = {topic: total_budget * weight / total_weight for topic, weight in weights.items()}
    floors = {topic: int(share) for topic, share in raw.items()}
    remainder = total_budget - sum(floors.values())

    by_fraction = sorted(raw.items(), key=lambda kv: kv[1] - floors[kv[0]], reverse=True)
    for topic, _ in by_fraction[:remainder]:
        floors[topic] += 1
    return floors


def _resolve_and_download(url: str) -> tuple[str | None, str | None]:
    """Follow redirects (e.g. Bing's apiclick.aspx wrapper) to the final
    landing page in one request, returning (final_url, html). Both None if
    the request fails outright (network error, non-2xx) — the caller treats
    that as an extraction failure and backfills."""
    try:
        response = httpx.get(
            url,
            timeout=REDIRECT_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": REDIRECT_USER_AGENT},
        )
        response.raise_for_status()
    except httpx.HTTPError:
        logger.warning("failed to resolve/download %s", url, exc_info=True)
        return None, None
    return str(response.url), response.text


def _is_unextractable(final_url: str) -> bool:
    host = urlsplit(final_url).netloc.lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in UNEXTRACTABLE_DOMAINS)


def _extract_text(final_url: str, html: str) -> str | None:
    if _is_unextractable(final_url):
        return None
    text = trafilatura.extract(html)
    if not text or len(text) < MIN_EXTRACTED_CHARS:
        return None
    return text


def _select_bucket(
    bucket_articles: list[Article], scores: dict[str, ArticleScore], k: int
) -> tuple[list[RankedArticle], list[Article], int]:
    """Walk this interest's candidates in score order, extracting until k are
    selected or candidates run out. A candidate that fails to resolve, fails
    to extract, or resolves to an unextractable domain is skipped in favor of
    the next-best-scored candidate (backfill) rather than shrinking the
    interest's selection. Returns (scored_entries, selected_articles,
    backfilled_count)."""
    ranked = sorted(bucket_articles, key=lambda a: scores[a.source_id].score, reverse=True)

    scored_entries: list[RankedArticle] = []
    selected_articles: list[Article] = []
    backfilled = 0

    for position, article in enumerate(ranked):
        article_score = scores[article.source_id]
        extracted: Article | None = None

        if len(selected_articles) < k:
            final_url, html = _resolve_and_download(str(article.url))
            if final_url is not None:
                article = article.model_copy(update={"final_url": final_url})
                text = _extract_text(final_url, html)
                if text is not None:
                    extracted = article.model_copy(update={"text": text})
            if extracted is None:
                logger.warning(
                    "candidate %s (interest %r) failed extraction, backfilling from next-best",
                    article.source_id,
                    article.interest,
                )
            elif position >= k:
                backfilled += 1

        is_selected = extracted is not None
        scored_entries.append(
            RankedArticle(
                article=article,
                interest=article.interest,
                score=article_score.score,
                reason=article_score.reason,
                selected=is_selected,
            )
        )
        if is_selected:
            selected_articles.append(extracted)

    return scored_entries, selected_articles, backfilled


def rank_stage(episode: Episode, fetch_output: FetchOutput, client: OpenAI | None = None) -> RankOutput:
    if client is None:
        client = OpenAI(api_key=require_env("OPENAI_API_KEY"))

    profile = episode.profile
    articles = fetch_output.articles
    total_budget = _total_budget(profile.podcast.duration_minutes)

    descriptions = {interest.topic: interest.description for interest in profile.interests}
    scores = _score_articles(client, profile.llm.model, articles, descriptions)

    has_extra = any(a.interest is None for a in articles)
    weights = _bucket_weights(profile.interests, has_extra)
    budgets = _per_bucket_budget(weights, total_budget)

    by_bucket: dict[str, list[Article]] = {}
    for article in articles:
        bucket = article.interest or EXTRA_BUCKET
        by_bucket.setdefault(bucket, []).append(article)

    scored: list[RankedArticle] = []
    selected: list[Article] = []
    total_backfilled = 0

    for bucket, bucket_articles in by_bucket.items():
        k = budgets.get(bucket, 0)
        bucket_scored, bucket_selected, backfilled = _select_bucket(bucket_articles, scores, k)
        scored.extend(bucket_scored)
        selected.extend(bucket_selected)
        total_backfilled += backfilled

    if total_backfilled:
        logger.info("backfilled %d candidate(s) after extraction failures", total_backfilled)

    selected.sort(key=lambda a: scores[a.source_id].score * weights.get(a.interest or EXTRA_BUCKET, 0.0), reverse=True)

    output = RankOutput(
        episode_id=episode.episode_id,
        ranked_at=datetime.now(timezone.utc),
        model=profile.llm.model,
        total_budget=total_budget,
        backfilled=total_backfilled,
        scored=scored,
        selected=selected,
    )

    out_path = episode_dir(episode.episode_id) / "ranked.json"
    out_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return output
