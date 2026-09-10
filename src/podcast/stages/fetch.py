"""Fetch stage: pull RSS feeds, extract full text, dedupe, keep a recent window.

Typed input: Profile (+ episode_id). Typed output: FetchOutput, persisted as
data/episodes/<episode_id>/articles.json.
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
import trafilatura
from openai import OpenAI

from podcast.env import require_env
from podcast.models import (
    DEFAULT_CURATED_WINDOW_HOURS,
    Article,
    FetchOutput,
    Profile,
    QueryList,
)
from podcast.paths import episode_dir

logger = logging.getLogger(__name__)

# Below this many extracted characters we treat the extraction as failed rather
# than risk grounding the script in a near-empty article.
MIN_EXTRACTED_CHARS = 200

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


def _feed_plan(profile: Profile) -> list[tuple[str, str, int]]:
    """Return (feed_url, source, window_hours) triples: each interest's curated
    feeds if it has any (one triple per feed), else one generated search feed
    per query for that interest; plus every top-level profile.feeds entry as
    an extra curated feed. Dedup across all of it happens later, in
    fetch_stage, the same way regardless of how a feed_url was produced."""
    plan: list[tuple[str, str, int]] = []
    for interest in profile.interests:
        window_hours = interest.effective_window_hours
        if interest.is_curated:
            plan.extend((str(feed), SOURCE_CURATED, window_hours) for feed in interest.feeds)
        else:
            # Queries should already be cached by ensure_interest_queries; falling
            # back to the bare topic as a single query keeps this usable even if
            # that step was skipped (e.g. calling fetch_stage directly in tests).
            queries = interest.queries or [interest.topic]
            plan.extend((_build_search_feed_url(q), SOURCE_SEARCH, window_hours) for q in queries)
    plan.extend((str(feed), SOURCE_CURATED, DEFAULT_CURATED_WINDOW_HOURS) for feed in profile.feeds)
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


def _source_id(url: str) -> str:
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


def _candidates_for_feed(feed_url: str, now: datetime, window_hours: int, max_entries: int, source: str) -> list[dict]:
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
            }
        )
    return candidates


def _extract_article(candidate: dict) -> Article | None:
    downloaded = trafilatura.fetch_url(candidate["url"])
    text = trafilatura.extract(downloaded) if downloaded else None
    if not text or len(text) < MIN_EXTRACTED_CHARS:
        logger.warning("skipping article %s: extraction failed or too short", candidate["url"])
        return None

    return Article(
        source_id=_source_id(candidate["url"]),
        url=candidate["url"],
        title=candidate["title"],
        feed_url=candidate["feed_url"],
        published_at=candidate["published_at"],
        fetched_at=datetime.now(timezone.utc),
        summary=candidate["summary"],
        text=text,
        source=candidate["source"],
    )


def fetch_stage(profile: Profile, episode_id: str) -> FetchOutput:
    now = datetime.now(timezone.utc)

    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    candidates: list[dict] = []

    for feed_url, source, window_hours in _feed_plan(profile):
        for candidate in _candidates_for_feed(
            feed_url, now, window_hours, profile.fetch.max_entries_per_feed, source
        ):
            norm_url = _normalize_url(candidate["url"])
            norm_title = _normalize_title(candidate["title"])
            if norm_url in seen_urls or norm_title in seen_titles:
                continue
            seen_urls.add(norm_url)
            seen_titles.add(norm_title)
            candidates.append(candidate)

    # Full-text extraction is the expensive/networked step, so it runs last,
    # only on the deduped, in-window, capped candidate list.
    articles = [a for c in candidates if (a := _extract_article(c)) is not None]

    output = FetchOutput(
        episode_id=episode_id,
        fetched_at=now,
        articles=articles,
    )

    out_path = episode_dir(episode_id) / "articles.json"
    out_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return output
