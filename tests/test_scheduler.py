"""Tests for scheduler.py's cron evaluation. No real waiting/clock: is_due()
is a pure function built on the same CronTrigger class the real
BackgroundScheduler uses, so cron correctness is tested by feeding it fixed
timestamps rather than starting a scheduler thread.
"""

from __future__ import annotations

from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from podcast import scheduler
from podcast.models import Host, Interest, Listener, PodcastSettings, Profile, Style

UTC = timezone.utc


def _profile(schedule: str = "0 7 * * *") -> Profile:
    return Profile(
        name="Test",
        interests=[Interest(topic="testing", weight=1.0, feeds=["https://example.com/feed.xml"])],
        podcast=PodcastSettings(
            name="Test Podcast",
            duration_minutes=8,
            listener=Listener(name="Eudald"),
            hosts=[
                Host(name="Nova", voice_id="voice-nova", persona="Nova is curious and precise."),
                Host(name="Max", voice_id="voice-max", persona="Max is curious and precise."),
            ],
            style=Style(humour=2, depth=2, tangents=True, banter=True),
            tone="curious",
        ),
        schedule=schedule,
    )


def test_is_due_true_when_cron_fires_between_last_and_now():
    profile = _profile("0 7 * * *")
    now = datetime(2026, 9, 14, 7, 0, tzinfo=UTC)
    last_fired_at = datetime(2026, 9, 13, 7, 0, tzinfo=UTC)
    assert scheduler.is_due(profile, now, last_fired_at) is True


def test_is_due_false_when_no_fire_in_window():
    profile = _profile("0 7 * * *")
    now = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
    last_fired_at = datetime(2026, 9, 13, 7, 0, tzinfo=UTC)
    assert scheduler.is_due(profile, now, last_fired_at) is False


def test_is_due_false_on_the_boundary_with_no_fire_strictly_between():
    profile = _profile("0 7 * * *")
    now = datetime(2026, 9, 13, 7, 0, tzinfo=UTC)
    last_fired_at = datetime(2026, 9, 13, 7, 0, tzinfo=UTC)
    assert scheduler.is_due(profile, now, last_fired_at) is False


def test_is_due_true_with_no_last_fired_at_and_a_past_fire_time():
    profile = _profile("0 7 * * *")
    now = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
    assert scheduler.is_due(profile, now, None) is True


def test_schedule_from_profile_registers_job_with_correct_trigger():
    # Left unstarted: add_job's registration + trigger construction don't
    # need a running scheduler (no background thread involved), and not
    # starting one keeps this test instant with nothing to tear down.
    sched = BackgroundScheduler(timezone="UTC")
    profile = _profile("30 6 * * *")

    scheduler.schedule_from_profile(sched, profile, profile_id=1)

    job = sched.get_job(scheduler.JOB_ID)
    assert job is not None
    assert isinstance(job.trigger, CronTrigger)
    assert str(job.trigger) == str(CronTrigger.from_crontab("30 6 * * *", timezone="UTC"))


def test_schedule_from_profile_replaces_existing_job():
    # replace_existing only dedupes against jobs already committed to the
    # jobstore — before start(), added jobs sit in a pending queue and
    # aren't deduped against each other. So this test needs a started
    # scheduler; shut it down immediately (wait=False) with nothing ever
    # actually ticking.
    sched = BackgroundScheduler(timezone="UTC")
    sched.start(paused=True)
    try:
        scheduler.schedule_from_profile(sched, _profile("0 7 * * *"), profile_id=1)
        scheduler.schedule_from_profile(sched, _profile("15 9 * * *"), profile_id=1)

        jobs = [j for j in sched.get_jobs() if j.id == scheduler.JOB_ID]
        assert len(jobs) == 1
        assert str(jobs[0].trigger) == str(CronTrigger.from_crontab("15 9 * * *", timezone="UTC"))
    finally:
        sched.shutdown(wait=False)
