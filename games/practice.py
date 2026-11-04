"""Practice games: a copy of a live game for a faculty member to try the sim against three
scripted reference firms.

- A practice game has no deadlines and no students. It has one team for the faculty member who
  made it ("Your practice firm") and three reference firms, each playing a fixed path.
- The reference firms' decisions are written at the start of every close, before the engine
  computes, so none of them is ever filled in as a missing submission.
- Rounds close only when the faculty member presses Advance. "Start over" wipes the play and goes
  back to the first decision round.
- Practice games stay out of billing, reminders, the scheduler and the reporting connector.

The three paths are the engine's own calibration archetypes (HERITAGE, PERF, PIVOT in
flexee_engine/vendor/calibration.py), each played the same way every round, with the app-layer
levers (channel, message, line extension) that tell the same story. A test pins the numbers to the
calibration module, so they cannot drift from it quietly.
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone as dj_tz
from rest_framework.exceptions import PermissionDenied, ValidationError

from . import models
from .validators import LEVER_FIELDS, validate_decision

log = logging.getLogger("flexee.practice")

YOUR_FIRM = "Your practice firm"


@dataclass(frozen=True)
class ReferencePath:
    id: str
    name: str
    blurb: str
    # The core decision, as the calibration archetype plays it every round.
    brand: float
    perf: float
    research: float
    positioning: float
    emphasis: tuple
    price: float
    promotion: float
    # App-layer levers that tell the same story.
    channel: dict
    message: str
    launch_line: bool


REFERENCE_PATHS: dict[str, ReferencePath] = {
    p.id: p for p in (
        ReferencePath(
            id="heritage_defender", name="Heritage Defender",
            blurb="Defends the loyal base: high-caffeine heritage position, retail shelf, "
                  "the heritage message, no functional line. Strong now, exposed in the crisis.",
            brand=6.0, perf=2.0, research=2.0, positioning=18.0,
            emphasis=(0.45, 0.15, 0.05, 0.05, 0.30), price=1.50, promotion=0.0,
            channel={"RETAIL": 0.70, "DTC": 0.20, "SOCIAL": 0.10},
            message="HERITAGE", launch_line=False),
        ReferencePath(
            id="performance_chaser", name="Performance Chaser",
            blurb="Chases the performance and Gen-Z segments with heavy performance marketing, "
                  "a lean-wellness position and a performance message; modest promotion.",
            brand=2.0, perf=6.0, research=2.0, positioning=70.0,
            emphasis=(0.10, 0.15, 0.30, 0.35, 0.10), price=1.55, promotion=0.3,
            channel={"RETAIL": 0.30, "DTC": 0.40, "SOCIAL": 0.30},
            message="PERFORMANCE", launch_line=False),
        ReferencePath(
            id="wellness_pivoter", name="Wellness Pivoter",
            blurb="Moves to clean functional wellness: wellness position, social and "
                  "direct-to-consumer, the clean-energy message, and launches the functional "
                  "line the round it opens. Buffered in the crisis.",
            brand=4.0, perf=3.0, research=3.0, positioning=82.0,
            emphasis=(0.05, 0.15, 0.40, 0.30, 0.10), price=1.55, promotion=0.1,
            channel={"RETAIL": 0.20, "DTC": 0.30, "SOCIAL": 0.50},
            message="CLEAN_ENERGY", launch_line=True),
    )
}
REFERENCE_PATH_IDS = tuple(REFERENCE_PATHS)


def path_levers(path: ReferencePath, config: dict) -> dict:
    """Every field the path plays, fitted to this game's own budget and price band so a path is
    valid whatever the faculty config says (the allocation keeps its proportions)."""
    budget = float(config.get("round_budget", 10.0))
    base = path.brand + path.perf + path.research
    brand = round(path.brand * budget / base, 6)
    perf = round(path.perf * budget / base, 6)
    research = round(budget - brand - perf, 6)          # exact sum, whatever the rounding
    lo, hi = (float(x) for x in config.get("price_band", [1.10, 1.90]))
    return {
        "brand": brand, "perf": perf, "research": research,
        "positioning": path.positioning,
        "emphasis": list(path.emphasis),
        "price": min(max(path.price, lo), hi),
        "promotion": path.promotion,
        "channel": dict(path.channel),
        "message": path.message,
        "line_extension": {"launch": path.launch_line, "positioning": path.positioning},
    }


def reference_decision(path_id: str, open_levers: list, config: dict) -> dict:
    """What a reference firm plays when `open_levers` are open: the path's fields for those
    levers only, checked by the same validator a student's decision goes through."""
    open_fields = {f for lever in open_levers for f in LEVER_FIELDS.get(lever, ())}
    levers = {k: v for k, v in path_levers(REFERENCE_PATHS[path_id], config).items() if k in open_fields}
    return validate_decision(levers, open_levers, config)


def write_reference_decisions(game: models.Game, rnd: int, open_levers: list) -> int:
    """Write every reference firm's decision for round `rnd`. Safe to repeat (a reopened round
    is closed again). Returns how many firms it wrote for; a round with no open lever has no
    decision to write."""
    if not open_levers:
        return 0
    wrote = 0
    for team in game.teams.exclude(reference_path=""):
        if team.reference_path not in REFERENCE_PATHS:
            continue
        levers = reference_decision(team.reference_path, open_levers, game.config_snapshot or {})
        models.RoundSubmission.objects.update_or_create(
            game=game, round=rnd, team=team, defaults={"submitted_levers": levers, "submitted_by": None})
        wrote += 1
    return wrote


def my_team(game: models.Game, user):
    """The faculty member's own firm in their practice game (None for any other game or user)."""
    if not game.is_practice or user is None or not getattr(user, "is_faculty", False):
        return None
    return game.teams.filter(users=user, reference_path="").first()


def first_round(game: models.Game) -> int:
    """A practice starts where the decisions start: the takeover round."""
    return max(1, int(game.takeover_round))


@transaction.atomic
def create_practice(user, source: models.Game) -> tuple[models.Game, bool]:
    """A practice copy of `source` for `user`. Returns (game, created). A user keeps one
    practice game per source: asking again gives the same one back (use Start over to reset)."""
    if source.is_practice:
        raise ValidationError({"detail": "This is already a practice game."})
    existing = models.Game.objects.filter(is_practice=True, practice_of=source, faculty=user).first()
    if existing:
        return existing, False
    now = dj_tz.now()
    game = models.Game.objects.create(
        name=f"{source.name} (practice)", num_rounds=source.num_rounds,
        takeover_round=source.takeover_round,
        config_snapshot=copy.deepcopy(source.config_snapshot),
        reveal_policy=copy.deepcopy(source.reveal_policy),
        faculty=user, status=models.GameStatus.RUNNING,
        current_round=max(1, int(source.takeover_round)),
        timezone=source.timezone, round_pacing_days=source.round_pacing_days,
        start_date=None, schedule=[],                       # no deadlines
        students_per_team=1, student_capacity=0,
        price_per_student=0, advisor_rate_per_hour=0,       # nothing to bill
        deployed=True, deployed_at=now, student_deployed=True, student_deployed_at=now,
        is_practice=True, practice_of=source, ai_enabled=source.ai_enabled,
    )
    game.faculty_members.set([user])
    mine = models.Team.objects.create(
        game=game, name=YOUR_FIRM, members=[user.get_full_name() or user.username])
    mine.users.add(user)
    for pid in REFERENCE_PATH_IDS:
        models.Team.objects.create(game=game, name=REFERENCE_PATHS[pid].name, reference_path=pid)
    return game, True


def reset_practice(game: models.Game) -> None:
    """Start over: wipe the play (decisions, results, forecasts, research, AI sessions, close
    history) and go back to the first decision round. The firms stay."""
    if not game.is_practice:
        raise ValidationError({"detail": "Only a practice game can be started over."})
    from .services import GameService
    svc = GameService(game)
    if not svc.claim_close():       # the claim a close takes: refuse while one is running
        raise ValidationError({"detail": "A round is closing right now. Try again in a minute."})
    try:
        with transaction.atomic():
            for rel in ("submissions", "results", "focus_groups", "relationships",
                        "advisor_consults", "defenses", "research", "forecasts"):
                getattr(game, rel).all().delete()
            models.RoundCloseLog.objects.filter(game_id_ref=game.pk).delete()
            game.current_round = first_round(game)
            game.status = models.GameStatus.RUNNING
            game.locked_rounds = []
            game.save(update_fields=["current_round", "status", "locked_rounds"])
    finally:
        svc.release_close()


def require_practice_access(user, game: models.Game) -> None:
    """Only the faculty member who owns a practice game (or an admin) may start it over."""
    if not (user.is_admin or game.faculty_id == user.id):
        raise PermissionDenied("This practice game belongs to someone else.")
