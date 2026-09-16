"""Tests for the generic one-retry-with-feedback wrapper shared by the
outline/script/critique/perform stages. See docs/decisions.md ("One-retry-
with-feedback")."""

from __future__ import annotations

import pytest

from podcast.llm_retry import generate_with_retry


def test_returns_the_result_when_validation_passes_first_try():
    calls: list[str] = []

    def generate(prompt: str) -> str:
        calls.append(prompt)
        return "ok"

    def validate(result: str) -> None:
        pass

    result, retried = generate_with_retry(generate, validate, "the prompt", stage_name="test")

    assert result == "ok"
    assert retried is False
    assert calls == ["the prompt"]  # no retry needed


def test_retries_once_with_the_error_appended_to_the_prompt():
    calls: list[str] = []
    attempts = {"n": 0}

    def generate(prompt: str) -> str:
        calls.append(prompt)
        attempts["n"] += 1
        return "bad" if attempts["n"] == 1 else "good"

    def validate(result: str) -> None:
        if result == "bad":
            raise ValueError("that was wrong")

    result, retried = generate_with_retry(generate, validate, "original prompt", stage_name="test")

    assert result == "good"
    assert retried is True
    assert len(calls) == 2
    assert calls[0] == "original prompt"
    assert "original prompt" in calls[1]  # feedback prompt still carries the original instructions
    assert "that was wrong" in calls[1]  # the validation error is fed back verbatim


def test_second_validation_failure_propagates_unchanged():
    calls: list[str] = []

    def generate(prompt: str) -> str:
        calls.append(prompt)
        return "always bad"

    def validate(result: str) -> None:
        raise ValueError("still wrong")

    with pytest.raises(ValueError, match="still wrong"):
        generate_with_retry(generate, validate, "original prompt", stage_name="test")

    assert len(calls) == 2  # exactly one retry, no more


def test_non_valueerror_from_generate_is_not_retried():
    calls: list[str] = []

    def generate(prompt: str) -> str:
        calls.append(prompt)
        raise RuntimeError("network blip")

    def validate(result: str) -> None:
        pass

    with pytest.raises(RuntimeError, match="network blip"):
        generate_with_retry(generate, validate, "original prompt", stage_name="test")

    assert len(calls) == 1  # not retried — only ValueError from validate() triggers a retry
