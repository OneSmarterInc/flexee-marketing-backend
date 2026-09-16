"""Workspace billing — real-money charges derived from live simulation data.

Every figure here is computed from actual records, not sampled:

  * Seat charges  — each enrolled student billed at the sim's ``price_per_student``.
                    "Received" reflects the seats an instructor has marked paid
                    (``Game.seats_paid``); the rest is pending.
  * Advisor charges — accrue per *started hour* of one-on-one advisor chat, counted from
                    the ``AdvisorConsultation`` table and priced at the sim's
                    ``advisor_rate_per_hour`` (0 = advisors included, no charge).
  * Focus-group charges — accrue per *persona interviewed*, not per session: a group of 8
                    costs 8 units, one of 2 costs 2. Summed across ``FocusGroupSession``
                    rows and priced at the same ``advisor_rate_per_hour``.

Per-student price and advisor rate are configured per sim.
"""
from __future__ import annotations

from decimal import Decimal

from . import models


def _money(d: Decimal) -> float:
    """Round a Decimal to cents and hand back a JSON-friendly float."""
    return float(d.quantize(Decimal("0.01")))


def _enrolled_student_ids(game) -> set[int]:
    ids: set[int] = set()
    for team in game.teams.all():
        for u in team.users.all():
            if getattr(u, "role", None) == "student":
                ids.add(u.id)
    return ids


def _sim_row(game) -> dict:
    seats = len(_enrolled_student_ids(game))
    capacity = game.student_capacity or seats
    price = Decimal(game.price_per_student or 0)
    seats_paid = min(game.seats_paid or 0, seats)

    seat_billed = price * seats
    seat_received = price * seats_paid

    advisor_rate = Decimal(game.advisor_rate_per_hour or 0)
    advisor_hours = models.AdvisorConsultation.objects.filter(game=game).count()
    # Priced by personas interviewed, not sessions convened — a bigger group costs more.
    group_units = sum(
        len(ids or []) for ids in
        models.FocusGroupSession.objects.filter(game=game).values_list("persona_ids", flat=True)
    )
    advisor_charge = advisor_rate * advisor_hours
    group_charge = advisor_rate * group_units
    advisor_total = advisor_charge + group_charge

    billed = seat_billed + advisor_total
    received = seat_received  # advisor charges are invoiced, not seat-paid
    pending = billed - received

    return {
        "id": game.id,
        "name": game.name,
        "seats": seats,
        "capacity": capacity,
        "seats_paid": seats_paid,
        "price_per_student": _money(price),
        "advisor_hours": advisor_hours,
        "advisor_charge": _money(advisor_charge),
        "group_units": group_units,
        "group_charge": _money(group_charge),
        "advisor_total": _money(advisor_total),
        "billed": _money(billed),
        "received": _money(received),
        "pending": _money(pending),
    }


def compute_billing(games_qs) -> dict:
    """Build the full billing payload (summary counts, charge totals, per-sim rows)."""
    games = list(games_qs.prefetch_related("teams", "teams__users"))
    rows = [_sim_row(g) for g in games]

    student_ids: set[int] = set()
    instructor_names: set[str] = set()
    team_count = 0
    for g in games:
        student_ids |= _enrolled_student_ids(g)
        instructor_names |= set(g.faculty_members.values_list("username", flat=True))
        team_count += g.teams.count()

    total_billed = sum((Decimal(str(r["billed"])) for r in rows), Decimal("0"))
    total_received = sum((Decimal(str(r["received"])) for r in rows), Decimal("0"))
    total_advisor = sum((Decimal(str(r["advisor_total"])) for r in rows), Decimal("0"))
    total_group = sum((Decimal(str(r["group_charge"])) for r in rows), Decimal("0"))

    return {
        "counts": {
            "simulations": len(games),
            "teams": team_count,
            "students": len(student_ids),
            "instructors": len(instructor_names),
        },
        "charges": {
            "total_billed": _money(total_billed),
            "received": _money(total_received),
            "pending": _money(total_billed - total_received),
            "advisor": _money(total_advisor),
            "advisor_group": _money(total_group),
        },
        "sims": rows,
    }
