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

    # Admin-created sims stay hidden from faculty until deployed. Default True so existing
    # rows and faculty-created sims are visible; the create flow sets False for admin.
    deployed = models.BooleanField(default=True)

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

    class Meta:
        unique_together = ("game", "name")
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} @ {self.game_id}"


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
