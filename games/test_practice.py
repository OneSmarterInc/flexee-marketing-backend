"""Practice games: the scripted reference firms, creating and starting over, playing through,
and staying out of billing and history."""
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Role, User
from flexee_engine import api
from . import models, practice
from .practice import (REFERENCE_PATH_IDS, REFERENCE_PATHS, create_practice, path_levers,
                       reference_decision, reset_practice)
from .services import GameService
from .validators import LEVER_FIELDS

BASE = "/meridian/api/games"


def _source(faculty, **kw):
    game = models.Game.objects.create(
        name="Torque 101", faculty=faculty, num_rounds=14, takeover_round=4, current_round=2,
        config_snapshot=api.default_config(), timezone="America/New_York",
        status=models.GameStatus.RUNNING, deployed=True, student_deployed=True, **kw)
    game.faculty_members.add(faculty)
    models.Team.objects.create(game=game, name="Team A")
    return game


class ReferencePathTests(TestCase):
    def test_the_numbers_are_the_engine_calibration_archetypes(self):
        import calibration           # flexee_engine puts vendor/ on sys.path
        pairs = {"heritage_defender": "HERITAGE", "performance_chaser": "PERF",
                 "wellness_pivoter": "PIVOT"}
        for pid, arch in pairs.items():
            path, want = REFERENCE_PATHS[pid], calibration.ARCHETYPES[arch]
            for field in ("brand", "perf", "research", "positioning", "price", "promotion"):
                self.assertEqual(getattr(path, field), want[field], f"{pid}.{field}")
            self.assertEqual(tuple(path.emphasis), tuple(want["emphasis"]), f"{pid}.emphasis")

    def test_every_path_is_a_valid_decision_in_every_round(self):
        cfg = api.default_config()
        for pid in REFERENCE_PATH_IDS:
            for rnd in range(1, 15):
                open_levers = GameService(models.Game(config_snapshot=cfg, num_rounds=14)).open_levers(rnd)
                got = reference_decision(pid, open_levers, cfg)      # raises if invalid
                allowed = {f for lever in open_levers for f in LEVER_FIELDS[lever]}
                self.assertLessEqual(set(got), allowed, f"{pid} r{rnd} wrote a closed lever")
                if not open_levers:
                    self.assertEqual(got, {})

    def test_gating_follows_the_schedule(self):
        cfg = api.default_config()
        svc = GameService(models.Game(config_snapshot=cfg, num_rounds=14))
        self.assertEqual(reference_decision("heritage_defender", svc.open_levers(3), cfg), {})
        r4 = reference_decision("heritage_defender", svc.open_levers(4), cfg)
        self.assertEqual(set(r4), {"brand", "perf", "research", "emphasis"})
        self.assertIn("price", reference_decision("heritage_defender", svc.open_levers(8), cfg))
        self.assertIn("line_extension", reference_decision("wellness_pivoter", svc.open_levers(7), cfg))

    def test_paths_are_fitted_to_the_games_budget_and_price_band(self):
        cfg = {**api.default_config(), "round_budget": 12.0, "price_band": [1.60, 1.80]}
        for pid in REFERENCE_PATH_IDS:
            lv = path_levers(REFERENCE_PATHS[pid], cfg)
            self.assertAlmostEqual(lv["brand"] + lv["perf"] + lv["research"], 12.0, places=6)
            self.assertTrue(1.60 <= lv["price"] <= 1.80)
            self.assertAlmostEqual(sum(lv["emphasis"]), 1.0, places=9)
            self.assertAlmostEqual(sum(lv["channel"].values()), 1.0, places=9)

    def test_only_the_pivoter_launches_the_functional_line(self):
        self.assertEqual([p for p in REFERENCE_PATH_IDS if REFERENCE_PATHS[p].launch_line],
                         ["wellness_pivoter"])


class PracticeGameTests(TestCase):
    def setUp(self):
        self.faculty = User.objects.create_user(username="fac", password="x", role=Role.FACULTY,
                                                email="fac@example.com", first_name="Fay")
        self.source = _source(self.faculty)
        self.client = APIClient()
        self.client.force_authenticate(self.faculty)

    def _practice(self):
        return self.client.post(f"{BASE}/{self.source.id}/practice/")

    def test_creating_one(self):
        r = self._practice()
        self.assertEqual(r.status_code, 201)
        g = models.Game.objects.get(pk=r.json()["id"])
        self.assertTrue(g.is_practice)
        self.assertEqual(g.practice_of_id, self.source.id)
        self.assertEqual(g.name, "Torque 101 (practice)")
        self.assertEqual(g.current_round, 4)                    # the first decision round
        self.assertEqual(g.status, models.GameStatus.RUNNING)
        self.assertEqual(g.schedule, [])                        # no deadlines
        self.assertIsNone(g.start_date)
        self.assertEqual(g.price_per_student, 0)
        self.source.refresh_from_db()
        self.assertEqual(g.config_snapshot, self.source.config_snapshot)
        names = {t.name: t.reference_path for t in g.teams.all()}
        self.assertEqual(names, {"Your practice firm": "", "Heritage Defender": "heritage_defender",
                                 "Performance Chaser": "performance_chaser",
                                 "Wellness Pivoter": "wellness_pivoter"})
        mine = g.teams.get(name="Your practice firm")
        self.assertEqual(list(mine.users.all()), [self.faculty])
        self.assertTrue(r.json()["is_practice"])
        self.assertEqual(models.Game.objects.filter(is_practice=True).count(), 1)

    def test_asking_again_returns_the_same_game(self):
        first = self._practice()
        second = self._practice()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(models.Game.objects.filter(is_practice=True).count(), 1)

    def test_a_practice_game_cannot_be_practised(self):
        pid = self._practice().json()["id"]
        self.assertEqual(self.client.post(f"{BASE}/{pid}/practice/").status_code, 400)

    def test_students_and_unrelated_faculty_cannot(self):
        student = User.objects.create_user(username="stu", password="x", role=Role.STUDENT)
        c = APIClient(); c.force_authenticate(student)
        self.assertEqual(c.post(f"{BASE}/{self.source.id}/practice/").status_code, 403)
        other = User.objects.create_user(username="fac2", password="x", role=Role.FACULTY)
        c = APIClient(); c.force_authenticate(other)
        self.assertEqual(c.post(f"{BASE}/{self.source.id}/practice/").status_code, 404)

    def test_it_is_private_to_the_faculty_member_who_made_it(self):
        pid = self._practice().json()["id"]
        other = User.objects.create_user(username="fac2", password="x", role=Role.FACULTY)
        self.source.faculty_members.add(other)               # co-faculty of the source
        c = APIClient(); c.force_authenticate(other)
        self.assertEqual(c.get(f"{BASE}/{pid}/").status_code, 404)
        self.assertEqual(c.post(f"{BASE}/{pid}/practice/reset/").status_code, 404)

    def test_the_faculty_member_plays_as_their_own_firm(self):
        pid = self._practice().json()["id"]
        state = self.client.get(f"{BASE}/{pid}/state/").json()
        self.assertEqual(state["my_team"], "Your practice firm")
        self.assertEqual(state["current_round"], 4)
        lv = {"brand": 5, "perf": 2, "research": 3, "emphasis": [0.2] * 5}
        r = self.client.post(f"{BASE}/{pid}/submit/", {"round": 4, "levers": lv}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["team"], "Your practice firm")

    def test_nobody_can_decide_for_a_reference_firm(self):
        pid = self._practice().json()["id"]
        lv = {"brand": 5, "perf": 2, "research": 3, "emphasis": [0.2] * 5}
        r = self.client.post(f"{BASE}/{pid}/submit/",
                             {"round": 4, "levers": lv, "team": "Heritage Defender"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_an_ordinary_game_still_makes_faculty_name_a_team(self):
        lv = {"brand": 5, "perf": 2, "research": 3, "emphasis": [0.2] * 5}
        r = self.client.post(f"{BASE}/{self.source.id}/submit/", {"round": 4, "levers": lv}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_advance_writes_reference_decisions_and_shows_the_player_their_own_view(self):
        pid = self._practice().json()["id"]
        lv = {"brand": 5, "perf": 2, "research": 3, "emphasis": [0.2] * 5}
        self.client.post(f"{BASE}/{pid}/submit/", {"round": 4, "levers": lv}, format="json")
        r = self.client.post(f"{BASE}/{pid}/close-round/", {"round": 4}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        g = models.Game.objects.get(pk=pid)
        self.assertEqual(g.current_round, 5)
        for t in g.teams.exclude(reference_path=""):
            sub = g.submissions.get(round=4, team=t)
            self.assertEqual(set(sub.submitted_levers), {"brand", "perf", "research", "emphasis"})
        res = self.client.get(f"{BASE}/{pid}/results/?round=4").json()
        self.assertIn("firm", res)                              # the student-safe shape, not the raw record
        self.assertNotIn("firms", res)
        self.assertIn("scorecard", res)
        card = self.client.get(f"{BASE}/{pid}/scorecard/").json()
        self.assertTrue(card and "own_report" in card[0])

    def test_closing_a_round_again_does_not_duplicate_reference_decisions(self):
        pid = self._practice().json()["id"]
        self.client.post(f"{BASE}/{pid}/close-round/", {"round": 4}, format="json")
        self.client.post(f"{BASE}/{pid}/reopen-round/", {"round": 4}, format="json")
        self.client.post(f"{BASE}/{pid}/close-round/", {"round": 4}, format="json")
        g = models.Game.objects.get(pk=pid)
        self.assertEqual(g.submissions.filter(round=4).exclude(team__reference_path="").count(), 3)

    def test_a_whole_game_plays_through_to_the_end(self):
        pid = self._practice().json()["id"]
        for rnd in range(4, 15):
            r = self.client.post(f"{BASE}/{pid}/close-round/", {"round": rnd}, format="json")
            self.assertEqual(r.status_code, 200, f"round {rnd}: {r.content}")
        g = models.Game.objects.get(pk=pid)
        self.assertEqual(g.status, models.GameStatus.COMPLETE)
        last = GameService(g).result_for(14)
        self.assertTrue({t.name for t in g.teams.all()} <= set(last["firms"]))   # plus the engine's backdrop firms

    def test_start_over_wipes_the_play_but_keeps_the_firms(self):
        pid = self._practice().json()["id"]
        lv = {"brand": 5, "perf": 2, "research": 3, "emphasis": [0.2] * 5}
        self.client.post(f"{BASE}/{pid}/submit/", {"round": 4, "levers": lv}, format="json")
        self.client.post(f"{BASE}/{pid}/close-round/", {"round": 4}, format="json")
        self.client.post(f"{BASE}/{pid}/close-round/", {"round": 5}, format="json")
        r = self.client.post(f"{BASE}/{pid}/practice/reset/")
        self.assertEqual(r.status_code, 200, r.content)
        g = models.Game.objects.get(pk=pid)
        self.assertEqual((g.current_round, g.status, g.locked_rounds), (4, "running", []))
        self.assertEqual(g.submissions.count() + g.results.count(), 0)
        self.assertEqual(g.teams.count(), 4)
        self.assertEqual(models.RoundCloseLog.objects.filter(game_id_ref=pid).count(), 0)
        self.assertEqual(self.client.post(f"{BASE}/{pid}/close-round/", {"round": 4},
                                          format="json").status_code, 200)   # plays again

    def test_start_over_refuses_while_a_round_is_closing(self):
        pid = self._practice().json()["id"]
        g = models.Game.objects.get(pk=pid)
        self.assertTrue(GameService(g).claim_close())
        r = self.client.post(f"{BASE}/{pid}/practice/reset/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("closing", str(r.json()))

    def test_only_a_practice_game_can_be_started_over(self):
        with self.assertRaises(Exception):
            reset_practice(self.source)

    def test_it_stays_out_of_billing_and_history(self):
        pid = self._practice().json()["id"]
        self.client.post(f"{BASE}/{pid}/close-round/", {"round": 4}, format="json")
        admin = User.objects.create_user(username="adm", password="x", role=Role.ADMIN)
        c = APIClient(); c.force_authenticate(admin)
        billed = c.get(f"{BASE}/billing/").json()
        self.assertEqual(billed["counts"]["simulations"], 1)             # the source only
        self.assertNotIn(pid, [row["id"] for row in billed["sims"]])
        log = models.RoundCloseLog.objects.get(game_id_ref=pid)
        self.assertTrue(log.practice)

    def test_deleting_the_source_keeps_the_practice_game(self):
        g, _ = create_practice(self.faculty, self.source)
        self.source.delete()
        g.refresh_from_db()
        self.assertIsNone(g.practice_of_id)
        self.assertTrue(g.is_practice)
