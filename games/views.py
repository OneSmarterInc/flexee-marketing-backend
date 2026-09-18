import logging

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone as dj_timezone
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


# --- Public: student self-registration via invite / join link ---------------------
@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def join(request, token):
    from rest_framework.authtoken.models import Token
    from accounts.serializers import UserSerializer
    from .setup import resolve_join, accept_join

    game, inv = resolve_join(token)
    if not game:
        return Response({"detail": "This invite link is invalid or has expired."},
                        status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response({
            "game_name": game.name,
            "email": inv.email if inv else None,
            "email_locked": bool(inv),
        })

    try:
        user, _ = accept_join(
            token,
            email=request.data.get("email", ""),
            password=request.data.get("password", ""),
            first=request.data.get("first_name", ""),
            last=request.data.get("last_name", ""),
        )
    except ValueError as e:
        raise ValidationError({"detail": str(e)})
    tok, _ = Token.objects.get_or_create(user=user)
    return Response({"token": tok.key, "user": UserSerializer(user).data,
                     "game_id": game.id}, status=status.HTTP_201_CREATED)


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
        # students only see a sim once faculty have deployed it to students
        return qs.filter(student_deployed=True, teams__users=user).distinct()

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

        # Faculty config (A1). Each knob defaults to the calibrated value, so a sim created
        # with the defaults reproduces the shipped config exactly (goldens hold).
        config = api.default_config()
        NUM_ROUNDS = int(data.get("num_rounds", config.get("num_rounds", 14)))
        TAKEOVER = int(data.get("takeover_round", config.get("takeover_round", 4)))
        config.update({"num_rounds": NUM_ROUNDS, "takeover_round": TAKEOVER})
        if "crisis_round" in data:
            config["crisis_round"] = int(data["crisis_round"])
        if "crisis_damage_base" in data:
            config["crisis_damage_base"] = float(data["crisis_damage_base"])
        if data.get("lever_open_round"):
            lor = {**config.get("lever_open_round", {}), **data["lever_open_round"]}
            config["lever_open_round"] = lor
            # keep the combined lever_schedule (base levers + Group B extras) consistent
            config["lever_schedule"] = {**lor, **config.get("extra_lever_open_round", {})}
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

        from .schedule import build_schedule
        pacing = data.get("round_pacing_days", 7)
        # No start date given → the sim starts today, so it always gets a real schedule
        # instead of sitting undated until faculty sets one.
        start_date = data.get("start_date") or dj_timezone.localdate()
        schedule = build_schedule(start_date, pacing, NUM_ROUNDS)

        with transaction.atomic():
            game = models.Game.objects.create(
                name=data["name"], num_rounds=NUM_ROUNDS, takeover_round=TAKEOVER,
                config_snapshot=config, reveal_policy=reveal_policy, faculty=request.user,
                status=models.GameStatus.SETUP,
                student_capacity=data.get("student_capacity", 0),
                start_date=start_date,
                timezone=data.get("timezone", "UTC"),
                round_pacing_days=pacing,
                schedule=schedule,
                students_per_team=data.get("students_per_team", 4),
                price_per_student=data.get("price_per_student", 0),
                advisor_rate_per_hour=data.get("advisor_rate_per_hour", 0),
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

    @action(detail=False, methods=["get"], permission_classes=[IsAdmin])
    def billing(self, request):
        from .billing import compute_billing
        return Response(compute_billing(models.Game.objects.all()))

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

    # ---- Financials (read-only reporting overlay) ----
    @action(detail=True, methods=["get"])
    def financials(self, request, pk=None):
        game = self.get_object()
        svc = self._service(game)
        team = team_for_user(game, request.user)
        return Response({
            "my_team": team.name if team else None,
            "financials": svc.financials(),
        })

    # ---- Market research (Group C) ----
    @action(detail=True, methods=["get", "post"])
    def research(self, request, pk=None):
        game = self.get_object()
        svc = self._service(game)
        team = team_for_user(game, request.user)
        if request.method == "POST":
            if team is None:
                raise ValidationError({"detail": "only a team member can commission research"})
            r = svc.commission_research(team, request.data.get("study"), request.data.get("mode"),
                                        user=request.user)
            return Response(r)
        return Response({
            "catalog": api.research_catalog(),
            "budget": svc.research_budget(team) if team else None,
            "history": svc.research_history(team) if team else [],
        })

    # ---- Forecast loop (Group C) ----
    @action(detail=True, methods=["get", "post"])
    def forecast(self, request, pk=None):
        game = self.get_object()
        svc = self._service(game)
        team = team_for_user(game, request.user)
        if request.method == "POST":
            if team is None:
                raise ValidationError({"detail": "only a team member can forecast"})
            rnd = int(request.data.get("round", game.current_round))
            r = svc.submit_forecast(rnd, team, request.data.get("metric", "own_share"),
                                    request.data.get("predicted"), note=request.data.get("note", ""),
                                    user=request.user)
            return Response(r)
        return Response(svc.forecasts_for(team) if team else {"rows": [], "avg_accuracy": None})

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
        from django.utils import timezone
        game = self.get_object()
        game.deployed = bool(request.data.get("deployed", True))
        fields = ["deployed"]
        if game.deployed and not game.deployed_at:
            game.deployed_at = timezone.now()
            fields.append("deployed_at")
        # unpublishing to faculty also retracts it from students
        if not game.deployed and game.student_deployed:
            game.student_deployed = False
            fields.append("student_deployed")
        game.save(update_fields=fields)
        return Response({"id": game.id, "deployed": game.deployed, "deployed_at": game.deployed_at,
                         "student_deployed": game.student_deployed})

    # ---- open / close the sim to students (faculty) ----
    @action(detail=True, methods=["post"], url_path="student-deploy",
            permission_classes=[IsFaculty])
    def student_deploy(self, request, pk=None):
        from django.utils import timezone
        game = self.get_object()
        want = bool(request.data.get("deployed", True))
        if want and not game.deployed:
            raise ValidationError({"detail": "This sim hasn't been deployed to faculty yet."})
        game.student_deployed = want
        fields = ["student_deployed"]
        if want and not game.student_deployed_at:
            game.student_deployed_at = timezone.now()
            fields.append("student_deployed_at")
        game.save(update_fields=fields)
        return Response({"id": game.id, "student_deployed": game.student_deployed,
                         "student_deployed_at": game.student_deployed_at})

    # ---- extend a round's due date, shifting later rounds (faculty) ----
    @action(detail=True, methods=["post"], url_path="extend-round",
            permission_classes=[IsFaculty])
    def extend_round(self, request, pk=None):
        from datetime import date
        from .schedule import extend_round as _extend
        game = self.get_object()
        try:
            round_no = int(request.data.get("round"))
            new_due = date.fromisoformat(str(request.data.get("due")))
        except (TypeError, ValueError):
            raise ValidationError({"detail": "Provide a round number and a due date (YYYY-MM-DD)."})
        cascade = bool(request.data.get("cascade", True))
        try:
            game.schedule = _extend(game.schedule, round_no, new_due, cascade=cascade)
        except ValueError as e:
            raise ValidationError({"detail": str(e)})
        game.save(update_fields=["schedule"])
        return Response({"id": game.id, "schedule": game.schedule})

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
        # anonymized cohort best/avg/worst block). Enrich each round with the brand-tracker
        # trajectory: the team's positioning on the heritage↔wellness axis and brand equity.
        hist = self._service(game).history()
        by_round = {rec["round"]: rec for rec in hist}
        scoped = []
        for r in report:
            rec = by_round.get(r["round"], {})
            firm = (rec.get("firms") or {}).get(team.name) or {}
            dec = (rec.get("effective_decisions") or {}).get(team.name) or {}
            scoped.append({
                "round": r["round"],
                "own_report": r.get("teams", {}).get(team.name),
                "positioning": dec.get("positioning"),
                "brand_equity": firm.get("eq"),
            })
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
        models.Enrollment.objects.get_or_create(game=game, user=user)
        return Response({"team": team.name, "assigned": user.username})

    # ---- faculty setup flow -----------------------------------------------------
    @action(detail=True, methods=["get"], permission_classes=[IsFaculty])
    def setup(self, request, pk=None):
        from .setup import setup_summary
        return Response(setup_summary(self.get_object()))

    @action(detail=True, methods=["post"], url_path="provision-test-team",
            permission_classes=[IsFaculty])
    def provision_test_team(self, request, pk=None):
        from .setup import provision_test_team, setup_summary
        game = self.get_object()
        try:
            result = provision_test_team(game, request.data.get("per_firm", 4))
        except ValueError as e:
            raise ValidationError({"detail": str(e)})
        return Response({**result, "summary": setup_summary(game)})

    @action(detail=True, methods=["get", "post"], permission_classes=[IsFaculty])
    def invitations(self, request, pk=None):
        from .setup import create_invitations, setup_summary
        game = self.get_object()
        if request.method == "POST":
            emails = request.data.get("emails")
            if isinstance(emails, str):
                emails = [emails]
            if not emails:
                raise ValidationError({"detail": "Provide one or more email addresses."})
            send = bool(request.data.get("send", True))
            result = create_invitations(game, emails, send=send)
            return Response({**result, "summary": setup_summary(game)})
        rows = [{"id": i.id, "email": i.email, "status": i.status, "emailed": i.emailed,
                 "created_at": i.created_at} for i in game.invitations.all()]
        return Response({"invitations": rows})

    @action(detail=True, methods=["post"], url_path="invite-link",
            permission_classes=[IsFaculty])
    def invite_link(self, request, pk=None):
        from .setup import ensure_join_token, join_url
        game = self.get_object()
        return Response({"link": join_url(ensure_join_token(game))})

    @action(detail=True, methods=["post"], url_path="add-team",
            permission_classes=[IsFaculty])
    def add_team(self, request, pk=None):
        game = self.get_object()
        name = (request.data.get("name") or "").strip()
        if not name:
            n = game.teams.count() + 1
            name = f"Team {n}"
        if game.teams.filter(name=name).exists():
            raise ValidationError({"detail": f"A firm named “{name}” already exists."})
        team = models.Team.objects.create(game=game, name=name)
        return Response({"id": team.id, "name": team.name}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="delete-team",
            permission_classes=[IsFaculty])
    def delete_team(self, request, pk=None):
        game = self.get_object()
        team = get_object_or_404(models.Team, game=game, pk=request.data.get("team_id"))
        team.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="place-student",
            permission_classes=[IsFaculty])
    def place_student(self, request, pk=None):
        """Place, move, or unplace an enrolled student. team_id null = awaiting placement."""
        from accounts.models import User
        from .setup import setup_summary
        game = self.get_object()
        user = get_object_or_404(User, pk=request.data.get("user_id"))
        models.Enrollment.objects.get_or_create(game=game, user=user)
        for t in game.teams.filter(users=user):
            t.users.remove(user)
        team_id = request.data.get("team_id")
        if team_id:
            team = get_object_or_404(models.Team, game=game, pk=team_id)
            team.users.add(user)
        return Response(setup_summary(game))

    # ---- faculty sim console: overview + payment + teaching note ----------------
    @action(detail=True, methods=["get"], permission_classes=[IsFaculty])
    def overview(self, request, pk=None):
        from .setup import overview_summary
        return Response(overview_summary(self.get_object()))

    @action(detail=True, methods=["get"], permission_classes=[IsFaculty])
    def students(self, request, pk=None):
        """Enrolled students with placement + payment, for the Students management view."""
        from accounts.models import Role
        game = self.get_object()
        team_by_user = {}
        for t in game.teams.all():
            for u in t.users.all():
                team_by_user[u.id] = t.name
        rows = []
        for e in game.enrollments.select_related("user"):
            u = e.user
            rows.append({"id": u.id, "name": (f"{u.first_name} {u.last_name}".strip() or u.username),
                         "email": u.email or u.username, "team": team_by_user.get(u.id),
                         "paid": e.paid, "is_test": e.is_test})
        return Response({"students": rows})

    @action(detail=True, methods=["post"], url_path="mark-paid",
            permission_classes=[IsFaculty])
    def mark_paid(self, request, pk=None):
        from accounts.models import User
        game = self.get_object()
        enr = get_object_or_404(models.Enrollment, game=game,
                                user=get_object_or_404(User, pk=request.data.get("user_id")))
        enr.paid = bool(request.data.get("paid", True))
        enr.save(update_fields=["paid"])
        return Response({"user_id": enr.user_id, "paid": enr.paid})

    @action(detail=True, methods=["post"], url_path="teaching-note",
            permission_classes=[IsFaculty])
    def teaching_note(self, request, pk=None):
        game = self.get_object()
        game.teaching_note = request.data.get("note", "")
        game.save(update_fields=["teaching_note"])
        return Response({"teaching_note": game.teaching_note})

    @action(detail=True, methods=["get"], permission_classes=[IsFaculty])
    def defenses(self, request, pk=None):
        """Read-only board-defense results per team. Defenses are auto-graded on submit."""
        game = self.get_object()
        graded = {d.team_id: d for d in models.DefenseSubmission.objects.filter(game=game)}
        rows = []
        for t in game.teams.all():
            d = graded.get(t.id)
            rows.append({
                "team": t.name,
                "defended": bool(d),
                "defense_text": d.defense_text if d else None,
                "combined": d.combined if d else None,
                "graded_at": d.graded_at if d else None,
            })
        return Response({"defenses": rows})

    @action(detail=True, methods=["post"], url_path="add-cofaculty",
            permission_classes=[IsFaculty])
    def add_cofaculty(self, request, pk=None):
        from accounts.models import User
        game = self.get_object()
        u = get_object_or_404(User, username=request.data.get("username"))
        if not u.is_faculty:
            raise ValidationError({"detail": "That user isn't a faculty member."})
        game.faculty_members.add(u)
        return Response({"added": u.username})

    @action(detail=True, methods=["post"], url_path="remove-cofaculty",
            permission_classes=[IsFaculty])
    def remove_cofaculty(self, request, pk=None):
        from accounts.models import User
        game = self.get_object()
        u = get_object_or_404(User, username=request.data.get("username"))
        if game.faculty_id == u.id:
            raise ValidationError({"detail": "The sim owner can't be removed."})
        game.faculty_members.remove(u)
        return Response({"removed": u.username})
