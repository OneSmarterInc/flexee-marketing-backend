"""Persistence schema — Part B of the UI Contracts & Persistence doc.

Storage-agnostic there; here it's relational. The RoundSubmission is the SOURCE OF
TRUTH; RoundResult is derived (recomputed from submissions whenever a round closes)
and stored for speed and audit.
"""

from django.conf import settings
from django.db import models


class GameStatus(models.TextChoices):
    SETUP = "setup", "Setup"
    RUNNING = "running", "Running"
    COMPLETE = "complete", "Complete"


class Game(models.Model):
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=16, choices=GameStatus.choices,
                              default=GameStatus.SETUP)
    current_round = models.PositiveIntegerField(default=1)
    num_rounds = models.PositiveIntegerField(default=14)
    takeover_round = models.PositiveIntegerField(default=4)

    # Full config snapshot (faculty_config + params/equity/promotion/crisis in force) so a
    # game is reproducible regardless of later default changes.
    config_snapshot = models.JSONField(default=dict)
    reveal_policy = models.JSONField(default=dict)

    # Rounds that are locked (frozen) after close. Faculty can reopen (LOCKED decision:
    # lock on close; faculty can reopen; missing submission = baseline, no extra penalty).
    locked_rounds = models.JSONField(default=list, blank=True)

    faculty = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="games_owned")
    # Faculty assigned to run this sim (the creator is added automatically).
    faculty_members = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True,
                                             related_name="faculty_sims")

    # Cohort / scheduling metadata (rounds=14 and takeover=4 are fixed for v1).
    student_capacity = models.PositiveIntegerField(default=0)   # 0 = unset / unlimited
    start_date = models.DateField(null=True, blank=True)
    timezone = models.CharField(max_length=64, default="UTC")
    round_pacing_days = models.PositiveIntegerField(default=7)

    # Two-stage deployment:
    #   1. Faculty deployment (admin) — `deployed`: the admin releases the sim to its faculty.
    #   2. Student deployment (faculty) — `student_deployed`: the faculty opens it to students.
    # A sim can only be student-deployed once it is faculty-deployed. Students see it only
    # when student-deployed; faculty see it once faculty-deployed.
    deployed = models.BooleanField(default=True)
    deployed_at = models.DateTimeField(null=True, blank=True)          # faculty deployment
    student_deployed = models.BooleanField(default=False)
    student_deployed_at = models.DateTimeField(null=True, blank=True)  # student deployment

    # Per-round schedule [{round, opens, due}] (ISO dates), built at creation from
    # start_date + round_pacing_days. Faculty can extend a round, shifting later rounds.
    schedule = models.JSONField(default=list, blank=True)

    # Self-registration link token: students who open /join/<token> enroll in this sim.
    join_token = models.CharField(max_length=64, blank=True, default="")

    # Faculty's private teaching note for this cohort (debrief prompts, reminders…).
    teaching_note = models.TextField(blank=True, default="")

    # Cohort shape: the expected team size.
    students_per_team = models.PositiveIntegerField(default=4)

    # Billing: real-money workspace charges. price_per_student is the seat price charged
    # per enrolled student; seats_paid is how many of those seats an instructor has marked
    # paid (drives "Received"). advisor_rate_per_hour prices advisor chat per started hour
    # (0 = advisors included, no charge). Advisor hours are counted from actual records.
    price_per_student = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    seats_paid = models.PositiveIntegerField(default=0)
    advisor_rate_per_hour = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    # Practice game: a no-deadline copy of a live game for a faculty member to try the
    # sim against scripted reference firms. Left out of billing, the scheduler, reminders
    # and the reporting connector. `practice_of` points at the game it was copied from.
    is_practice = models.BooleanField(default=False)
    practice_of = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="practice_runs")

    # Per-game switch for every AI feature (Ask Claude, and the voice layer behind focus
    # groups, advisors and the written-defense grade). On by default.
    ai_enabled = models.BooleanField(default=True)

    # Which round the day-before reminder email has already gone out for (once per round).
    reminder_sent_for_round = models.PositiveIntegerField(null=True, blank=True)

    # One claim per close: set by an atomic UPDATE keyed on the round being closed, taken
    # by every close path (Advance, the scheduled sweep). A claim older than 15 minutes is
    # treated as dead.
    close_claimed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} [{self.status}] r{self.current_round}/{self.num_rounds}"


class Team(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=100)
    members = models.JSONField(default=list, blank=True)  # display roster
    users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True,
                                   related_name="teams")
    # A reference firm in a practice game plays a scripted archetype (see games/practice.py);
    # nobody can decide for it. Empty for an ordinary team.
    reference_path = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        unique_together = ("game", "name")
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} @ {self.game_id}"


class Enrollment(models.Model):
    """A student enrolled in a sim. Enrollment is separate from firm placement: an
    enrolled student may be awaiting placement (no team) or placed (on a Team)."""

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="enrollments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="enrollments")
    is_test = models.BooleanField(default=False)  # provisioned test account
    paid = models.BooleanField(default=False)     # seat marked paid by faculty
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("game", "user")
        ordering = ["created_at"]


class InviteStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"


class Invitation(models.Model):
    """An emailed / link invite for a student to join a sim. Accepting one enrolls the
    student. The self-registration link uses the game's join_token instead of a row here."""

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    token = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=16, choices=InviteStatus.choices, default=InviteStatus.PENDING)
    emailed = models.BooleanField(default=False)
    emailed_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="+")
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("game", "email")
        ordering = ["created_at"]


class RoundSubmission(models.Model):
    """Source of truth. Only the OPEN-lever fields the team set are stored."""

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="submissions")
    round = models.PositiveIntegerField()
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="submissions")
    submitted_levers = models.JSONField(default=dict)
    submitted_at = models.DateTimeField(auto_now=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                     null=True, blank=True)

    class Meta:
        unique_together = ("game", "round", "team")
        ordering = ["round", "team_id"]

    def __str__(self) -> str:
        return f"sub g{self.game_id} r{self.round} {self.team.name}"


class RoundResult(models.Model):
    """Derived from submissions on round-close; overwritten on recompute."""

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="results")
    round = models.PositiveIntegerField()

    # Faithful round-trip of the engine's record. The store returns exactly what was
    # stored, so any engine-specific keys survive and downstream reads are identical.
    raw = models.JSONField(default=dict, blank=True)

    # Denormalized columns (populated from `raw`) for querying, admin, and audit.
    firms = models.JSONField(default=dict)
    scorecard = models.JSONField(default=dict)
    equity = models.JSONField(default=dict)
    attribute_state = models.JSONField(default=dict)
    deal_conditioning = models.JSONField(default=dict)
    promotion_depth = models.JSONField(default=dict)
    damage = models.JSONField(default=dict)
    segment_sizes = models.JSONField(default=dict)
    open_levers = models.JSONField(default=list)
    effective_decisions = models.JSONField(default=dict)

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("game", "round")
        ordering = ["round"]

    def __str__(self) -> str:
        return f"result g{self.game_id} r{self.round}"


class FocusGroupSession(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="focus_groups")
    round = models.PositiveIntegerField()
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="focus_groups")
    persona_ids = models.JSONField(default=list)
    prompt = models.TextField(blank=True, default="")
    responses = models.JSONField(default=list)  # student-safe records only
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class PersonaRelationship(models.Model):
    """Carried AI state cache; authoritative value is re-derivable from history."""

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="relationships")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="relationships")
    persona_id = models.CharField(max_length=64)
    relationship = models.FloatField(default=0.5)
    updated_round = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("game", "team", "persona_id")


class AdvisorConsultation(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="advisor_consults")
    round = models.PositiveIntegerField()
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="advisor_consults")
    decision_under_review = models.JSONField(default=dict)
    stances = models.JSONField(default=list)  # six public stances
    usage_record = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class DefenseSubmission(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="defenses")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="defenses")
    defense_text = models.TextField(blank=True, default="")
    deterministic_grade = models.JSONField(default=dict)
    prose_grade = models.JSONField(default=dict, null=True, blank=True)
    combined = models.JSONField(default=dict)
    graded_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("game", "team")


class GradeCache(models.Model):
    """Backs the written-defense grader's cache so a regrade after restart is identical."""

    input_hash = models.CharField(max_length=128, primary_key=True)
    prose_dimensions = models.JSONField(default=dict)
    prose_overall = models.FloatField(default=0.0)
    justification = models.TextField(blank=True, default="")


class ResearchCommission(models.Model):
    """A commissioned market-research study (Group C). Stated studies are cheap and carry the
    say-do bias; behavioral studies cost more and render the truth. Cost is debited from the
    team's research budget. The rendered sheet is re-derivable from state, so only the
    commission (what was bought, at what cost) is stored."""

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="research")
    round = models.PositiveIntegerField()
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="research")
    study = models.CharField(max_length=64)
    mode = models.CharField(max_length=16)  # "stated" | "behavioral"
    cost = models.FloatField(default=0.0)
    sheet = models.JSONField(default=dict)   # rendered at commission time
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class Forecast(models.Model):
    """A team's per-round prediction of a key outcome (Group C forecast loop). Accuracy is
    scored when the round closes and folds into the board-defense grade."""

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="forecasts")
    round = models.PositiveIntegerField()
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="forecasts")
    metric = models.CharField(max_length=32, default="own_share")   # own_share | growth_share
    predicted = models.FloatField()
    actual = models.FloatField(null=True, blank=True)
    accuracy = models.FloatField(null=True, blank=True)  # 0..1, scored on close
    note = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True)

    class Meta:
        unique_together = ("game", "round", "team")
        ordering = ["round"]


# ---------------------------------------------------------------------------------------
# History that must outlive a game. These carry the game's id and name as plain columns
# (no foreign key) on purpose: deleting a game must not erase its close history, scheduler
# heartbeat or AI use, which the reporting connector reads.
# ---------------------------------------------------------------------------------------
class RoundCloseLog(models.Model):
    """One round close, recorded when it ends (every team closes in one step)."""

    SOURCES = (("deadline", "deadline"), ("faculty", "faculty"))
    STATUSES = (("succeeded", "succeeded"), ("failed", "failed"))

    game_id_ref = models.PositiveIntegerField(db_index=True)
    course = models.CharField(max_length=200)            # the game's name, copied in
    round = models.PositiveIntegerField()
    source = models.CharField(max_length=16, choices=SOURCES)
    status = models.CharField(max_length=16, choices=STATUSES)
    error_code = models.CharField(max_length=32, null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    deadline_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(db_index=True)
    seconds = models.FloatField(default=0.0)
    firms = models.PositiveIntegerField(default=0)
    firms_submitted = models.PositiveIntegerField(default=0)
    firms_carried_forward = models.PositiveIntegerField(default=0)
    # Kept (so the site-wide spend and history stay whole) but left out of reporting. A flag
    # rather than a lookup on the game, because the game may be deleted afterwards.
    practice = models.BooleanField(default=False)

    class Meta:
        ordering = ["completed_at"]
        indexes = [models.Index(fields=["game_id_ref", "round"])]


class SchedulerState(models.Model):
    """When the scheduled sweep last ran (key "round-sweep")."""

    key = models.CharField(max_length=64, primary_key=True)
    last_run_at = models.DateTimeField()


class AiCall(models.Model):
    """One call to Claude on behalf of a game: which feature, how it went, tokens and cost."""

    OUTCOMES = (("ok", "ok"), ("error", "error"))

    game_id_ref = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    course = models.CharField(max_length=200, null=True, blank=True)
    feature = models.CharField(max_length=32)   # ask_claude | focus_group | advisors | defense_grade
    outcome = models.CharField(max_length=8, choices=OUTCOMES, default="ok")
    sentences_dropped = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.FloatField(default=0.0)
    practice = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]


class AskQuestion(models.Model):
    """A faculty member's question to Ask Claude about one game, kept 90 days. A question Claude
    could not answer is kept too (status "failed") and does not count against the allowance."""

    STATUSES = (("answered", "answered"), ("failed", "failed"))

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="ask_questions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                             null=True, blank=True)
    conversation_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    page = models.CharField(max_length=64, blank=True, default="")
    context = models.JSONField(default=dict, blank=True)       # what was on screen
    question = models.TextField()
    answer = models.TextField(blank=True, default="")
    lookups = models.JSONField(default=list, blank=True)       # what Claude looked up
    dropped = models.JSONField(default=list, blank=True)       # sentences the figure check removed
    figures_checked = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=STATUSES, default="answered")
    model = models.CharField(max_length=64, blank=True, default="")
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
