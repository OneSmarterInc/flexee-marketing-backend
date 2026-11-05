"""Audit of 5 Nov 2026: each test states the CORRECT behaviour. Before the fixes they fail,
which is the proof of each finding; after the fixes they pass and stay as regression tests."""
from django.test import TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from accounts.models import Role, User
from flexee_engine import api
from . import models
from .services import GameService

API = "/meridian/api"


def _game(**kw):
    owner = User.objects.create_user(username="owner@school.edu", email="owner@school.edu",
                                     password="owner-pass-123", role=Role.FACULTY)
    game = models.Game.objects.create(name="Audit Game", faculty=owner, num_rounds=14,
                                      current_round=1, takeover_round=4,
                                      config_snapshot=api.default_config(), **kw)
    game.faculty_members.add(owner)
    team = models.Team.objects.create(game=game, name="Team A")
    return game, team, owner


class F1JoinLinkCannotTakeOverAccounts(TestCase):
    """A join link must never sign someone in to an account that already exists."""

    def test_join_with_an_existing_faculty_email_and_wrong_password_gets_no_token(self):
        game, team, owner = _game()
        game.join_token = "class-wide-join-link"
        game.save()
        r = APIClient().post(f"{API}/join/class-wide-join-link/",
                             {"email": "owner@school.edu", "password": "not-the-password"}, format="json")
        self.assertNotIn("token", r.data, f"join returned a sign-in token for the faculty account: {r.status_code}")

    def test_join_cannot_sign_in_as_an_admin(self):
        game, team, owner = _game()
        game.join_token = "class-wide-join-link"
        game.save()
        User.objects.create_user(username="admin@flexee.org", email="admin@flexee.org",
                                 password="admin-pass-123", role=Role.ADMIN)
        c = APIClient()
        r = c.post(f"{API}/join/class-wide-join-link/",
                   {"email": "admin@flexee.org", "password": "anything-8"}, format="json")
        if "token" in r.data:
            c.credentials(HTTP_AUTHORIZATION=f"Token {r.data['token']}")
            me = c.get(f"{API}/auth/me/").data
            self.fail(f"join link signed in as role={me.get('role')}")


class F2PublicSignUpCannotMakeFaculty(TestCase):
    def test_sign_up_refuses_the_faculty_role(self):
        r = APIClient().post(f"{API}/auth/register/", {
            "username": "stranger", "email": "stranger@example.com",
            "password": "stranger-pass", "role": "faculty"}, format="json")
        self.assertNotEqual(r.status_code, 201, "anyone can create a faculty account")


class F3TestStudentsOnlyOutsideLiveClasses(TestCase):
    def test_no_test_team_in_a_live_class(self):
        game, team, owner = _game(student_deployed=True)
        c = APIClient()
        c.force_authenticate(owner)
        r = c.post(f"{API}/games/{game.pk}/provision-test-team/", {"per_firm": 1}, format="json")
        self.assertGreaterEqual(r.status_code, 400, "test students were added to a live class")


class F4SubmissionsOnlyForTheOpenRound(TestCase):
    def test_cannot_save_decisions_for_a_future_round(self):
        # Round 1 is open. Round 2 is a listening round, so an empty decision is VALID there:
        # the only thing that should refuse it is that round 2 isn't open yet.
        game, team, owner = _game()
        svc = GameService(game)
        self.assertEqual(svc.open_levers(2), [])
        with self.assertRaises(ValidationError, msg="decisions were saved for a round that isn't open yet"):
            svc.submit(2, team, {})


class F5ForecastsLockWithTheRound(TestCase):
    def test_cannot_rewrite_a_forecast_after_its_round_closed(self):
        game, team, owner = _game()
        svc = GameService(game)
        svc.submit_forecast(1, team, "own_share", 5.0)
        svc.close_round(1)
        actual = models.Forecast.objects.get(game=game, team=team, round=1).actual
        with self.assertRaises(ValidationError, msg="a closed round's forecast could be rewritten to the result"):
            svc.submit_forecast(1, team, "own_share", actual)


class F6LastAdminStays(TestCase):
    def test_an_admin_cannot_demote_themselves_when_they_are_the_last_admin(self):
        admin = User.objects.create_user(username="only-admin", email="a@flexee.org",
                                         password="admin-pass-123", role=Role.ADMIN)
        c = APIClient()
        c.force_authenticate(admin)
        r = c.post(f"{API}/auth/users/set-role/", {"username": "only-admin", "role": "student"}, format="json")
        self.assertGreaterEqual(r.status_code, 400, "the last admin demoted themselves")


from django.test import override_settings


@override_settings(FLEXEE_AUTH_RATE="30/min")
class M4SignInIsRateLimited(TestCase):
    def test_repeated_wrong_passwords_are_slowed_down(self):
        from django.core.cache import cache
        cache.clear()
        User.objects.create_user(username="s@x.edu", email="s@x.edu", password="right-pass-1", role=Role.STUDENT)
        c = APIClient()
        codes = [c.post(f"{API}/auth/login/", {"username": "s@x.edu", "password": f"guess-{i}"},
                        format="json").status_code for i in range(40)]
        cache.clear()
        self.assertIn(429, codes, "40 wrong passwords in a row were all answered")


class H3SafeDefaults(TestCase):
    def test_debug_is_off_unless_turned_on(self):
        import subprocess, sys, os
        env = {k: v for k, v in os.environ.items() if k not in ("DJANGO_DEBUG",)}
        env["DJANGO_SECRET_KEY"] = "a-real-secret-for-this-check"
        out = subprocess.run([sys.executable, "-c",
                              "import django,os;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');"
                              "django.setup();from django.conf import settings;print(settings.DEBUG)"],
                             env=env, capture_output=True, text=True)
        self.assertIn("False", out.stdout, out.stderr)
