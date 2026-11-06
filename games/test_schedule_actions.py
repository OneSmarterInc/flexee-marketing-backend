"""Extend and reopen, as faculty use them from the Schedule screen."""
from datetime import timedelta

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Role, User
from flexee_engine import api
from . import models
from .deadlines import today_in
from .schedule import build_schedule
from .services import GameService

API = "/meridian/api"


def _live_game(days_ago=10):
    owner = User.objects.create_user(username="f@school.edu", email="f@school.edu",
                                     password="owner-pass-123", role=Role.FACULTY)
    start = today_in("America/New_York") - timedelta(days=days_ago)
    game = models.Game.objects.create(
        name="Sched", faculty=owner, num_rounds=14, current_round=1, takeover_round=4,
        config_snapshot=api.default_config(), timezone="America/New_York", start_date=start,
        schedule=build_schedule(start, 7, 14), student_deployed=True, status=models.GameStatus.RUNNING)
    game.faculty_members.add(owner)
    team = models.Team.objects.create(game=game, name="Team A")
    c = APIClient()
    c.force_authenticate(owner)
    return game, team, c


def _iso(days):
    return (today_in("America/New_York") + timedelta(days=days)).isoformat()


class ExtendTests(TestCase):
    def test_extending_the_open_round_moves_its_date_and_shifts_later_rounds(self):
        game, team, c = _live_game(days_ago=2)
        before = {r["round"]: r for r in game.schedule}
        r = c.post(f"{API}/games/{game.pk}/extend-round/", {"round": 1, "due": _iso(9)}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        after = {row["round"]: row for row in r.data["schedule"]}
        self.assertEqual(after[1]["due"], _iso(9))
        self.assertNotEqual(after[2]["opens"], before[2]["opens"])

    def test_a_round_that_already_closed_cannot_be_extended(self):
        game, team, c = _live_game()
        GameService(game).close_round(1)
        r = c.post(f"{API}/games/{game.pk}/extend-round/", {"round": 1, "due": _iso(5)}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Reopen", str(r.data))

    def test_a_date_in_the_past_is_refused_on_a_live_class(self):
        game, team, c = _live_game(days_ago=2)
        r = c.post(f"{API}/games/{game.pk}/extend-round/", {"round": 1, "due": _iso(-3)}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("passed", str(r.data))


class ReopenTests(TestCase):
    def _closed_round_one(self):
        game, team, c = _live_game()          # round 1's deadline passed days ago
        GameService(game).close_round(1)
        game.refresh_from_db()
        return game, team, c

    def test_reopening_after_the_deadline_needs_a_new_date(self):
        game, team, c = self._closed_round_one()
        r = c.post(f"{API}/games/{game.pk}/reopen-round/", {"round": 1}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("new due date", str(r.data))
        game.refresh_from_db()
        self.assertIn(1, game.locked_rounds)          # nothing was unlocked

    def test_reopening_with_a_new_date_lets_teams_resubmit_and_leaves_later_rounds_alone(self):
        game, team, c = self._closed_round_one()
        later = {r["round"]: dict(r) for r in game.schedule}
        r = c.post(f"{API}/games/{game.pk}/reopen-round/", {"round": 1, "due": _iso(2)}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        game.refresh_from_db()
        self.assertNotIn(1, game.locked_rounds)
        now = {row["round"]: row for row in game.schedule}
        self.assertEqual(now[1]["due"], _iso(2))
        self.assertEqual(now[2], later[2])
        GameService(game).submit(1, team, {})         # accepted again

    def test_reopening_a_class_without_a_clock_needs_no_date(self):
        game, team, c = self._closed_round_one()
        game.student_deployed = False
        game.save(update_fields=["student_deployed"])
        r = c.post(f"{API}/games/{game.pk}/reopen-round/", {"round": 1}, format="json")
        self.assertEqual(r.status_code, 200, r.content)


class ReopenedRoundCanBeExtended(TestCase):
    def test_a_reopened_round_can_be_given_more_time(self):
        game, team, c = _live_game()
        GameService(game).close_round(1)
        c.post(f"{API}/games/{game.pk}/reopen-round/", {"round": 1, "due": _iso(1)}, format="json")
        r = c.post(f"{API}/games/{game.pk}/extend-round/", {"round": 1, "due": _iso(4), "cascade": False}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual({x["round"]: x for x in r.data["schedule"]}[1]["due"], _iso(4))
