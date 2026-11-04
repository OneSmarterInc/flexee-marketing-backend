"""The day-before reminder email.

About a day before a round closes, every active student on a team that has NOT submitted
gets one email with the real closing time in the game's time zone. For a round shorter than
two days it goes out half a round before the close.

- Runs at the end of every sweep (see games/scheduler.py).
- Once per round: the game is claimed for the round (`reminder_sent_for_round`) before
  anything is sent, so two sweeps cannot both send it. If nothing at all could be sent
  (mail down), the claim is given back and the next sweep retries; a send that partly
  worked is not repeated.
- Left out: teams that have submitted, deactivated users, duplicate addresses, test accounts
  (test-N@mailinator.com, a public inbox), reference firms, and practice games.
- Needs working mail: with the console/dummy backend nothing is sent and nothing is claimed.
  REMINDER_EMAILS_ENABLED=false switches reminders off; EMAIL_DRY_RUN=true logs each email
  instead of sending it.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail

from accounts.models import Role
from . import models
from .deadlines import clock_runs, now_utc, round_deadline, round_length_days, zone

log = logging.getLogger("flexee.reminders")

TEST_EMAIL = re.compile(r"^test-\d+@mailinator\.com$", re.IGNORECASE)
_NO_REAL_MAIL = ("console", "dummy")


def mail_configured() -> bool:
    """True when an email could actually be delivered (or EMAIL_DRY_RUN is on)."""
    if getattr(settings, "EMAIL_DRY_RUN", False):
        return True
    backend = getattr(settings, "EMAIL_BACKEND", "")
    return not any(word in backend for word in _NO_REAL_MAIL)


def lead_time(game, rnd: int) -> timedelta:
    """A day before the close; half a round before it for a round shorter than two days."""
    days = round_length_days(game, rnd)
    return timedelta(days=days / 2) if days < 2 else timedelta(days=1)


def _recipients(game, rnd: int) -> list[tuple[str, str, str]]:
    """(email, display name, team name) for each student who should be reminded."""
    submitted = set(game.submissions.filter(round=rnd).values_list("team_id", flat=True))
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for team in game.teams.filter(reference_path="").prefetch_related("users"):
        if team.id in submitted:
            continue
        for user in team.users.all():
            email = (user.email or "").strip()
            if user.role != Role.STUDENT or not user.is_active or not email:
                continue
            if TEST_EMAIL.match(email) or email.lower() in seen:
                continue
            seen.add(email.lower())
            out.append((email, (user.first_name or user.username), team.name))
    return out


def _closing_text(game, deadline) -> str:
    local = deadline.astimezone(zone(game.timezone))
    return f"{local.strftime('%A, %B')} {local.day} at {local.strftime('%I:%M %p').lstrip('0')} ({game.timezone})"


def _send(game, rnd: int, deadline, email: str, name: str, team: str) -> bool:
    subject = f"Round {rnd} of {game.name} closes {_closing_text(game, deadline)}"
    body = (
        f"Hello {name},\n\n"
        f"Round {rnd} of \"{game.name}\" closes on {_closing_text(game, deadline)}. "
        f"Your firm, {team}, hasn't submitted its decision yet.\n\n"
        f"Open the simulation: {settings.FRONTEND_URL}/app\n\n"
        f"If your team doesn't submit by then, your firm plays this round on the average "
        f"of the firms that did.\n"
    )
    if getattr(settings, "EMAIL_DRY_RUN", False):
        log.info("[dry run] reminder to %s: %s", email, subject)
        return True
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
        return True
    except Exception:
        log.exception("reminder to %s could not be sent", email)
        return False


def send_game_reminder(game, now=None) -> int:
    """Send this game's reminder if it is due. Returns how many emails went out."""
    now = now or now_utc()
    rnd = game.current_round
    if not clock_runs(game):
        return 0
    deadline = round_deadline(game, rnd)
    if deadline is None or not (deadline - lead_time(game, rnd) <= now < deadline):
        return 0
    # Nothing to submit yet (a listening round): no reminder.
    from .services import GameService
    if not GameService(game).open_levers(rnd):
        return 0
    if game.reminder_sent_for_round == rnd:
        return 0

    previous = game.reminder_sent_for_round
    claimed = models.Game.objects.filter(pk=game.pk).exclude(reminder_sent_for_round=rnd) \
        .update(reminder_sent_for_round=rnd)
    if claimed != 1:
        return 0   # another sweep got there first
    game.reminder_sent_for_round = rnd

    recipients = _recipients(game, rnd)
    sent = sum(1 for email, name, team in recipients if _send(game, rnd, deadline, email, name, team))
    if recipients and sent == 0:
        # Nothing could be sent (mail down): give the claim back so the next sweep retries.
        models.Game.objects.filter(pk=game.pk).update(reminder_sent_for_round=previous)
        game.reminder_sent_for_round = previous
    return sent


def send_due_reminders(now=None) -> int:
    """The reminder pass of a sweep, across every live game."""
    if not getattr(settings, "REMINDER_EMAILS_ENABLED", True) or not mail_configured():
        return 0
    total = 0
    for game in models.Game.objects.filter(student_deployed=True, is_practice=False) \
            .exclude(status=models.GameStatus.COMPLETE):
        try:
            total += send_game_reminder(game, now)
        except Exception:
            log.exception("reminder pass failed for game %s", game.pk)
    return total
