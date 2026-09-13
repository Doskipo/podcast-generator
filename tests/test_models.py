"""Tests for validation living directly on the Pydantic models (as opposed
to a pipeline stage) — currently just PodcastSettings' host count/voice_id
guard. See docs/decisions.md ("Profile validation")."""

from __future__ import annotations

import pytest

from podcast.models import Host, Listener, PodcastSettings, Style


def _podcast_settings(hosts: list[Host]) -> dict:
    return dict(
        name="Test Podcast",
        duration_minutes=8,
        listener=Listener(name="Eudald"),
        hosts=hosts,
        style=Style(humour=2, depth=2, tangents=True, banter=True),
        tone="curious",
    )


def _host(name: str, voice_id: str = "voice-id") -> Host:
    return Host(name=name, voice_id=voice_id, persona=f"{name} is curious.")


def test_podcast_settings_accepts_exactly_two_hosts_with_voice_ids():
    settings = PodcastSettings(**_podcast_settings([_host("Nova"), _host("Max")]))
    assert len(settings.hosts) == 2


def test_podcast_settings_rejects_one_host():
    with pytest.raises(ValueError, match="exactly two hosts"):
        PodcastSettings(**_podcast_settings([_host("Nova")]))


def test_podcast_settings_rejects_three_hosts():
    with pytest.raises(ValueError, match="exactly two hosts"):
        PodcastSettings(**_podcast_settings([_host("Nova"), _host("Max"), _host("Extra")]))


def test_podcast_settings_rejects_zero_hosts():
    with pytest.raises(ValueError, match="exactly two hosts"):
        PodcastSettings(**_podcast_settings([]))


@pytest.mark.parametrize("bad_voice_id", ["", "   "])
def test_podcast_settings_rejects_a_host_with_no_voice_id(bad_voice_id):
    with pytest.raises(ValueError, match="voice_id"):
        PodcastSettings(**_podcast_settings([_host("Nova", voice_id=bad_voice_id), _host("Max")]))
