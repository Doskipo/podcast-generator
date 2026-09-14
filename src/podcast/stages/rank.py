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
    TokenUsage,
)
from podcast.paths import episode_dir

logger = logging.getLogger(__name__)

# Below this many extracted characters we treat the extraction as failed
# rather than risk grounding the script in a near-empty article. Same
# threshold fetch.py used to apply before extraction moved here.
MIN_EXTRACTED_CHARS = 200

# Articles per scoring call, chunked *within* one interest — a call never
# mixes interests (see docs/decisions.md, "per-interest batching + echo
# validation": mixing them let the model lose track of which interest it was
# scoring partway through a batch and repeat a previous item's reasoning).
BATCH_SIZE = 20

# Initial score attempt + one retry for any item whose echoed `interest`
# doesn't match what it was actually scored against.
MAX_SCORE_ATTEMPTS = 2

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


def _render_candidate(article: Article) -> str:
    summary = article.summary or "(no summary)"
    return f"[{article.source_id}] {article.title} — {summary}"


def _score_batch(
    client: OpenAI, model: str, interest_topic: str | None, interest_description: str | None, batch: list[Article]
) -> tuple[ScoreBatch, TokenUsage]:
    """Boundary around the OpenAI call — the seam tests monkeypatch. Every
    article in `batch` belongs to the same interest; a call never mixes
    interests (see docs/decisions.md)."""
    label = interest_topic or "general"
    description_line = f"Description: {interest_description}\n" if interest_description else ""
    known_ids = ", ".join(a.source_id for a in batch)
    candidates_block = "\n".join(_render_candidate(a) for a in batch)

    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You score how relevant each candidate below is to a single interest:\n"
                    f"Interest: {label}\n"
                    f"{description_line}"
                    "Use this rubric:\n"
                    "  1.0 = squarely about the topic as described\n"
                    "  0.7 = clearly related to the topic\n"
                    "  0.4 = tangential to the topic\n"
                    "  0.0 = unrelated to the topic\n"
                    "Be strict: most candidates are not a great fit for the interest they were "
                    "found under, so most scores should land below 0.7 — reserve 0.7 and above "
                    "for candidates you're genuinely confident belong.\n"
                    "Your one-line reason must name a specific concept from the candidate's "
                    "title or summary. Only return a score at all if that concept actually "
                    "appears in the title or summary you were given — never credit a candidate "
                    "for a connection that isn't stated in the text you can see.\n"
                    f'Echo back exactly "{label}" in every result\'s `interest` field — this is '
                    "a consistency check, not a judgment call.\n"
                    f"Score every one of these ids exactly once: {known_ids}"
                ),
            },
            {"role": "user", "content": candidates_block},
        ],
        response_format=ScoreBatch,
    )
    usage = TokenUsage(
        model=model, prompt_tokens=completion.usage.prompt_tokens, completion_tokens=completion.usage.completion_tokens
    )
    return completion.choices[0].message.parsed, usage


def _chunks(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _score_batch_with_validation(
    client: OpenAI, model: str, interest_topic: str | None, description: str | None, batch: list[Article]
) -> tuple[dict[str, ArticleScore], list[TokenUsage]]:
    """Score `batch` (all one interest), rejecting and re-scoring any item
    whose echoed `interest` doesn't match what it was actually scored
    against. An item that still mismatches after MAX_SCORE_ATTEMPTS gets a
    defensive fallback rather than a silently wrong score. Returns every
    call's token usage alongside the scores — a rescore attempt is a real,
    billed API call and must be counted too."""
    expected_label = interest_topic or "general"
    known_ids = {a.source_id for a in batch}
    results: dict[str, ArticleScore] = {}
    usage: list[TokenUsage] = []
    remaining = batch

    for _attempt in range(MAX_SCORE_ATTEMPTS):
        if not remaining:
            break
        response, call_usage = _score_batch(client, model, interest_topic, description, remaining)
        usage.append(call_usage)
        mismatched_ids: set[str] = set()
        for score in response.scores:
            if score.source_id not in known_ids:
                logger.warning("scorer returned unknown source_id %r, ignoring", score.source_id)
                continue
            if score.interest != expected_label:
                logger.warning(
                    "candidate %s echoed interest %r, expected %r — rejecting and re-scoring",
                    score.source_id,
                    score.interest,
                    expected_label,
                )
                mismatched_ids.add(score.source_id)
                continue
            results[score.source_id] = score
        remaining = [a for a in remaining if a.source_id in mismatched_ids]

    for article in remaining:
        logger.warning(
            "candidate %s still had a mismatched interest echo after retrying, defaulting to 0.0",
            article.source_id,
        )
        results[article.source_id] = ArticleScore(
            source_id=article.source_id, interest=expected_label, score=0.0, reason="interest echo mismatch"
        )

    return results, usage


def _score_articles(
    client: OpenAI, model: str, articles: list[Article], descriptions: dict[str, str | None]
) -> tuple[dict[str, ArticleScore], list[TokenUsage]]:
    """Score every article, batched per interest (a batch never mixes
    interests) and chunked to BATCH_SIZE within each interest. Any id the
    model doesn't return a score for at all — after echo-validated retries —
    gets a defensive 0.0/"not scored" entry rather than crashing the stage."""
    scores: dict[str, ArticleScore] = {}
    usage: list[TokenUsage] = []

    by_interest: dict[str | None, list[Article]] = {}
    for article in articles:
        by_interest.setdefault(article.interest, []).append(article)

    for interest_topic, interest_articles in by_interest.items():
        description = descriptions.get(interest_topic)
        for batch in _chunks(interest_articles, BATCH_SIZE):
            batch_scores, batch_usage = _score_batch_with_validation(client, model, interest_topic, description, batch)
            scores.update(batch_scores)
            usage.extend(batch_usage)

    for article in articles:
        if article.source_id not in scores:
            logger.warning("scorer never returned a score for %s, defaulting to 0.0", article.source_id)
            scores[article.source_id] = ArticleScore(
                source_id=article.source_id, interest=article.interest or "general", score=0.0, reason="not scored"
            )

    return scores, usage


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
    except httpx.HTTPError as exc:
        # One-line summary at WARNING (this is common — 403s, timeouts,
        # dead links — not exceptional); the full traceback still goes to
        # DEBUG for when it's actually needed.
        error_response = getattr(exc, "response", None)
        status = error_response.status_code if error_response is not None else None
        final_url = str(error_response.url) if error_response is not None else url
        logger.warning("failed to resolve/download %s: status=%s final_url=%s", url, status, final_url)
        logger.debug("resolve/download failure detail for %s", url, exc_info=True)
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
    scores, usage = _score_articles(client, profile.llm.model, articles, descriptions)

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
        usage=usage,
    )

    out_path = episode_dir(episode.episode_id) / "ranked.json"
    out_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return output
