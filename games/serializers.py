from rest_framework import serializers

from flexee_engine import api
from . import models


class TeamSerializer(serializers.ModelSerializer):
    user_ids = serializers.PrimaryKeyRelatedField(
        source="users", many=True, read_only=True
    )

    class Meta:
        model = models.Team
        fields = ("id", "name", "members", "user_ids")


class GameSerializer(serializers.ModelSerializer):
    teams = TeamSerializer(many=True, read_only=True)
    faculty_members = serializers.SerializerMethodField()

    class Meta:
        model = models.Game
        fields = ("id", "name", "created_at", "status", "current_round", "num_rounds",
                  "takeover_round", "config_snapshot", "reveal_policy", "teams",
                  "locked_rounds", "student_capacity", "start_date", "timezone",
                  "round_pacing_days", "faculty_members", "deployed", "deployed_at",
                  "student_deployed", "student_deployed_at", "schedule",
                  "students_per_team", "price_per_student",
                  "seats_paid", "advisor_rate_per_hour")
        read_only_fields = ("created_at", "status", "current_round")

    def get_faculty_members(self, obj):
        return list(obj.faculty_members.values_list("username", flat=True))


# --- Faculty setup (A1) -----------------------------------------------------------
# v1 fixes rounds=14 and takeover=4 (the calibrated config is tuned to them), so setup
# collects the cohort shape instead: team count, capacity, schedule, and faculty.
class FacultySetupSerializer(serializers.Serializer):
    name = serializers.CharField()
    num_teams = serializers.IntegerField(min_value=1, max_value=26)
    student_capacity = serializers.IntegerField(required=False, default=0, min_value=0)
    start_date = serializers.DateField(required=False, allow_null=True)
    # No default: every deadline is on the game's own calendar, so creating a game needs the
    # time zone the class meets in (a real IANA name).
    timezone = serializers.CharField(required=True, allow_blank=True)
    round_pacing_days = serializers.IntegerField(required=False, default=7, min_value=1)
    students_per_team = serializers.IntegerField(required=False, default=4, min_value=1)
    price_per_student = serializers.DecimalField(max_digits=8, decimal_places=2,
                                                 required=False, default=0, min_value=0)
    advisor_rate_per_hour = serializers.DecimalField(max_digits=8, decimal_places=2,
                                                     required=False, default=0, min_value=0)
    faculty_usernames = serializers.ListField(
        child=serializers.CharField(), required=False, default=list)
    # faculty config (A1) — all default to the calibrated values, so omitting them
    # reproduces the shipped config byte-for-byte.
    num_rounds = serializers.IntegerField(required=False, min_value=10, max_value=14)
    takeover_round = serializers.IntegerField(required=False, min_value=1, max_value=14)
    crisis_round = serializers.IntegerField(required=False, min_value=1, max_value=14)
    crisis_damage_base = serializers.FloatField(required=False, min_value=0)
    lever_open_round = serializers.DictField(required=False)
    scorecard_weights = serializers.DictField(required=False)
    engagement_cap_per_round = serializers.IntegerField(required=False, min_value=1)
    crisis_inputs = serializers.DictField(required=False)
    reveal_policy = serializers.DictField(required=False)

    def validate_timezone(self, value):
        from .deadlines import validate_timezone
        try:
            return validate_timezone(value)
        except ValueError as e:
            raise serializers.ValidationError(str(e))


# --- Student round submission (A2) ------------------------------------------------
class SubmissionSerializer(serializers.Serializer):
    round = serializers.IntegerField(min_value=1)
    team = serializers.CharField(required=False)  # faculty may specify; students inferred
    levers = serializers.DictField()


# --- Focus group (A3) -------------------------------------------------------------
class FocusGroupRequestSerializer(serializers.Serializer):
    round = serializers.IntegerField(min_value=1)
    team = serializers.CharField(required=False)
    persona_ids = serializers.ListField(child=serializers.CharField(),
                                        min_length=1, max_length=8)
    prompt = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_persona_ids(self, value):
        if len(value) < 5:
            raise serializers.ValidationError("pick 5–8 personas")
        valid = set(api.persona_by_id().keys())
        bad = [p for p in value if p not in valid]
        if bad:
            raise serializers.ValidationError(f"unknown personas: {bad}")
        return value


# --- Advisor panel (A4) -----------------------------------------------------------
class AdvisorRequestSerializer(serializers.Serializer):
    round = serializers.IntegerField(min_value=1)
    team = serializers.CharField(required=False)
    decision_under_review = serializers.DictField()


# --- Board defense (A6) -----------------------------------------------------------
class DefenseRequestSerializer(serializers.Serializer):
    team = serializers.CharField(required=False)
    defense_text = serializers.CharField()


# --- Read serializers -------------------------------------------------------------
class FocusGroupSessionSerializer(serializers.ModelSerializer):
    team = serializers.CharField(source="team.name", read_only=True)

    class Meta:
        model = models.FocusGroupSession
        fields = ("id", "round", "team", "persona_ids", "prompt", "responses", "created_at")


class AdvisorConsultationSerializer(serializers.ModelSerializer):
    team = serializers.CharField(source="team.name", read_only=True)

    class Meta:
        model = models.AdvisorConsultation
        fields = ("id", "round", "team", "decision_under_review", "stances",
                  "usage_record", "created_at")


class RoundResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.RoundResult
        fields = ("round", "firms", "scorecard", "equity", "attribute_state",
                  "deal_conditioning", "promotion_depth", "damage", "segment_sizes",
                  "open_levers", "effective_decisions")


class RoundSubmissionSerializer(serializers.ModelSerializer):
    team = serializers.CharField(source="team.name", read_only=True)

    class Meta:
        model = models.RoundSubmission
        fields = ("round", "team", "submitted_levers", "submitted_at")
