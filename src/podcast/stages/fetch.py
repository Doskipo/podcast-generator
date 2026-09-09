"""Fetch stage: pull RSS feeds, extract full text, dedupe, keep a recent window.

Typed input: Profile (+ episode_id). Typed output: FetchOutput, persisted as
data/episodes/<episode_id>/articles.json.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import feedparser
import trafilatura

from podcast.models import Article, FetchOutput, Profile
from podcast.paths import episode_dir

logger = logging.getLogger(__name__)

# Below this many extracted characters we treat the extraction as failed rather
# than risk grounding the script in a near-empty article.
MIN_EXTRACTED_CHARS = 200


def _normalize_url(url: str) -> str:
    """Strip query/fragment and trailing slash so tracking params don't defeat dedupe."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


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


def _candidates_for_feed(feed_url: str, cutoff: datetime, max_entries: int) -> list[dict]:
    """Parse one feed and return recent, capped candidates (newest first). Never raises."""
    try:
        parsed = feedparser.parse(feed_url)
    except Exception:
        logger.warning("skipping feed %s: failed to parse", feed_url, exc_info=True)
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
    )


def fetch_stage(profile: Profile, episode_id: str) -> FetchOutput:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=profile.fetch.window_hours)

    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    candidates: list[dict] = []

    for feed_url in profile.feeds:
        for candidate in _candidates_for_feed(
            str(feed_url), cutoff, profile.fetch.max_entries_per_feed
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
        window_hours=profile.fetch.window_hours,
        articles=articles,
    )

    out_path = episode_dir(episode_id) / "articles.json"
    out_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return output
