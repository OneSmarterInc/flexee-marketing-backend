"""Faculty setup flow: provision test teams, invite students (email + self-registration
link), accept joins, and summarise setup progress for a sim."""
from __future__ import annotations

import secrets

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from accounts.models import Role, User
from . import models

TEST_PASSWORD = "flexee-test-1234"          # shared password for provisioned test accounts
TEST_DOMAIN = "mailinator.com"


# ---- links ----------------------------------------------------------------------
def _new_token() -> str:
    return secrets.token_urlsafe(24)


def join_url(token: str) -> str:
    return f"{settings.FRONTEND_URL}/join/{token}"


def ensure_join_token(game) -> str:
    if not game.join_token:
        game.join_token = _new_token()
        game.save(update_fields=["join_token"])
    return game.join_token


# ---- test team ------------------------------------------------------------------
def provision_test_team(game, per_firm: int) -> dict:
    """Create test students (test-N@mailinator.com), enroll them, and round-robin them
    across the sim's firms. Existing test accounts are reused. Returns a small summary."""
    teams = list(game.teams.all())
    if not teams:
        raise ValueError("Create at least one firm before provisioning a test team.")
    per_firm = max(1, min(int(per_firm), 20))
    total = per_firm * len(teams)

    created = 0
    with transaction.atomic():
        for i in range(total):
            email = f"test-{i + 1}@{TEST_DOMAIN}"
            user, made = User.objects.get_or_create(
                username=email,
                defaults={"email": email, "role": Role.STUDENT,
                          "first_name": "Test", "last_name": f"Student {i + 1}"},
            )
            if made:
                user.set_password(TEST_PASSWORD)
                user.save()
                created += 1
            models.Enrollment.objects.get_or_create(
                game=game, user=user, defaults={"is_test": True})
            team = teams[i % len(teams)]
            team.users.add(user)
    return {"provisioned": total, "created": created, "password": TEST_PASSWORD, "domain": TEST_DOMAIN}


# ---- invitations ----------------------------------------------------------------
def _send_invite_email(game, inv) -> bool:
    link = join_url(inv.token)
    subject = f"You're invited to the {game.name} simulation"
    body = (
        f"You've been invited to join the “{game.name}” marketing simulation.\n\n"
        f"Open this link to register and join:\n{link}\n\n"
        "If you weren't expecting this, you can ignore this email."
    )
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [inv.email], fail_silently=False)
        inv.emailed = True
        inv.emailed_at = timezone.now()
        inv.save(update_fields=["emailed", "emailed_at"])
        return True
    except Exception:
        return False


def create_invitations(game, emails: list[str], send: bool = True) -> dict:
    """Create (or reuse) invitations for a list of emails; optionally email each a join
    link. Returns counts. Duplicate emails within a sim reuse the existing invite."""
    cleaned = []
    seen = set()
    for raw in emails:
        e = (raw or "").strip().lower()
        if not e or "@" not in e or e in seen:
            continue
        seen.add(e)
        cleaned.append(e)

    created = 0
    emailed = 0
    for e in cleaned:
        inv, made = models.Invitation.objects.get_or_create(
            game=game, email=e, defaults={"token": _new_token()})
        if made:
            created += 1
        if send and inv.status == models.InviteStatus.PENDING:
            if _send_invite_email(game, inv):
                emailed += 1
    return {"created": created, "emailed": emailed, "requested": len(cleaned)}


# ---- accept a join --------------------------------------------------------------
def resolve_join(token: str):
    """Return (game, invitation|None) for a token, or (None, None) if unknown."""
    inv = models.Invitation.objects.filter(token=token).select_related("game").first()
    if inv:
        return inv.game, inv
    game = models.Game.objects.filter(join_token=token).first()
    return (game, None) if game else (None, None)


def accept_join(token: str, email: str, password: str, first: str = "", last: str = ""):
    """Register (or attach) a student and enroll them in the sim the token points to.
    Returns (user, game). Raises ValueError on bad token / weak password."""
    game, inv = resolve_join(token)
    if not game:
        raise ValueError("This invite link is invalid or has expired.")
    email = (email or "").strip().lower()
    if inv and inv.email and email != inv.email.lower():
        email = inv.email.lower()  # personalized invite pins the email
    if not email or "@" not in email:
        raise ValueError("A valid email is required.")
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")

    with transaction.atomic():
        user = User.objects.filter(username=email).first() or User.objects.filter(email=email).first()
        if user is None:
            user = User(username=email, email=email, role=Role.STUDENT,
                        first_name=first.strip(), last_name=last.strip())
            user.set_password(password)
            user.save()
        models.Enrollment.objects.get_or_create(game=game, user=user)
        if inv and inv.status == models.InviteStatus.PENDING:
            inv.status = models.InviteStatus.ACCEPTED
            inv.accepted_by = user
            inv.accepted_at = timezone.now()
            inv.save(update_fields=["status", "accepted_by", "accepted_at"])
    return user, game


# ---- summary --------------------------------------------------------------------
def setup_summary(game) -> dict:
    invites = list(game.invitations.all())
    total = len(invites)
    accepted = sum(1 for i in invites if i.status == models.InviteStatus.ACCEPTED)
    pending = total - accepted
    emailed = sum(1 for i in invites if i.emailed)

    enrollments = list(game.enrollments.select_related("user"))
    placed_ids = set()
    teams = []
    for t in game.teams.all():
        members = [{"id": u.id, "name": _name(u), "email": u.email or u.username}
                   for u in t.users.all() if u.role == Role.STUDENT]
        for m in members:
            placed_ids.add(m["id"])
        teams.append({"id": t.id, "name": t.name, "members": members})

    awaiting = [{"id": e.user.id, "name": _name(e.user), "email": e.user.email or e.user.username,
                 "is_test": e.is_test}
                for e in enrollments if e.user.id not in placed_ids]

    enrolled_total = len(enrollments)
    placed = len(placed_ids)

    checklist = {
        "has_students": enrolled_total > 0 or total > 0,
        "placed": placed,
        "placed_total": enrolled_total,
        "faculty_deployed": game.deployed,
        "student_deployed": game.student_deployed,
    }
    done = sum([checklist["has_students"], game.deployed, game.student_deployed])

    return {
        "invites": {"total": total, "pending": pending, "accepted": accepted,
                    "emailed": emailed},
        "placement": {"placed": placed, "unplaced": len(awaiting), "enrolled": enrolled_total},
        "teams": teams,
        "awaiting": awaiting,
        "checklist": checklist,
        "steps_done": done,
        "join_link": join_url(game.join_token) if game.join_token else None,
    }


def _name(u) -> str:
    full = f"{u.first_name or ''} {u.last_name or ''}".strip()
    return full or u.username or u.email


def overview_summary(game) -> dict:
    """Everything the faculty sim console's Overview page shows: enrollment, payment,
    run progress, firms, grading queue, teaching team, and cohort provisioning."""
    from accounts.models import Role

    enrollments = list(game.enrollments.select_related("user"))
    enrolled = len(enrollments)
    paid = sum(1 for e in enrollments if e.paid)
    paid_by_user = {e.user_id: e.paid for e in enrollments}

    cur = game.current_round or 1
    from .services import GameService  # local import: avoid a module-load cycle with services.py
    decisions_open = bool(GameService(game).open_levers(cur))
    submitted_team_ids = set(game.submissions.filter(round=cur).values_list("team_id", flat=True))

    placed_ids = set()
    firms = []
    for t in game.teams.all():
        members = [u for u in t.users.all() if u.role == Role.STUDENT]
        for u in members:
            placed_ids.add(u.id)
        firms.append({
            "id": t.id, "name": t.name,
            "members": len(members),
            "paid": sum(1 for u in members if paid_by_user.get(u.id)),
            "submitted_current_round": t.id in submitted_team_ids,
        })
    in_firms = len(placed_ids)

    # board defenses are auto-graded on submission (no manual queue); this is just a count.
    defended = models.DefenseSubmission.objects.filter(game=game).count()

    total = game.num_rounds or 14
    pct = round(min(cur - 1, total) / total * 100) if total else 0
    sched = {s["round"]: s for s in (game.schedule or [])}
    round_due = (sched.get(cur) or {}).get("due")

    teaching_team = []
    for un in game.faculty_members.values_list("username", flat=True):
        u = User.objects.filter(username=un).first()
        if u:
            teaching_team.append({"id": u.id, "username": u.username, "name": _name(u),
                                  "email": u.email or u.username, "role": u.role})

    needs = []
    unpaid = enrolled - paid
    if unpaid > 0:
        needs.append({"kind": "unpaid", "count": unpaid, "label": f"{unpaid} student{'s' if unpaid != 1 else ''} unpaid",
                      "detail": "Payment is still outstanding."})
    awaiting = enrolled - in_firms
    if awaiting > 0:
        needs.append({"kind": "unplaced", "count": awaiting, "label": f"{awaiting} awaiting placement",
                      "detail": "Not yet assigned to a firm."})
    not_submitted = [f["name"] for f in firms if f["members"] > 0 and not f["submitted_current_round"]]
    if decisions_open and not_submitted:
        needs.append({
            "kind": "not_submitted", "count": len(not_submitted),
            "label": f"{len(not_submitted)} firm{'s' if len(not_submitted) != 1 else ''} haven't submitted round {cur}",
            "detail": ", ".join(not_submitted),
        })

    return {
        "id": game.id,
        "name": game.name,
        "status": game.status,
        "deployed": game.deployed,
        "student_deployed": game.student_deployed,
        "stats": {
            "capacity": game.student_capacity or 0,
            "enrolled": enrolled,
            "in_firms": in_firms,
            "paid": paid,
            "unpaid": unpaid,
            "defended": defended,
        },
        "run": {
            "current_round": cur, "num_rounds": total, "pct": pct,
            "remaining": max(total - cur, 0), "round_due": round_due,
            "locked_rounds": game.locked_rounds or [], "decisions_open": decisions_open,
        },
        "firms": firms,
        "placed": in_firms,
        "needs_attention": needs,
        "teaching_team": teaching_team,
        "cohort": {
            "timezone": game.timezone,
            "start_date": game.start_date.isoformat() if game.start_date else None,
            "round_pacing_days": game.round_pacing_days,
            "price_per_student": float(game.price_per_student or 0),
            "advisor_rate_per_hour": float(game.advisor_rate_per_hour or 0),
        },
        "teaching_note": game.teaching_note or "",
    }
