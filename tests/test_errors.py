"""Smoke tests for podcast.errors.humanize_stage_error — turning a raised
stage exception (provider SDK errors especially) into a short reason for
the Episodes page / dashboard. No network."""

from __future__ import annotations

import httpx
import openai
from elevenlabs.core.api_error import ApiError as ElevenLabsApiError

from podcast.errors import humanize_stage_error


def _openai_status_error(cls, status_code: int) -> Exception:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status_code, request=request, json={"error": {"message": "raw provider detail"}})
    return cls("raw provider detail", response=response, body=None)


def test_openai_rate_limit_is_humanized():
    exc = _openai_status_error(openai.RateLimitError, 429)
    reason = humanize_stage_error(exc)
    assert reason == "OpenAI: rate limit or quota exceeded"
    assert "raw provider detail" not in reason


def test_openai_authentication_error_is_humanized():
    exc = _openai_status_error(openai.AuthenticationError, 401)
    assert humanize_stage_error(exc) == "OpenAI: authentication failed — check the API key"


def test_openai_permission_denied_is_humanized():
    exc = _openai_status_error(openai.PermissionDeniedError, 403)
    assert humanize_stage_error(exc) == "OpenAI: access denied for this API key"


def test_openai_other_status_falls_back_to_status_code():
    exc = _openai_status_error(openai.BadRequestError, 400)
    assert humanize_stage_error(exc) == "OpenAI: request failed (status 400)"


def test_openai_connection_error_is_humanized():
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    exc = openai.APIConnectionError(request=request)
    assert humanize_stage_error(exc) == "OpenAI: could not connect (network or timeout)"


def test_elevenlabs_rate_limit_is_humanized():
    exc = ElevenLabsApiError(status_code=429, body={"detail": "quota exceeded, raw"})
    reason = humanize_stage_error(exc)
    assert reason == "ElevenLabs: rate limit or quota exceeded"
    assert "raw" not in reason


def test_elevenlabs_unauthorized_is_humanized():
    exc = ElevenLabsApiError(status_code=401, body={"detail": "invalid key"})
    assert humanize_stage_error(exc) == "ElevenLabs: authentication failed — check the API key"


def test_elevenlabs_quota_exceeded_via_401_with_quota_body_code_is_humanized():
    """A real shape observed in practice: ElevenLabs returns quota-exceeded
    as HTTP 401 (not 429), with body.detail.code == "quota_exceeded" the
    only reliable signal — the status code alone would misread this as an
    auth failure."""
    exc = ElevenLabsApiError(
        status_code=401,
        body={
            "detail": {
                "type": "invalid_request",
                "code": "quota_exceeded",
                "message": "This request exceeds your API key quota.",
                "status": "quota_exceeded",
            }
        },
    )
    assert humanize_stage_error(exc) == "ElevenLabs: rate limit or quota exceeded"


def test_elevenlabs_genuine_401_without_quota_body_code_is_still_authentication_failure():
    exc = ElevenLabsApiError(status_code=401, body={"detail": {"code": "invalid_api_key", "status": "invalid_api_key"}})
    assert humanize_stage_error(exc) == "ElevenLabs: authentication failed — check the API key"


def test_elevenlabs_other_status_falls_back_to_status_code():
    exc = ElevenLabsApiError(status_code=500, body={"detail": "server error"})
    assert humanize_stage_error(exc) == "ElevenLabs: request failed (status 500)"


def test_unrecognized_exception_falls_back_to_str():
    assert humanize_stage_error(ValueError("outline references unknown source_ids: ['x']")) == (
        "outline references unknown source_ids: ['x']"
    )


def test_unrecognized_exception_with_no_message_falls_back_to_class_name():
    assert humanize_stage_error(RuntimeError()) == "RuntimeError"


def test_long_fallback_message_is_truncated():
    exc = ValueError("x" * 500)
    reason = humanize_stage_error(exc)
    assert len(reason) == 200
    assert reason.endswith("…")
