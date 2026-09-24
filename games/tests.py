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
