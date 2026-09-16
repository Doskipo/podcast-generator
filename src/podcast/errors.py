"""Turns a raised stage exception into a short, human-readable failure
reason for the UI/dashboard. Left alone, a provider SDK exception's
`str()` is a wall of text (an HTTP status line plus a raw JSON error body)
— unreadable in a status badge or a table cell, and it drowns the one
thing a user actually needs to know: is this my API key, my quota, or
something else. See docs/decisions.md ("Episode list: mocked, status
filter, resilience").
"""

from __future__ import annotations

import openai
from elevenlabs.core.api_error import ApiError as ElevenLabsApiError

# Kept short on purpose — this is read in a status badge / table cell, not
# a log line (the full exception is still logged via logger.exception in
# service.call_stage).
_FALLBACK_MAX_LEN = 200

_STATUS_MESSAGES = {
    401: "authentication failed — check the API key",
    403: "access denied for this API key",
    429: "rate limit or quota exceeded",
}


def _from_status_code(provider: str, status_code: int | None) -> str:
    detail = _STATUS_MESSAGES.get(status_code)
    if detail:
        return f"{provider}: {detail}"
    return f"{provider}: request failed (status {status_code})"


def _elevenlabs_body_code(body: object) -> str | None:
    """ElevenLabs' own error `code`/`status` field, when `body` is the
    usual `{"detail": {"code": ..., "status": ..., ...}}` shape — seen in
    practice: a quota-exceeded request comes back as HTTP 401 (not 429),
    with `code: "quota_exceeded"` the only reliable signal it's a quota
    problem, not a bad API key. Falls back to status-code alone (via
    `_from_status_code`) when `body` doesn't have this shape."""
    if not isinstance(body, dict):
        return None
    detail = body.get("detail")
    if not isinstance(detail, dict):
        return None
    return detail.get("code") or detail.get("status")


def humanize_stage_error(exc: Exception) -> str:
    """Short, human-readable reason for `exc`, prefixed with the provider
    when recognized (OpenAI or ElevenLabs auth/quota/rate-limit errors —
    the two paid, externally-rate-limited dependencies this pipeline has).
    Anything else falls back to `str(exc)`, truncated, so this is always
    safe to call and never hides an unrecognized error's actual message."""
    if isinstance(exc, openai.APIStatusError):
        return _from_status_code("OpenAI", exc.status_code)
    if isinstance(exc, openai.APIConnectionError):
        return "OpenAI: could not connect (network or timeout)"

    if isinstance(exc, ElevenLabsApiError):
        if _elevenlabs_body_code(exc.body) == "quota_exceeded":
            return "ElevenLabs: rate limit or quota exceeded"
        return _from_status_code("ElevenLabs", exc.status_code)

    text = str(exc) or exc.__class__.__name__
    if len(text) > _FALLBACK_MAX_LEN:
        text = text[: _FALLBACK_MAX_LEN - 1] + "…"
    return text
