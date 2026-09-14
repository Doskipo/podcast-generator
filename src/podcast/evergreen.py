"""Evergreen fallback: when an interest has zero selected (real, fetched)
candidates, ground one story in a Wikipedia primer instead of leaving it
silent. Called from `podcast.stages.rank.rank_stage` — one attempt per
empty interest, never in competition with real news (see docs/decisions.md,
"Evergreen fallback", for why news always wins, why the fixed 0.5 score,
and why the 7-day anti-repeat cache).

Not a pipeline stage of its own — no typed Output model, no persisted
manifest of its own. `fetch_primer` either hands rank_stage a real, grounded
`Article` (`source="evergreen"`) or `None`; every failure mode (search
turns up nothing, extraction fails, the network is down) degrades to `None`
and is logged, never raised — same defensive posture as fetch.py's per-feed
try/except and rank.py's backfill-on-failure.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import httpx

from podcast import paths
from podcast.models import Article, Interest
from podcast.stages.fetch import source_id_for_url

logger = logging.getLogger(__name__)

# Sits between the rank-stage scoring rubric's "tangential" (0.4) and
# "clearly related" (0.7) tiers (see rank.py:_score_batch's prompt): always
# beats a weak/tangential real candidate if one somehow existed (it
# wouldn't — evergreen only ever fills a genuinely empty interest), but
# never lands in the "confident match" tier reserved for real news, so a
# good news day from *other* interests still leads the episode's global
# ordering. See docs/decisions.md.
EVERGREEN_SCORE = 0.5

# A primer for the same topic won't repeat within this many days — long
# enough that consecutive episodes never sound identical, short enough that
# a slow-news topic isn't permanently exiled from its best-matching page.
CACHE_TTL_DAYS = 7

# Plain-text chars kept from the extract (summary + first sections) — same
# truncate-for-prompt-size idea as script.py's ARTICLE_TEXT_CHARS, just a
# bigger budget since a primer is the *only* grounding for its segment.
EVERGREEN_TEXT_CHARS = 4000

SEARCH_LIMIT = 5
TIMEOUT_SECONDS = 15
USER_AGENT = "podcast-generator/1.0 (personal podcast pipeline)"

_SEARCH_URL = "https://en.wikipedia.org/w/rest.php/v1/search/page"
_ACTION_API_URL = "https://en.wikipedia.org/w/api.php"


def _wikipedia_search(query: str) -> list[str]:
    """Boundary around the Wikipedia REST search call — the seam tests
    monkeypatch. Returns candidate page titles, best match first."""
    response = httpx.get(
        _SEARCH_URL,
        params={"q": query, "limit": SEARCH_LIMIT},
        timeout=TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return [page["title"] for page in response.json().get("pages", [])]


def _wikipedia_extract(title: str) -> str | None:
    """Boundary around the Wikipedia extract call — the seam tests
    monkeypatch. Plain-text summary *and* the page's first sections (the
    action API's `extracts` prop with `explaintext`, no `exintro`),
    truncated to EVERGREEN_TEXT_CHARS. None if the page has no extract."""
    response = httpx.get(
        _ACTION_API_URL,
        params={
            "action": "query",
            "format": "json",
            "prop": "extracts",
            "explaintext": 1,
            "exsectionformat": "plain",
            "redirects": 1,
            "titles": title,
        },
        timeout=TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    pages = response.json().get("query", {}).get("pages", {})
    page = next(iter(pages.values()), None)
    extract = (page or {}).get("extract")
    return extract[:EVERGREEN_TEXT_CHARS] if extract else None


def _load_cache() -> dict:
    path = paths.EVERGREEN_CACHE_PATH
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("evergreen: could not read cache at %s, starting empty: %s", path, exc)
        return {}


def _save_cache(cache: dict) -> None:
    path = paths.EVERGREEN_CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _pick_title(candidates: list[str], cached_entry: dict | None, now: datetime, topic: str) -> str | None:
    """The top search candidate, unless it's the page already used for this
    topic within CACHE_TTL_DAYS — then the next-best candidate instead.
    Falls back to reusing it if it's the only candidate at all: a repeated
    primer still beats no primer (no_content is the worse outcome)."""
    if not candidates:
        return None
    if cached_entry is not None:
        used_at = datetime.fromisoformat(cached_entry["used_at"])
        if now - used_at < timedelta(days=CACHE_TTL_DAYS):
            avoid = cached_entry["page_title"]
            fresh = [c for c in candidates if c != avoid]
            if fresh:
                return fresh[0]
            logger.info(
                "evergreen: only candidate page for %r is %r, used within the last %d day(s) — reusing anyway",
                topic,
                avoid,
                CACHE_TTL_DAYS,
            )
    return candidates[0]


def fetch_primer(interest: Interest) -> Article | None:
    """One grounded Wikipedia primer for `interest`, or None if search/
    extraction turns up nothing usable — never raises."""
    query = interest.description or interest.topic
    try:
        candidates = _wikipedia_search(query)
    except httpx.HTTPError as exc:
        logger.warning("evergreen: search failed for %r: %s", interest.topic, exc)
        return None
    if not candidates:
        logger.warning("evergreen: no Wikipedia page found for %r", interest.topic)
        return None

    cache = _load_cache()
    now = datetime.now(timezone.utc)
    title = _pick_title(candidates, cache.get(interest.topic), now, interest.topic)
    if title is None:
        return None

    try:
        extract = _wikipedia_extract(title)
    except httpx.HTTPError as exc:
        logger.warning("evergreen: extract failed for %r (%s): %s", interest.topic, title, exc)
        return None
    if not extract:
        logger.warning("evergreen: %r (%s) had no usable extract", interest.topic, title)
        return None

    url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
    article = Article(
        source_id=source_id_for_url(url),
        url=url,
        title=title,
        feed_url=url,  # no real feed for a primer — the page itself stands in
        published_at=now,  # evergreen has no meaningful publish date; fetch time instead
        fetched_at=now,
        summary=extract[:300],
        text=extract,
        source="evergreen",
        interest=interest.topic,
    )

    cache[interest.topic] = {"page_title": title, "used_at": now.isoformat()}
    _save_cache(cache)

    return article
