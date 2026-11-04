"""The round sweep: closes due rounds, sends the day-before reminder, records a heartbeat.

Three things call it, and they are safe to run together because every close takes the same
atomic claim (`GameService.claim_close`):

- the `run_scheduler` management command (a loop, every five minutes by default),
- an external cron hitting /meridian/api/cron/round-tick (guarded by CRON_SECRET),
- the lazy path: loading a game after its deadline closes the round then and there
  (`auto_advance_if_due`), so a game still moves on even if nothing is running the sweep.

The sweep never closes a game's last round: a facilitator concludes it. Practice games have
no deadlines and are left alone, as are games not yet deployed to students.
"""
from __future__ import annotations

import logging

from rest_framework.exceptions import ValidationError

from . import models
from .deadlines import clock_runs, now_utc, round_deadline
from .reminders import send_due_reminders

log = logging.getLogger("flexee.scheduler")

SWEEP_KEY = "round-sweep"


def is_due(game, now=None) -> bool:
    """Whether the game's open round is past its deadline and the sweep may close it."""
    if not clock_runs(game) or game.current_round >= game.num_rounds:
        return False
    deadline = round_deadline(game, game.current_round)
    return deadline is not None and (now or now_utc()) >= deadline


def auto_advance_if_due(game, now=None) -> bool:
    """Close the game's open round if its deadline has passed. True if a round was closed.
    Never raises: a lazy page load must not fail because a close did."""
    from .services import GameService
    if not is_due(game, now):
        return False
    rnd = game.current_round
    try:
        GameService(game).close_round(rnd, source="deadline")
    except ValidationError:
        return False        # someone else is already closing it
    except Exception:
        log.exception("auto-advance of game %s round %s failed", game.pk, rnd)
        return False
    game.refresh_from_db()
    return True


def run_sweep(now=None) -> dict:
    """One pass: close every due round (catching up if several are overdue), send reminders,
    record the heartbeat. Returns what it did."""
    now = now or now_utc()
    closed: list[dict] = []
    games = models.Game.objects.filter(student_deployed=True, is_practice=False) \
        .exclude(status=models.GameStatus.COMPLETE)
    for game in games:
        for _ in range(game.num_rounds):          # bounded: at most one close per round
            rnd = game.current_round
            if not auto_advance_if_due(game, now):
                break
            closed.append({"game": game.pk, "round": rnd})
    try:
        reminders = send_due_reminders(now)
    except Exception:
        log.exception("reminder pass failed")
        reminders = 0
    models.SchedulerState.objects.update_or_create(key=SWEEP_KEY, defaults={"last_run_at": now})
    return {"closed": closed, "reminders_sent": reminders, "ran_at": now.isoformat()}
