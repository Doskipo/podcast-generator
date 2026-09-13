"""Daily episode scheduling. One job, driven by profile.schedule's cron
expression, sharing the same service.run_episode() entrypoint the CLI and
the API's POST /episodes use.

`is_due` is a pure, unit-testable seam: it answers "would this profile's
cron fire at least once in (last_fired_at, now]?" using the exact same
CronTrigger class the real scheduler runs, so a test can check cron
evaluation without waiting on a real clock or starting a scheduler thread.
It isn't itself wired into the running app — the real app only ever uses
`_tick`, invoked by APScheduler's own trigger evaluation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from podcast import db, service
from podcast.models import Profile

logger = logging.getLogger(__name__)

JOB_ID = "podcast-daily-generate"


def create_scheduler() -> BackgroundScheduler:
    return BackgroundScheduler(timezone="UTC")


def schedule_from_profile(scheduler: BackgroundScheduler, profile: Profile, profile_id: int) -> None:
    """(Re-)register the daily-generate job from profile.schedule.
    replace_existing=True makes this safe to call again after PUT /profile
    changes the cron expression."""
    trigger = CronTrigger.from_crontab(profile.schedule, timezone="UTC")
    scheduler.add_job(_tick, trigger=trigger, id=JOB_ID, replace_existing=True, kwargs={"profile_id": profile_id})


def _tick(profile_id: int) -> None:
    """The scheduled job body. Reloads the profile row fresh (PUT /profile
    may have changed it since this job was registered) and starts a new
    episode via the same shared service function the API/CLI use."""
    with db.session_scope() as session:
        row = session.get(db.ProfileRecord, profile_id)
        if row is None:
            logger.warning("scheduled tick fired for missing profile_id=%s; skipping", profile_id)
            return
        profile = Profile.model_validate(row.data)
    service.run_episode(profile, profile_id)


# Substitute lower bound for is_due() when there's no last_fired_at yet
# ("never scheduled before"). CronTrigger.get_next_fire_time's `now` argument
# only sets the *lower* bound of the search, never an upper one (it happily
# returns a fire time arbitrarily far in the future) — so `is_due` must
# search forward from *some* point and then check the result against `now`
# itself. A concrete 10-years-ago anchor (rather than datetime.min, which
# risks overflow in the trigger's internal date arithmetic) is more than
# enough slack for any realistic daily/weekly podcast schedule.
_NEVER_FIRED_LOOKBACK = timedelta(days=3650)


def is_due(profile: Profile, now: datetime, last_fired_at: datetime | None) -> bool:
    """True if profile.schedule's cron trigger has at least one fire time in
    (last_fired_at, now]."""
    trigger = CronTrigger.from_crontab(profile.schedule, timezone="UTC")
    lower_bound = last_fired_at or (now - _NEVER_FIRED_LOOKBACK)
    next_fire = trigger.get_next_fire_time(lower_bound, now)
    return next_fire is not None and next_fire <= now
