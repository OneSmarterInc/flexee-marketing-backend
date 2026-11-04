"""The six views of the Flexee family reporting contract v1, worked out from Marketing's own data.
The tools and columns are the contract's; how Marketing fills them:

- A course is a game; a quarter is a round (the column is still called "quarter"). Rounds before the
  takeover round are the listening period (warm_up).
- A course is "running" once its game is deployed to students, "finished" once it is complete,
  otherwise "setting_up". Practice games are left out of every view.
- Deadlines are the game's own: a round closes at 23:59 game time on its due date, so overdue means
  more than ten minutes past that moment (the last round is never closed by the scheduler, so it
  waits for a faculty member and is not overdue).
- report_closes has one row per round close (every team closes in one step), so firm_id is null.
- report_requests has no rows: Marketing has no accounting-method requests.
- report_ai_use comes from the AiCall log: ask_claude, focus_group, advisors and defense_grade.
- report_health: Marketing has no queue, so the job columns and php_version are null; the scheduler
  columns come from the sweep's heartbeat.

Operational facts only: no person's name or email, no team's work and no team results. Test
participants (test-N@mailinator.com, and provisioned test enrollments) are left out of every student
count.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Count

from games import ai_use, models
from games.deadlines import current_deadline, round_deadline, round_opens, schedule_entry
from games.scheduler import SWEEP_KEY
from .oauth import PRODUCT_NAME, active_connections

SIM = "marketing_management"
CONTRACT = "v1"
TEST_EMAIL = re.compile(r"^test-\d+@mailinator\.com$", re.IGNORECASE)


def _col(entries):
    return {"sim": "Which sim the row comes from.", **dict(entries)}


TOOLS = {
    "report_courses": {
        "title": "Courses",
        "description": "One row per course: its status, the round it is on, and how many teams and students it has.",
        "columns": _col([
            ("course_id", "The course's id."),
            ("course", "The course's name."),
            ("institution", "Always null: Marketing does not record an institution."),
            ("product", PRODUCT_NAME + "."),
            ("engine_version", 'Always "1": Marketing has one engine.'),
            ("status", "setting_up (not yet deployed to students), running or finished."),
            ("quarter_now", "The round open or closing now, else null."),
            ("quarters", "Rounds in the course, the listening period included."),
            ("firms", "Teams in the course."),
            ("students", "Students enrolled, leaving out test accounts."),
            ("students_confirmed", "Enrolled students who have accepted: in Marketing an enrollment exists only once the student has, so the same as students."),
            ("next_deadline_at", "The open round's decision deadline, else null."),
            ("created_at", "When the course was created."),
            ("deployed_at", "When the course was deployed to students, else null."),
        ]),
    },
    "report_quarters": {
        "title": "Quarters",
        "description": "One row per course round (a quarter in the contract): its state and deadline, when it opened and closed, how long the close took, and how many teams submitted.",
        "columns": _col([
            ("course_id", "The course's id."),
            ("course", "The course's name."),
            ("quarter", "The round's number."),
            ("warm_up", "True for the listening rounds before the takeover round, when nothing is decided."),
            ("state", "locked, open, processing or closed."),
            ("deadline_at", "The decision deadline (23:59 course time on the due date), where the course has a schedule."),
            ("deadline_extended", "True when a facilitator extended the deadline (or an earlier round's, which moved it)."),
            ("opened_at", "When the round opened, where known."),
            ("opened_after_deadline", "Always false."),
            ("overdue", "True when the round is open more than 10 minutes past its deadline, which happens only if nothing is closing rounds. The last round is never closed automatically, so it is never overdue."),
            ("close_started_at", "When the close began, where recorded."),
            ("closed_at", "When the close finished, where recorded."),
            ("close_minutes", "Minutes the close took, where recorded."),
            ("firms", "Teams in the course."),
            ("firms_submitted", "Teams that submitted: counted live for the open round, and as recorded at the close for closed ones."),
            ("firms_carried_forward", "Teams that did not submit and were closed on the cohort average, as recorded at the close."),
            ("explanations_required", "Always false."),
            ("explanations_submitted", "Always null."),
        ]),
    },
    "report_closes": {
        "title": "Quarter closes",
        "description": "One row per round close in the last 90 days. Marketing closes every team in one step.",
        "columns": _col([
            ("close_id", "The close's id."),
            ("course_id", "The course's id."),
            ("course", "The course's name, kept after the course is deleted."),
            ("quarter", "The round closed."),
            ("firm_id", "Always null: a close covers every team at once."),
            ("status", "succeeded or failed."),
            ("error_code", "other for a failed close, else null."),
            ("source", "deadline (the scheduled sweep or a page load after the deadline) or faculty (the Advance button)."),
            ("started_at", "When it started."),
            ("completed_at", "When it finished."),
            ("seconds", "Seconds it took."),
        ]),
    },
    "report_requests": {
        "title": "Accounting-method requests",
        "description": "Marketing has no accounting-method requests, so this returns no rows.",
        "columns": _col([
            ("request_id", "The request's id."), ("course_id", "The course's id."),
            ("course", "The course's name."), ("quarter", "The round."),
            ("status", "pending, approved or declined."), ("filed_at", "When it was filed."),
            ("decided_at", "When it was decided."), ("days_waiting", "Days waiting."),
            ("has_reason", "Whether it had a reason."), ("review", "Claude's review."),
        ]),
    },
    "report_ai_use": {
        "title": "AI use",
        "description": "One row per course, month and AI feature in the last 12 months: calls, failures, sentences the checks dropped, tokens and cost.",
        "columns": _col([
            ("course_id", "The course's id."),
            ("course", "The course's name."),
            ("month", "The month, as YYYY-MM."),
            ("feature", "ask_claude, focus_group, advisors or defense_grade."),
            ("calls", "Uses of the feature that reached Claude."),
            ("failures", "Calls that failed or came back unusable."),
            ("sentences_dropped", "Sentences the checks removed."),
            ("input_tokens", "Input tokens."),
            ("output_tokens", "Output tokens."),
            ("cost_usd", "Cost in US dollars."),
        ]),
    },
    "report_health": {
        "title": "Site health",
        "description": "One row for the site: the deployed commit, the database, the scheduler, overdue rounds, failed closes and connections.",
        "columns": _col([
            ("contract", "The reporting contract's version."),
            ("checked_at", "When these figures were worked out."),
            ("environment", "The app environment."),
            ("commit", "The deployed commit, else null."),
            ("php_version", "Always null: Marketing runs on Python."),
            ("database_version", 'The database server\'s version, e.g. "PostgreSQL 14.19".'),
            ("migrations_pending", "Database migrations not yet applied."),
            ("jobs_waiting", "Always null: Marketing has no queue."),
            ("oldest_job_minutes", "Always null."),
            ("failed_jobs_7d", "Always null."),
            ("scheduler_last_run_at", "When the round sweep last ran, else null."),
            ("scheduler_ok", "True when the sweep ran in the last 10 minutes."),
            ("courses_running", "Courses running now."),
            ("quarters_overdue", "Open rounds more than 10 minutes past their deadline."),
            ("closes_failed", "Failed closes in the last 7 days."),
            ("requests_waiting", "Always 0."),
            ("ai_enabled", "True when AI features can run on this site."),
            ("ai_spent_this_month_usd", "AI spend this month, in US dollars."),
            ("ai_ceiling_usd", "The site's monthly AI ceiling, in US dollars."),
            ("certificate_expires_at", "Always null: the host renews the certificate."),
            ("certificate_days_left", "Always null."),
            ("connections_active", "Active reporting connections."),
        ]),
    },
}


def iso(d: datetime | None):
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def _now():
    return datetime.now(timezone.utc)


def _games():
    return models.Game.objects.filter(is_practice=False).order_by("created_at")


def _status(g: models.Game) -> str:
    if g.status == models.GameStatus.COMPLETE:
        return "finished"
    return "running" if g.student_deployed else "setting_up"


def _overdue(g: models.Game, now: datetime) -> bool:
    """Running, not on the last round (a faculty member closes that one), and more than ten
    minutes past the open round's deadline."""
    if _status(g) != "running" or g.current_round >= g.num_rounds:
        return False
    deadline = current_deadline(g)
    return bool(deadline and now - deadline > timedelta(minutes=10))


def _students() -> dict:
    """Enrolled students per game, leaving out test accounts."""
    out: dict = {}
    for e in models.Enrollment.objects.filter(is_test=False).select_related("user"):
        if TEST_EMAIL.match(e.user.email or ""):
            continue
        out[e.game_id] = out.get(e.game_id, 0) + 1
    return out


# ------------------------------------------------------------------------------- views
def courses() -> list[dict]:
    teams = dict(models.Team.objects.values_list("game_id").annotate(n=Count("id")))
    students = _students()
    rows = []
    for g in _games():
        status = _status(g)
        rows.append({
            "sim": SIM, "course_id": g.id, "course": g.name, "institution": None,
            "product": PRODUCT_NAME, "engine_version": "1", "status": status,
            "quarter_now": min(g.current_round, g.num_rounds) if status == "running" else None,
            "quarters": g.num_rounds, "firms": teams.get(g.id, 0),
            "students": students.get(g.id, 0), "students_confirmed": students.get(g.id, 0),
            "next_deadline_at": iso(current_deadline(g)) if status == "running" else None,
            "created_at": iso(g.created_at), "deployed_at": iso(g.student_deployed_at),
        })
    return rows


def quarters() -> list[dict]:
    now = _now()
    teams = dict(models.Team.objects.values_list("game_id").annotate(n=Count("id")))
    logs = {}
    for l in models.RoundCloseLog.objects.filter(status="succeeded", practice=False).order_by("completed_at"):
        logs[(l.game_id_ref, l.round)] = l            # the latest successful close of a round wins
    live: dict = {}
    for gid, rnd, n in (models.RoundSubmission.objects.values_list("game_id", "round")
                        .annotate(n=Count("team_id", distinct=True))):
        live[(gid, rnd)] = n
    rows = []
    for g in _games():
        status = _status(g)
        running, finished = status == "running", status == "finished"
        for r in range(1, g.num_rounds + 1):
            if finished or r < g.current_round:
                state = "closed"
            elif r == g.current_round and running:
                claimed = g.close_claimed_at and now - g.close_claimed_at < timedelta(minutes=15)
                state = "processing" if claimed else "open"
            else:
                state = "locked"
            current = r == g.current_round and running
            log = logs.get((g.id, r))
            entry = schedule_entry(g, r) or {}
            rows.append({
                "sim": SIM, "course_id": g.id, "course": g.name, "quarter": r,
                "warm_up": r < g.takeover_round, "state": state,
                "deadline_at": iso(round_deadline(g, r)),
                "deadline_extended": bool(entry.get("extended")),
                "opened_at": iso(round_opens(g, r) if current else ((log.opened_at if log else None) or round_opens(g, r))),
                "opened_after_deadline": False,
                "overdue": _overdue(g, now) if current else False,
                "close_started_at": iso(log.started_at) if log else None,
                "closed_at": iso(log.completed_at) if log else None,
                "close_minutes": round(log.seconds / 60) if log else None,
                "firms": teams.get(g.id, 0),
                "firms_submitted": live.get((g.id, r), 0) if current else (log.firms_submitted if log else None),
                "firms_carried_forward": (log.firms_carried_forward if log else (0 if current else None)),
                "explanations_required": False, "explanations_submitted": None,
            })
    return rows


def closes() -> list[dict]:
    since = _now() - timedelta(days=90)
    return [{
        "sim": SIM, "close_id": l.id, "course_id": l.game_id_ref, "course": l.course, "quarter": l.round,
        "firm_id": None, "status": l.status, "error_code": l.error_code, "source": l.source,
        "started_at": iso(l.started_at), "completed_at": iso(l.completed_at), "seconds": round(l.seconds, 1),
    } for l in models.RoundCloseLog.objects.filter(completed_at__gte=since, practice=False).order_by("completed_at")]


def ai_use_rows() -> list[dict]:
    start = ai_use.month_start(_now())
    y, m = start.year, start.month - 11              # the first of the month, eleven months back
    if m <= 0:
        y, m = y - 1, m + 12
    since = datetime(y, m, 1, tzinfo=timezone.utc)
    rows: dict = {}
    for c in models.AiCall.objects.filter(created_at__gte=since, practice=False):
        month = c.created_at.astimezone(timezone.utc).strftime("%Y-%m")
        key = (c.game_id_ref, month, c.feature)
        row = rows.setdefault(key, {
            "sim": SIM, "course_id": c.game_id_ref, "course": c.course, "month": month, "feature": c.feature,
            "calls": 0, "failures": 0, "sentences_dropped": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
        row["calls"] += 1
        if c.outcome == "error":
            row["failures"] += 1
        row["sentences_dropped"] += c.sentences_dropped
        row["input_tokens"] += c.input_tokens
        row["output_tokens"] += c.output_tokens
        row["cost_usd"] += c.cost_usd
    out = [{**r, "cost_usd": round(r["cost_usd"], 2)} for r in rows.values()]
    return sorted(out, key=lambda r: (r["month"], r["course"] or "", r["feature"]))


def _database_version():
    try:
        with connection.cursor() as cur:
            if connection.vendor == "postgresql":
                cur.execute("SHOW server_version")
                return f"PostgreSQL {cur.fetchone()[0].split(' ')[0]}"
            if connection.vendor == "sqlite":
                cur.execute("select sqlite_version()")
                return f"SQLite {cur.fetchone()[0]}"
            return connection.vendor
    except Exception:
        return None


def _migrations_pending():
    try:
        executor = MigrationExecutor(connection)
        return len(executor.migration_plan(executor.loader.graph.leaf_nodes()))
    except Exception:
        return None


def health() -> dict:
    now = _now()
    games = list(_games())
    running = [g for g in games if _status(g) == "running"]
    state = models.SchedulerState.objects.filter(key=SWEEP_KEY).first()
    last = state.last_run_at if state else None
    failed = models.RoundCloseLog.objects.filter(status="failed", practice=False,
                                                 completed_at__gt=now - timedelta(days=7)).count()
    return {
        "sim": SIM, "contract": CONTRACT, "checked_at": iso(now),
        "environment": "development" if settings.DEBUG else os.environ.get("DJANGO_ENV", "production"),
        "commit": getattr(settings, "GIT_COMMIT", "") or None,
        "php_version": None, "database_version": _database_version(),
        "migrations_pending": _migrations_pending(),
        "jobs_waiting": None, "oldest_job_minutes": None, "failed_jobs_7d": None,
        "scheduler_last_run_at": iso(last),
        "scheduler_ok": bool(last and now - last <= timedelta(minutes=10)),
        "courses_running": len(running),
        "quarters_overdue": sum(1 for g in running if _overdue(g, now)),
        "closes_failed": failed, "requests_waiting": 0,
        "ai_enabled": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "ai_spent_this_month_usd": ai_use.site_spend(now),
        "ai_ceiling_usd": ai_use.ceiling_usd(),
        "certificate_expires_at": None, "certificate_days_left": None,
        "connections_active": active_connections(),
    }


def run(name: str) -> list[dict]:
    if name == "report_courses":
        return courses()
    if name == "report_quarters":
        return quarters()
    if name == "report_closes":
        return closes()
    if name == "report_requests":
        return []
    if name == "report_ai_use":
        return ai_use_rows()
    if name == "report_health":
        return [health()]
    raise ValueError(f"Unknown view {name}")
