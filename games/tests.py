"""Django app-layer tests for rules the vendored engine goldens don't cover.

The engine suite (test_engine_integrity.py, flexee_engine/vendor/tests,
games/test_enrichment_overlay.py) proves the pure engine and its overlays. It never
touches Django, so it can't prove the app's OWN rules: that the round-9 crisis actually
fires when a faculty member closes a round, that faculty scorecard weights actually move
the ranking, that the engagement cap actually blocks a real call, or that a locked round
actually rejects a real submission. These do, against a real DB.
"""
import copy

from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError

from accounts.models import Role, User
from flexee_engine import api
from . import models
from .services import GameService, apply_scorecard_weights
from .views import GameViewSet


def _make_game(**overrides):
    faculty = User.objects.create_user(username="test_faculty", password="x", role=Role.FACULTY)
    game = models.Game.objects.create(
        name="Test Game", faculty=faculty, num_rounds=14, current_round=1, takeover_round=4,
        **overrides,
    )
    game.faculty_members.add(faculty)
    team = models.Team.objects.create(game=game, name="Team A")
    return game, team


class CrisisOnCloseTests(TestCase):
    """The round-9 crisis lands when a round is closed (default crisis_round=9)."""

    def test_round_9_crisis_lands_on_close(self):
        game, team = _make_game()
        svc = GameService(game)
        result = svc.close_round(9)
        self.assertIn("damage", result)
        self.assertGreater(result["damage"][team.name], 0.0)

    def test_no_crisis_damage_before_round_9(self):
        game, team = _make_game()
        svc = GameService(game)
        result = svc.close_round(8)
        damage = result.get("damage", {})
        self.assertEqual(damage.get(team.name, 0.0), 0.0)


class ScorecardWeightsTests(TestCase):
    """Faculty scorecard weights change the ranking (not just the number)."""

    def test_faculty_weights_change_the_ranking(self):
        # Team A wins on brand_health, Team B wins on financial; all else tied.
        report = [{
            "round": 1,
            "teams": {
                "Team A": {"own": {"overall": 0, "normalized": {
                    "brand_health": 80, "customer_value": 20,
                    "market_position": 20, "financial": 20}}},
                "Team B": {"own": {"overall": 0, "normalized": {
                    "brand_health": 20, "customer_value": 20,
                    "market_position": 20, "financial": 80}}},
            },
        }]
        brand_weighted = apply_scorecard_weights(copy.deepcopy(report), {
            "brand_health": 1.0, "customer_value": 0.0, "market_position": 0.0, "financial": 0.0})
        financial_weighted = apply_scorecard_weights(copy.deepcopy(report), {
            "brand_health": 0.0, "customer_value": 0.0, "market_position": 0.0, "financial": 1.0})

        self.assertEqual(brand_weighted[0]["teams"]["Team A"]["rank"], 1)
        self.assertEqual(financial_weighted[0]["teams"]["Team B"]["rank"], 1)
        self.assertNotEqual(
            brand_weighted[0]["teams"]["Team A"]["rank"],
            financial_weighted[0]["teams"]["Team A"]["rank"],
        )


class EngagementCapTests(TestCase):
    """The engagement cap rejects the extra action in a round."""

    def setUp(self):
        # Force the deterministic offline voice client so this test needs no network/key,
        # and clear the process-wide cache both sides so the override actually takes effect.
        api.get_model_client.cache_clear()
        self._settings_override = override_settings(FLEXEE_LLM_PROVIDER="offline")
        self._settings_override.enable()
        api.get_model_client.cache_clear()

    def tearDown(self):
        self._settings_override.disable()
        api.get_model_client.cache_clear()

    def test_cap_rejects_the_extra_action_in_a_round(self):
        game, team = _make_game()
        svc = GameService(game)
        cap = svc._engagement_cap()
        persona_id = api.persona_records()[0]["id"]

        for _ in range(cap):
            svc.focus_group(1, team, [persona_id])
        with self.assertRaises(ValidationError):
            svc.focus_group(1, team, [persona_id])

    def test_cap_is_per_round_not_per_game(self):
        game, team = _make_game()
        svc = GameService(game)
        cap = svc._engagement_cap()
        persona_id = api.persona_records()[0]["id"]

        for _ in range(cap):
            svc.focus_group(1, team, [persona_id])
        # A new round resets the count — this must NOT raise.
        svc.focus_group(2, team, [persona_id])


class ClosedRoundRejectsSubmissionTests(TestCase):
    """A closed round rejects a new submission."""

    def test_closed_round_rejects_a_new_submission(self):
        game, team = _make_game()
        svc = GameService(game)
        svc.close_round(1)
        with self.assertRaises(ValidationError):
            svc.submit(1, team, {})

    def test_open_round_accepts_a_submission(self):
        game, team = _make_game()
        svc = GameService(game)
        # Sanity check the negative above is actually about the lock, not the fixture:
        # the same call succeeds before the round is closed.
        svc.submit(1, team, {})


def _make_game_with(config_overrides=None, team_names=("Team A",), username="t_faculty"):
    """Like _make_game, but with a config override and several teams."""
    faculty = User.objects.create_user(username=username, password="x", role=Role.FACULTY)
    config = {**api.default_config(), **(config_overrides or {})}
    game = models.Game.objects.create(
        name="Test Game", faculty=faculty, num_rounds=14, current_round=1,
        takeover_round=4, config_snapshot=config,
    )
    game.faculty_members.add(faculty)
    teams = [models.Team.objects.create(game=game, name=n) for n in team_names]
    return game, teams


class ResearchNeverLeaksTruthTests(TestCase):
    """A student commissioning research receives only reported values, never engine truth."""

    def test_stated_study_response_and_history_carry_no_truth(self):
        game, (team,) = _make_game_with()
        svc = GameService(game)
        svc.close_round(1)

        result = svc.commission_research(team, "segment_perception_tracker", "stated")
        for seg, cell in result["sheet"].items():
            self.assertEqual(set(cell), {"reported"}, f"{seg} leaked {cell}")

        for entry in svc.research_history(team):
            for seg, cell in entry["sheet"].items():
                self.assertEqual(set(cell), {"reported"})

        # Faculty/audit copy keeps the full sheet.
        stored = models.ResearchCommission.objects.get(game=game, team=team)
        self.assertTrue(all("true" in cell for cell in stored.sheet.values()))


class LineExtensionThroughCloseRoundTests(TestCase):
    """Launching the line extension charges its cost and cushions the round-9 crisis,
    end to end through close_round."""

    def setUp(self):
        schedule = {**api.default_config()["lever_schedule"], "line_extension": 1}
        self.game, (self.a, self.b) = _make_game_with(
            {"lever_schedule": schedule}, team_names=("Team A", "Team B"))
        self.svc = GameService(self.game)
        # Team A launches in round 1; Team B never launches.
        self.svc.submit(1, self.a, {"line_extension": {"launch": True, "positioning": 90.0}})
        for rnd in range(1, 10):
            self.svc.close_round(rnd)

    def test_launch_and_carry_costs_are_charged(self):
        r1 = self.svc.result_for(1)["firms"]["Team A"]["line_extension"]
        r2 = self.svc.result_for(2)["firms"]["Team A"]["line_extension"]
        self.assertAlmostEqual(r1["cost"], 8.0 + 1.5)   # launch round: launch + carry
        self.assertAlmostEqual(r2["cost"], 1.5)         # later rounds: carry only
        self.assertNotIn("line_extension", self.svc.result_for(2)["firms"]["Team B"])

    def test_launched_team_takes_less_crisis_damage(self):
        damage = self.svc.result_for(9)["damage"]
        self.assertGreater(damage["Team B"], 0.0)
        self.assertLess(damage["Team A"], damage["Team B"])


class StudentResultHidesEngineInternalsTests(TestCase):
    """The student round result never includes raw perception or attraction."""

    def test_student_view_strips_perception_and_attraction(self):
        game, (team,) = _make_game_with()
        svc = GameService(game)
        svc.close_round(1)
        rec = svc.result_for(1)
        self.assertIn("perception", rec["firms"][team.name])   # present server-side...

        view = GameViewSet._student_result_view(rec, team.name)
        self.assertNotIn("perception", view["firm"])           # ...but not sent to students
        self.assertNotIn("attraction", view["firm"])
        self.assertIn("share", view["firm"])                   # outcomes still sent
