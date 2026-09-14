"""Fetch stage: pull RSS feeds, dedupe, keep a recent window — metadata only.

Typed input: Profile (+ episode_id). Typed output: FetchOutput, persisted as
data/episodes/<episode_id>/articles.json. Full-text extraction happens later,
in the rank stage, only for the candidates selected there — see
docs/decisions.md ("Rank stage") for why extraction moved out of this stage.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx
from openai import OpenAI

from podcast.env import require_env
from podcast.models import (
    DEFAULT_CURATED_WINDOW_HOURS,
    Article,
    FetchOutput,
    InterestSuggestion,
    Profile,
    QueryList,
)
from podcast.paths import episode_dir

logger = logging.getLogger(__name__)

# feedparser.parse(url) does its own fetch via urllib, which on Windows uses
# the system cert store instead of certifi and fails TLS verification against
# arXiv's chain. Downloading with httpx (certifi-backed, cross-platform) and
# handing feedparser the raw text instead of a URL sidesteps that entirely.
FEED_TIMEOUT_SECONDS = 15
FEED_USER_AGENT = "podcast-generator/0.1 (personal podcast pipeline; https://github.com/Doskipo/podcast-generator)"

# An interest with no curated feeds gets discovered via a Bing News RSS search
# instead. Chosen over Google News RSS by a throwaway comparison across four
# topics (calisthenics, League of Legends esports, Olivia Dean and Sienna
# Spiro new music, mathematics of machine learning, logged in
# docs/decisions.md): Bing's links resolve to the real publisher page and
# extract cleanly with trafilatura (25/27 sampled articles), while Google
# News's redirect links resolve off news.google.com but land somewhere
# trafilatura can't extract real article text from (0/40 sampled).
SOURCE_CURATED = "curated"
SOURCE_SEARCH = "bing_news_search"

# How many event-shaped queries to generate for an interest with no curated
# feeds and no manually-set queries. See ensure_interest_queries.
NUM_GENERATED_QUERIES = 3


def _build_search_feed_url(query: str) -> str:
    return "https://www.bing.com/news/search?" + urlencode({"q": query, "format": "RSS"})


def _feed_plan(profile: Profile) -> list[tuple[str, str, int, str | None]]:
    """Return (feed_url, source, window_hours, interest_topic) tuples: each
    interest's curated feeds if it has any (one tuple per feed), else one
    generated search feed per query for that interest; plus every top-level
    profile.feeds entry as an extra curated feed with no interest (None).
    Dedup across all of it happens later, in fetch_stage, the same way
    regardless of how a feed_url was produced."""
    plan: list[tuple[str, str, int, str | None]] = []
    for interest in profile.interests:
        window_hours = interest.effective_window_hours
        if interest.is_curated:
            plan.extend((str(feed), SOURCE_CURATED, window_hours, interest.topic) for feed in interest.feeds)
        else:
            # Queries should already be cached by ensure_interest_queries; falling
            # back to the bare topic as a single query keeps this usable even if
            # that step was skipped (e.g. calling fetch_stage directly in tests).
            queries = interest.queries or [interest.topic]
            plan.extend((_build_search_feed_url(q), SOURCE_SEARCH, window_hours, interest.topic) for q in queries)
    plan.extend((str(feed), SOURCE_CURATED, DEFAULT_CURATED_WINDOW_HOURS, None) for feed in profile.feeds)
    return plan


def _generate_queries(client: OpenAI, model: str, topic: str) -> list[str]:
    """Boundary around the OpenAI call — the seam tests monkeypatch."""
    today = datetime.now(timezone.utc).date().isoformat()
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You write short, event-shaped news-search queries for a topic — "
                    "phrases likely to surface actual recent news or events, not generic "
                    "explainers. Example for the topic 'calisthenics': 'calisthenics "
                    "competition', 'calisthenics world record', 'street workout news'. "
                    "Never hardcode a specific year (you don't reliably know the current "
                    "one) — write queries that work regardless of when they're run."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Today's date: {today}\nTopic: {topic}\n"
                    f"Give exactly {NUM_GENERATED_QUERIES} short search queries."
                ),
            },
        ],
        response_format=QueryList,
    )
    return completion.choices[0].message.parsed.queries[:NUM_GENERATED_QUERIES]


def ensure_interest_queries(profile: Profile, profile_path: str | Path, client: OpenAI | None = None) -> bool:
    """Fill in `queries` for every search-discovered interest (no curated
    feeds) that doesn't have them yet, with one cheap LLM call per such
    interest, then persist the result back into the profile's YAML file so
    later runs reuse the cache instead of calling the model again — one call
    per profile, not per run. Returns True if the file was rewritten."""
    to_generate = [interest for interest in profile.interests if not interest.is_curated and not interest.queries]
    if not to_generate:
        return False

    if client is None:
        client = OpenAI(api_key=require_env("OPENAI_API_KEY"))

    for interest in to_generate:
        interest.queries = _generate_queries(client, profile.llm.model, interest.topic)
        logger.info("generated queries for interest %r: %s", interest.topic, interest.queries)

    profile.to_yaml(profile_path)
    return True


def _generate_suggestion(client: OpenAI, model: str, topic: str, description: str | None) -> InterestSuggestion:
    """Boundary around the OpenAI call — the seam tests monkeypatch. Same
    call shape as _generate_queries, extended to also draft a one-line
    description — one call gets both, at no extra cost over queries alone."""
    today = datetime.now(timezone.utc).date().isoformat()
    user_content = f"Today's date: {today}\nTopic: {topic}\n"
    if description:
        user_content += f"User's own rough description (refine, don't ignore): {description}\n"
    user_content += f"Give a one-line description and exactly {NUM_GENERATED_QUERIES} short search queries."

    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You help a user define one topic of interest for a personalized news "
                    "podcast. Given a topic (and optionally the user's own rough description), "
                    "produce two things: "
                    "(1) description: a precise one-line description of what this interest "
                    "actually means — its scope and angle, so relevance can be judged against "
                    "it later. Example, for the topic 'interpretability': 'mechanistic "
                    "interpretability of neural networks: circuits, features, probes, sparse "
                    "autoencoders'. "
                    "(2) queries: short, event-shaped news-search queries — phrases likely to "
                    "surface actual recent news or events, not generic explainers. Example for "
                    "'calisthenics': 'calisthenics competition', 'calisthenics world record', "
                    "'street workout news'. Never hardcode a specific year (you don't reliably "
                    "know the current one) — write queries that work regardless of when they're "
                    "run."
                ),
            },
            {"role": "user", "content": user_content},
        ],
        response_format=InterestSuggestion,
    )
    parsed = completion.choices[0].message.parsed
    return InterestSuggestion(description=parsed.description, queries=parsed.queries[:NUM_GENERATED_QUERIES])


def suggest_interest(
    topic: str, description: str | None, model: str, client: OpenAI | None = None
) -> InterestSuggestion:
    """One LLM call: a one-line description draft (the user can accept or
    edit) plus event-shaped search queries — backs the settings UI's
    "suggest" button (POST /interests/suggest). User-triggered and opt-in;
    not wired into ensure_interest_queries, which only ever fills `queries`
    automatically at profile-load time."""
    if client is None:
        client = OpenAI(api_key=require_env("OPENAI_API_KEY"))
    return _generate_suggestion(client, model, topic, description)


# Query params stripped before dedup — known tracking noise only. Everything
# else in the query string is kept: a redirect wrapper like Bing's
# `apiclick.aspx?...&url=<real target>&...` encodes the only thing that makes
# the link distinct entirely in its query string, so blindly dropping the
# whole query (as this used to) collapses every wrapped link from every feed
# into one fake duplicate — silently discarding real candidates.
_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}


def _normalize_url(url: str) -> str:
    """Strip known tracking params and a trailing slash so syndication noise
    doesn't defeat dedupe, without discarding a redirect wrapper's real target."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    kept_params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in _TRACKING_PARAMS]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(kept_params), ""))


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def source_id_for_url(url: str) -> str:
    """Stable short id for an article/page url — first 8 hex chars of
    sha1(url). Public (not `_`-prefixed): podcast.evergreen reuses this
    exact scheme for Wikipedia primer urls rather than a second copy — same
    "make it public when a second module needs it" precedent as
    script.py:flatten_lines (see docs/decisions.md, "Script rebuild")."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]


def _entry_published_at(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        value = getattr(entry, key, None)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc)
    return None


def _download_feed(url: str) -> str:
    """Fetch a feed's raw body over HTTPS via httpx. Raises on network errors
    or non-2xx responses; the caller is responsible for catching."""
    response = httpx.get(
        url,
        timeout=FEED_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": FEED_USER_AGENT},
    )
    response.raise_for_status()
    return response.text


def _candidates_for_feed(
    feed_url: str, now: datetime, window_hours: int, max_entries: int, source: str, interest_topic: str | None
) -> list[dict]:
    """Parse one feed and return recent, capped candidates (newest first). Never raises."""
    cutoff = now - timedelta(hours=window_hours)
    try:
        content = _download_feed(feed_url)
        parsed = feedparser.parse(content)
    except Exception:
        logger.warning("skipping feed %s: failed to download/parse", feed_url, exc_info=True)
        return []

    if parsed.bozo and not parsed.entries:
        logger.warning("skipping feed %s: %s", feed_url, getattr(parsed, "bozo_exception", "malformed feed"))
        return []

    dated_entries: list[tuple[datetime, object]] = []
    for entry in parsed.entries:
        published_at = _entry_published_at(entry)
        if published_at is None or published_at < cutoff:
            continue
        dated_entries.append((published_at, entry))

    dated_entries.sort(key=lambda pair: pair[0], reverse=True)
    dated_entries = dated_entries[:max_entries]

    candidates = []
    for published_at, entry in dated_entries:
        url = entry.get("link")
        title = entry.get("title")
        if not url or not title:
            continue
        candidates.append(
            {
                "url": url,
                "title": title,
                "feed_url": feed_url,
                "published_at": published_at,
                "summary": entry.get("summary"),
                "source": source,
                "interest": interest_topic,
            }
        )
    return candidates


def fetch_stage(profile: Profile, episode_id: str) -> FetchOutput:
    now = datetime.now(timezone.utc)

    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    candidates: list[dict] = []

    feed_plan = _feed_plan(profile)
    for feed_url, source, window_hours, interest_topic in feed_plan:
        for candidate in _candidates_for_feed(
            feed_url, now, window_hours, profile.fetch.max_entries_per_feed, source, interest_topic
        ):
            norm_url = _normalize_url(candidate["url"])
            norm_title = _normalize_title(candidate["title"])
            if norm_url in seen_urls or norm_title in seen_titles:
                continue
            seen_urls.add(norm_url)
            seen_titles.add(norm_title)
            candidates.append(candidate)

    # No full-text extraction here — that's the expensive/networked step, and
    # it now runs in the rank stage, only for the candidates selected there.
    articles = [
        Article(
            source_id=source_id_for_url(c["url"]),
            url=c["url"],
            title=c["title"],
            feed_url=c["feed_url"],
            published_at=c["published_at"],
            fetched_at=now,
            summary=c["summary"],
            source=c["source"],
            interest=c["interest"],
        )
        for c in candidates
    ]

    output = FetchOutput(
        episode_id=episode_id,
        fetched_at=now,
        feeds_count=len(feed_plan),
        articles=articles,
    )

    out_path = episode_dir(episode_id) / "articles.json"
    out_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return output
