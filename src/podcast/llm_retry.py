"""Generic one-retry-with-feedback wrapper, shared by the three LLM-backed
script stages (outline, script, critique). See docs/decisions.md ("One-
retry-with-feedback") for why exactly one retry, with the validation error
fed back into the prompt, is the resilience policy here — not exponential
backoff, not N retries, not a silent fallback.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def generate_with_retry(
    generate: Callable[[str], T],
    validate: Callable[[T], None],
    user_prompt: str,
    stage_name: str,
) -> T:
    """Call `generate(user_prompt)`, then `validate(result)` — `validate`
    raises `ValueError` on an unacceptable result, returns None otherwise
    (a side-effect-free check, not a transform). On a ValueError, retries
    exactly once with the error message appended to `user_prompt` as
    corrective feedback for the model; a second ValueError propagates
    unchanged, exactly as if there were no retry at all. Any other
    exception (an OpenAI/network error, say) is never retried here — this
    is specifically for the model producing a structurally-valid-but-wrong
    response, not for transient call failures."""
    try:
        result = generate(user_prompt)
        validate(result)
        return result
    except ValueError as exc:
        logger.warning("%s: validation failed, retrying once with feedback: %s", stage_name, exc)
        retry_prompt = (
            f"{user_prompt}\n\n"
            f"Your previous response was invalid: {exc}\n"
            "Correct this and respond again, following all the same instructions."
        )
        result = generate(retry_prompt)
        validate(result)
        return result
