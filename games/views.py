import logging

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from flexee_engine import api
from . import models, serializers
from .permissions import IsAdmin, IsFaculty, can_act_for_team, team_for_user
from .services import GameService

log = logging.getLogger("flexee.views")


# --- Reference / health (no game context) -----------------------------------------
@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return Response({
        "status": "ok",
        "engine_mode": api.ENGINE_MODE,     # "real" or "stub"
        "llm_provider": getattr(settings, "FLEXEE_LLM_PROVIDER", "offline"),
    })


@api_view(["GET"])
def reference_personas(request):
    """Student-safe pool: id + name only (never segment)."""
    return Response(api.persona_records())


@api_view(["GET"])
def reference_advisors(request):
    return Response(api.advisor_roster())


@api_view(["GET"])
def default_config(request):
    return Response(api.default_config())


# --- Game viewset -----------------------------------------------------------------
class GameViewSet(viewsets.ModelViewSet):
    serializer_class = serializers.GameSerializer

    def get_queryset(self):
        user = self.request.user
        qs = models.Game.objects.prefetch_related("teams").all()
        if user.is_admin:
            return qs  # admins see every sim (deployed or not)
        if user.is_faculty:
            # faculty only see sims that have been deployed to them
            return qs.filter(deployed=True).filter(
                Q(faculty=user) | Q(faculty_members=user)).distinct()
        return qs.filter(deployed=True, teams__users=user).distinct()

    def destroy(self, request, *args, **kwargs):
        # Deleting a sim is admin-only (cascades to teams, submissions, results…).
        # Faculty run the sims deployed to them; they don't manage the lifecycle.
        if not request.user.is_admin:
            raise PermissionDenied("Admin only.")
        return super().destroy(request, *args, **kwargs)

    def _service(self, game):
        return GameService(game)

    def _resolve_team(self, game, user, team_name=None):
        """Faculty must name a team; a student acts as their own."""
        if user.is_faculty:
            if not team_name:
                raise ValidationError({"team": "faculty must specify a team"})
            return get_object_or_404(models.Team, game=game, name=team_name)
        team = team_for_user(game, user)
        if team is None:
            raise PermissionDenied("you are not on a team in this game")
        if team_name and team_name != team.name:
            raise PermissionDenied("you can only act for your own team")
        return team

    # ---- A1: faculty setup (create) ----
    def create(self, request, *args, **kwargs):
        # Only admins create sims; faculty run the sims deployed to them.
        if not request.user.is_admin:
            raise PermissionDenied("Admin only.")
        s = serializers.FacultySetupSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = s.validated_data

        # v1 fixes rounds=14 and takeover=4 (calibrated config is tuned to them).
        NUM_ROUNDS, TAKEOVER = 14, 4
        config = api.default_config()
        config.update({"num_rounds": NUM_ROUNDS, "takeover_round": TAKEOVER})
        if data.get("scorecard_weights"):
            config["scorecard_weights"] = data["scorecard_weights"]
        if "engagement_cap_per_round" in data:
            config["engagement_cap_per_round"] = data["engagement_cap_per_round"]
        if data.get("crisis_inputs"):
            config["crisis_inputs"] = data["crisis_inputs"]
            config["crisis_inputs_source"] = "faculty"
        reveal_policy = data.get("reveal_policy") or config.get("reveal_policy", {})

        # resolve assigned faculty (must be faculty/admin); creator is always included.
        from accounts.models import User
        assigned = {request.user}
        for uname in data.get("faculty_usernames", []):
            u = User.objects.filter(username=uname).first()
            if u and u.is_faculty:
                assigned.add(u)

        with transaction.atomic():
            game = models.Game.objects.create(
                name=data["name"], num_rounds=NUM_ROUNDS, takeover_round=TAKEOVER,
                config_snapshot=config, reveal_policy=reveal_policy, faculty=request.user,
                status=models.GameStatus.SETUP,
                student_capacity=data.get("student_capacity", 0),
                start_date=data.get("start_date"),
                timezone=data.get("timezone", "UTC"),
                round_pacing_days=data.get("round_pacing_days", 7),
                # sims start as a draft; the admin deploys them to faculty.
                deployed=False,
            )
            game.faculty_members.set(assigned)
            for i in range(data["num_teams"]):
                models.Team.objects.create(game=game, name=f"Team {chr(65 + i)}")
        return Response(serializers.GameSerializer(game).data,
                        status=status.HTTP_201_CREATED)

    # ---- faculty roster: students across the faculty's sims ----
    @action(detail=False, methods=["get"], url_path="my-students", permission_classes=[IsFaculty])
    def my_students(self, request):
        rows = []
        for g in self.get_queryset().prefetch_related("teams__users"):
            for t in g.teams.all():
                for u in t.users.all():
                    rows.append({"username": u.username, "email": u.email,
                                 "sim": g.name, "sim_id": g.id, "team": t.name})
        rows.sort(key=lambda r: (r["sim"], r["team"], r["username"]))
        return Response(rows)

    # ---- faculty roster: co-faculty across the faculty's sims (incl. the viewer) ----
    @action(detail=False, methods=["get"], url_path="my-faculty", permission_classes=[IsFaculty])
    def my_faculty(self, request):
        from accounts.models import Role
        by_user = {}
        for g in self.get_queryset().prefetch_related("faculty_members"):
            members = set(g.faculty_members.all())
            if g.faculty:
                members.add(g.faculty)
            for u in members:
                if u.role != Role.FACULTY:   # co-faculty only (admins excluded)
                    continue
                e = by_user.setdefault(u.username, {"username": u.username, "email": u.email,
                                                    "role": u.role, "sims": []})
                if g.name not in e["sims"]:
                    e["sims"].append(g.name)
        # always include the viewing faculty, even with no co-faculty/sims
        me = request.user
        if me.role == Role.FACULTY:
            by_user.setdefault(me.username, {"username": me.username, "email": me.email,
                                             "role": me.role, "sims": []})
        return Response({"me": me.username, "faculty": sorted(by_user.values(),
                                                              key=lambda r: r["username"])})

    # ---- state summary (student or faculty) ----
    @action(detail=True, methods=["get"])
    def state(self, request, pk=None):
        game = self.get_object()
        svc = self._service(game)
        team = team_for_user(game, request.user)
        rnd = game.current_round
        return Response({
            "game": serializers.GameSerializer(game).data,
            "current_round": rnd,
            "my_team": team.name if team else None,
            "open_levers": svc.open_levers(rnd),
            "is_listening": rnd < game.takeover_round,
            # The team's decision so far: prior submissions accumulated in round order
            # (latest value of each field wins), so the Decide form can carry a team's
            # strategy forward instead of resetting to the inherited baseline every round.
            "my_last_decision": self._accumulated_decision(game, team, rnd) if team else None,
        })

    @staticmethod
    def _accumulated_decision(game, team, upto_round: int) -> dict:
        """Merge this team's submissions (round <= upto_round) so the latest set value of
        each lever field wins. Only fields the team ever set appear; the form overlays them
        on the inherited baseline."""
        merged: dict = {}
        subs = game.submissions.filter(team=team, round__lte=upto_round).order_by("round")
        for sub in subs:
            merged.update(sub.submitted_levers or {})
        return merged

    # ---- A2: which levers are open ----
    @action(detail=True, methods=["get"], url_path="open-levers")
    def open_levers(self, request, pk=None):
        game = self.get_object()
        rnd = int(request.query_params.get("round", game.current_round))
        return Response({"round": rnd, "open_levers": self._service(game).open_levers(rnd)})

    # ---- A2: submit a decision ----
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        game = self.get_object()
        s = serializers.SubmissionSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        team = self._resolve_team(game, request.user, s.validated_data.get("team"))
        svc = self._service(game)
        saved = svc.submit(s.validated_data["round"], team,
                           s.validated_data["levers"], user=request.user)
        return Response({"round": s.validated_data["round"], "team": team.name,
                         "submitted_levers": saved})

    # ---- A2/A7: close a round (faculty) ----
    @action(detail=True, methods=["post"], url_path="close-round",
            permission_classes=[IsFaculty])
    def close_round(self, request, pk=None):
        game = self.get_object()
        rnd = int(request.data.get("round", game.current_round))
        rec = self._service(game).close_round(rnd)
        return Response({"round": rnd, "result": rec, "current_round": game.current_round,
                         "locked_rounds": game.locked_rounds})

    # ---- deploy / unpublish a sim to faculty (admin only) ----
    @action(detail=True, methods=["post"], permission_classes=[IsAdmin])
    def deploy(self, request, pk=None):
        game = self.get_object()
        game.deployed = bool(request.data.get("deployed", True))
        game.save(update_fields=["deployed"])
        return Response({"id": game.id, "deployed": game.deployed})

    # ---- reopen a locked round (faculty) ----
    @action(detail=True, methods=["post"], url_path="reopen-round",
            permission_classes=[IsFaculty])
    def reopen_round(self, request, pk=None):
        game = self.get_object()
        rnd = int(request.data.get("round", game.current_round))
        self._service(game).reopen_round(rnd)
        game.refresh_from_db()
        return Response({"round": rnd, "reopened": True,
                         "locked_rounds": game.locked_rounds})

    # ---- A2: round result (scoped) ----
    @action(detail=True, methods=["get"])
    def results(self, request, pk=None):
        game = self.get_object()
        rnd = int(request.query_params.get("round", game.current_round))
        rec = self._service(game).result_for(rnd)
        if rec is None:
            return Response({"detail": "round not closed yet"},
                            status=status.HTTP_404_NOT_FOUND)
        if request.user.is_faculty:
            return Response(rec)
        team = team_for_user(game, request.user)
        if team is None:
            raise PermissionDenied("not on a team")
        return Response(self._student_result_view(rec, team.name))

    @staticmethod
    def _student_result_view(rec, team_name):
        return {
            "round": rec["round"],
            "segment_sizes": rec["segment_sizes"],
            "open_levers": rec["open_levers"],
            "firm": rec["firms"].get(team_name),
            "scorecard": rec["scorecard"]["firms"].get(team_name),
            "equity": rec["equity"].get(team_name),
            "attribute_state": rec["attribute_state"].get(team_name),
            "deal_conditioning": rec["deal_conditioning"].get(team_name),
            "promotion_depth": rec["promotion_depth"].get(team_name),
        }

    # ---- A3: focus group ----
    @action(detail=True, methods=["post"], url_path="focus-group")
    def focus_group(self, request, pk=None):
        game = self.get_object()
        s = serializers.FocusGroupRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        team = self._resolve_team(game, request.user, s.validated_data.get("team"))
        responses = self._service(game).focus_group(
            s.validated_data["round"], team, s.validated_data["persona_ids"],
            prompt=s.validated_data.get("prompt", ""), user=request.user)
        return Response({"round": s.validated_data["round"], "team": team.name,
                         "responses": responses})

    # ---- A4: advisor panel ----
    @action(detail=True, methods=["post"])
    def advisors(self, request, pk=None):
        game = self.get_object()
        s = serializers.AdvisorRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        team = self._resolve_team(game, request.user, s.validated_data.get("team"))
        stances = self._service(game).consult_advisors(
            s.validated_data["round"], team,
            s.validated_data["decision_under_review"], user=request.user)
        return Response({"round": s.validated_data["round"], "team": team.name,
                         "stances": stances})

    # ---- A5: scorecard ----
    @action(detail=True, methods=["get"])
    def scorecard(self, request, pk=None):
        game = self.get_object()
        report = self._service(game).scorecard()  # list of {round, teams:{...}}
        if request.user.is_faculty:
            return Response(report)
        team = team_for_user(game, request.user)
        if team is None:
            raise PermissionDenied("not on a team")
        # Student sees only their own entry per round (which already carries the
        # anonymized cohort best/avg/worst block).
        scoped = [
            {"round": r["round"], "own_report": r.get("teams", {}).get(team.name)}
            for r in report
        ]
        return Response(scoped)

    # ---- A6: board defense ----
    @action(detail=True, methods=["post"])
    def defense(self, request, pk=None):
        game = self.get_object()
        s = serializers.DefenseRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        team = self._resolve_team(game, request.user, s.validated_data.get("team"))
        combined = self._service(game).board_defense(team, s.validated_data["defense_text"])
        return Response({"team": team.name, "grade": combined})

    # ---- A7: faculty dashboard ----
    @action(detail=True, methods=["get"], permission_classes=[IsFaculty])
    def dashboard(self, request, pk=None):
        game = self.get_object()
        svc = self._service(game)
        submissions = serializers.RoundSubmissionSerializer(
            game.submissions.select_related("team").all(), many=True).data
        focus_groups = serializers.FocusGroupSessionSerializer(
            game.focus_groups.select_related("team").all(), many=True).data
        advisor_consults = serializers.AdvisorConsultationSerializer(
            game.advisor_consults.select_related("team").all(), many=True).data
        return Response({
            "game": serializers.GameSerializer(game).data,
            "scoreboard": svc.scorecard(),           # faculty holds the de-anon key (UI overlay)
            "engagement": svc.engagement(),          # saturated value used for the grade
            "engagement_counts": svc.engagement_counts(),  # raw action counts
            "locked_rounds": game.locked_rounds,
            "submissions": submissions,
            "focus_groups": focus_groups,
            "advisor_consults": advisor_consults,
            "reveal_policy": game.reveal_policy,
        })

    # ---- roster management (faculty) ----
    @action(detail=True, methods=["post"], url_path="assign-student",
            permission_classes=[IsFaculty])
    def assign_student(self, request, pk=None):
        game = self.get_object()
        team = get_object_or_404(models.Team, game=game, name=request.data.get("team"))
        from accounts.models import User
        user = get_object_or_404(User, username=request.data.get("username"))
        team.users.add(user)
        return Response({"team": team.name, "assigned": user.username})
