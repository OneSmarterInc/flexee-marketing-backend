"""Operations tests: the required time zone, deadlines, the close claim and log, the sweep and
lazy auto-advance, the day-before reminder, the cron endpoint, and deploy-opens-the-round."""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from accounts.models import Role, User
from flexee_engine import api
from . import models
from .deadlines import end_of_day, is_valid_timezone, round_deadline, today_in, validate_timezone
from .reminders import lead_time, send_due_reminders, send_game_reminder
from .schedule import build_schedule
from .scheduler import auto_advance_if_due, run_sweep
from .services import GameService

TZ = "America/New_York"


def _live_game(first_due="2099-03-10", tz=TZ, rounds=14, takeover=4, current=1, **kw):
    """A game deployed to students, round 1 due 23:59 on `first_due` in `tz`."""
    first = date.fromisoformat(first_due) - timedelta(days=6)
    faculty = User.objects.create_user(username=f"fac{User.objects.count()}", password="x",
                                       role=Role.FACULTY, email="fac@example.com")
    game = models.Game.objects.create(
        name="Live Game", faculty=faculty, num_rounds=rounds, takeover_round=takeover,
        current_round=current, timezone=tz, start_date=first, status=models.GameStatus.RUNNING,
        schedule=build_schedule(first, 7, rounds), student_deployed=True, **kw)
    game.faculty_members.add(faculty)
    return game


def _team_with_student(game, name="Team A", email=None, **user_kw):
    team = models.Team.objects.create(game=game, name=name)
    n = User.objects.count()
    student = User.objects.create_user(
        username=f"stu{n}", password="x", role=Role.STUDENT,
        email=email if email is not None else f"stu{n}@example.com", **user_kw)
    team.users.add(student)
    return team, student


def utc(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=dt_timezone.utc)


class TimezoneTests(TestCase):
    def test_validation(self):
        self.assertTrue(is_valid_timezone("America/New_York"))
        self.assertFalse(is_valid_timezone("Not/AZone"))
        self.assertFalse(is_valid_timezone("   "))
        self.assertEqual(validate_timezone("  Asia/Kolkata "), "Asia/Kolkata")
        with self.assertRaisesMessage(ValueError, "Choose the time zone"):
            validate_timezone("")
        with self.assertRaisesMessage(ValueError, "not a real time zone"):
            validate_timezone("Mars/Olympus")

    def _create(self, **payload):
        admin, _ = User.objects.get_or_create(username="adm", defaults={"role": Role.ADMIN})
        client = APIClient()
        client.force_authenticate(admin)
        body = {"name": "G", "num_teams": 2, **payload}
        return client.post("/meridian/api/games/", body, format="json")

    def test_creating_a_game_needs_a_time_zone(self):
        for bad in ({}, {"timezone": ""}, {"timezone": "  "}, {"timezone": "Nowhere/Land"}):
            r = self._create(**bad)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("timezone", r.json(), bad)

    def test_a_valid_time_zone_is_stored_and_starts_the_schedule_today_there(self):
        r = self._create(timezone="Pacific/Auckland")
        self.assertEqual(r.status_code, 201, r.content)
        game = models.Game.objects.get(pk=r.json()["id"])
        self.assertEqual(game.timezone, "Pacific/Auckland")
        self.assertEqual(game.schedule[0]["opens"], today_in("Pacific/Auckland").isoformat())


class DeadlineTests(TestCase):
    def test_a_round_closes_at_2359_on_its_due_date_in_game_time(self):
        game = _live_game(first_due="2026-03-10")
        deadline = round_deadline(game, 1)
        self.assertEqual(deadline, end_of_day(date(2026, 3, 10), TZ))
        local = deadline.astimezone(end_of_day(date(2026, 3, 10), TZ).tzinfo)
        self.assertEqual((local.hour, local.minute), (23, 59))
        self.assertIsNone(round_deadline(game, 99))

    def test_no_schedule_means_no_deadline(self):
        game = _live_game()
        game.schedule = []
        self.assertIsNone(round_deadline(game, 1))


class SubmitDeadlineTests(TestCase):
    def test_a_change_after_the_deadline_is_refused_until_the_sweep_closes_it(self):
        game = _live_game(first_due="2026-03-10")
        team, _ = _team_with_student(game)
        svc = GameService(game)
        with patch("games.services.now_utc", return_value=utc(2026, 3, 11, 12)):
            with self.assertRaisesMessage(ValidationError, "has closed"):
                svc.submit(1, team, {})
        with patch("games.services.now_utc", return_value=utc(2026, 3, 10, 12)):
            svc.submit(1, team, {})           # before the deadline: fine

    def test_a_practice_game_has_no_deadline(self):
        game = _live_game(first_due="2026-03-10", is_practice=True)
        team, _ = _team_with_student(game)
        with patch("games.services.now_utc", return_value=utc(2030, 1, 1)):
            GameService(game).submit(1, team, {})

    def test_nobody_can_decide_for_a_reference_firm(self):
        game = _live_game()
        team = models.Team.objects.create(game=game, name="Ref", reference_path="heritage")
        with self.assertRaisesMessage(ValidationError, "reference firm"):
            GameService(game).submit(1, team, {})


class CloseClaimAndLogTests(TestCase):
    def test_one_claim_per_close_and_a_stale_claim_is_dead(self):
        game = _live_game()
        svc = GameService(game)
        self.assertTrue(svc.claim_close())
        self.assertFalse(GameService(models.Game.objects.get(pk=game.pk)).claim_close())
        models.Game.objects.filter(pk=game.pk).update(
            close_claimed_at=datetime.now(dt_timezone.utc) - timedelta(minutes=16))
        self.assertTrue(GameService(models.Game.objects.get(pk=game.pk)).claim_close())

    def test_a_close_while_another_is_running_is_refused(self):
        game = _live_game()
        svc = GameService(game)
        svc.claim_close()
        with self.assertRaisesMessage(ValidationError, "already closing"):
            GameService(models.Game.objects.get(pk=game.pk)).close_round(1)

    def test_a_close_is_recorded_and_the_claim_released(self):
        game = _live_game()
        team, _ = _team_with_student(game)
        _team_with_student(game, name="Team B")
        GameService(game).close_round(1, source="faculty")
        log = models.RoundCloseLog.objects.get()
        self.assertEqual((log.game_id_ref, log.course, log.round, log.source, log.status),
                         (game.pk, "Live Game", 1, "faculty", "succeeded"))
        self.assertEqual((log.firms, log.firms_submitted, log.firms_carried_forward), (2, 0, 0))
        self.assertGreaterEqual(log.seconds, 0)
        self.assertIsNone(models.Game.objects.get(pk=game.pk).close_claimed_at)

    def test_carried_forward_counts_firms_that_missed_a_round_with_decisions_open(self):
        game = _live_game(current=4)
        a, _ = _team_with_student(game)
        _team_with_student(game, name="Team B")
        svc = GameService(game)
        for r in (1, 2, 3):
            svc.close_round(r)
        svc.submit(4, a, {"brand": 5, "perf": 2, "research": 3, "emphasis": [0.2] * 5})
        svc.close_round(4)
        log = models.RoundCloseLog.objects.get(round=4)
        self.assertEqual((log.firms, log.firms_submitted, log.firms_carried_forward), (2, 1, 1))

    def test_a_failed_close_is_logged_and_releases_the_claim(self):
        game = _live_game()
        _team_with_student(game)
        svc = GameService(game)
        with patch.object(GameService, "_close_round_locked", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                svc.close_round(1, source="deadline")
        log = models.RoundCloseLog.objects.get()
        self.assertEqual((log.status, log.error_code, log.source), ("failed", "other", "deadline"))
        self.assertIsNone(models.Game.objects.get(pk=game.pk).close_claimed_at)

    def test_history_outlives_the_game(self):
        game = _live_game()
        _team_with_student(game)
        GameService(game).close_round(1)
        game.delete()
        self.assertEqual(models.RoundCloseLog.objects.count(), 1)

    def test_a_practice_close_is_flagged_so_reporting_can_leave_it_out(self):
        game = _live_game(is_practice=True)
        _team_with_student(game)
        GameService(game).close_round(1)
        self.assertTrue(models.RoundCloseLog.objects.get().practice)


class AutoAdvanceTests(TestCase):
    def test_a_due_round_is_closed_and_recorded_as_a_deadline_close(self):
        game = _live_game(first_due="2026-03-10")
        _team_with_student(game)
        self.assertTrue(auto_advance_if_due(game, utc(2026, 3, 11, 12)))
        game.refresh_from_db()
        self.assertEqual(game.current_round, 2)
        self.assertEqual(models.RoundCloseLog.objects.get().source, "deadline")

    def test_not_before_the_deadline(self):
        game = _live_game(first_due="2026-03-10")
        self.assertFalse(auto_advance_if_due(game, utc(2026, 3, 10, 12)))

    def test_never_the_last_round_practice_or_an_undeployed_game(self):
        last = _live_game(first_due="2026-03-10", current=14)
        self.assertFalse(auto_advance_if_due(last, utc(2027, 1, 1)))
        practice = _live_game(first_due="2026-03-10", is_practice=True)
        self.assertFalse(auto_advance_if_due(practice, utc(2027, 1, 1)))
        draft = _live_game(first_due="2026-03-10")
        draft.student_deployed = False
        self.assertFalse(auto_advance_if_due(draft, utc(2027, 1, 1)))

    def test_loading_a_game_after_its_deadline_advances_it(self):
        game = _live_game(first_due="2026-03-10")
        team, student = _team_with_student(game)
        client = APIClient()
        client.force_authenticate(student)
        self.assertEqual(client.get(f"/meridian/api/games/{game.pk}/state/").json()["current_round"], 2)


class SweepTests(TestCase):
    def test_the_sweep_catches_up_on_every_overdue_round_and_records_a_heartbeat(self):
        game = _live_game(first_due="2026-03-10")
        _team_with_student(game)
        # rounds 1 (due 3/10) and 2 (due 3/17) are past due; round 3 (due 3/24, 23:59 NY) is not
        result = run_sweep(utc(2026, 3, 24, 12))
        self.assertEqual([c["round"] for c in result["closed"]], [1, 2])
        game.refresh_from_db()
        self.assertEqual(game.current_round, 3)
        self.assertEqual(models.SchedulerState.objects.get(key="round-sweep").last_run_at,
                         utc(2026, 3, 24, 12))

    def test_a_quiet_sweep_still_records_a_heartbeat(self):
        run_sweep(utc(2026, 1, 1))
        self.assertEqual(models.SchedulerState.objects.count(), 1)


class CronEndpointTests(TestCase):
    URL = "/meridian/api/cron/round-tick/"

    def test_off_without_a_secret(self):
        with override_settings(CRON_SECRET=""):
            self.assertEqual(APIClient().post(self.URL).status_code, 503)

    @override_settings(CRON_SECRET="s3cret")
    def test_rejects_a_wrong_or_missing_secret(self):
        self.assertEqual(APIClient().post(self.URL).status_code, 401)
        self.assertEqual(APIClient().post(self.URL, HTTP_AUTHORIZATION="Bearer nope").status_code, 401)

    @override_settings(CRON_SECRET="s3cret")
    def test_runs_a_sweep_with_the_secret(self):
        r = APIClient().post(self.URL, HTTP_AUTHORIZATION="Bearer s3cret")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["closed"], [])
        self.assertEqual(models.SchedulerState.objects.count(), 1)


class ReminderTests(TestCase):
    def setUp(self):
        # round 4 has decisions open; it is due 2026-03-10 at 23:59 New York = 04:59 UTC the 11th
        self.game = _live_game(first_due="2026-03-10", current=4)
        self.deadline = round_deadline(self.game, 4)
        self.inside = self.deadline - timedelta(hours=20)       # within the day-before window
        self.team_a, self.stu_a = _team_with_student(self.game, "Team A", email="a@example.com")
        self.team_b, self.stu_b = _team_with_student(self.game, "Team B", email="b@example.com")

    def _submit(self, team):
        models.RoundSubmission.objects.create(
            game=self.game, round=4, team=team,
            submitted_levers={"brand": 5, "perf": 2, "research": 3, "emphasis": [0.2] * 5})

    def test_only_firms_that_have_not_submitted_are_reminded_once(self):
        self._submit(self.team_a)
        self.assertEqual(send_game_reminder(self.game, self.inside), 1)
        self.assertEqual([m.to for m in mail.outbox], [["b@example.com"]])
        self.assertIn("closes", mail.outbox[0].subject)
        self.assertIn("11:59 PM (America/New_York)", mail.outbox[0].body)
        self.assertEqual(send_game_reminder(self.game, self.inside), 0)      # once per round
        self.assertEqual(len(mail.outbox), 1)

    def test_not_before_the_window_and_not_after_the_deadline(self):
        self.assertEqual(send_game_reminder(self.game, self.deadline - timedelta(days=3)), 0)
        self.assertEqual(send_game_reminder(self.game, self.deadline + timedelta(minutes=1)), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_no_reminder_in_a_listening_round_with_nothing_to_submit(self):
        game = _live_game(first_due="2026-03-10", current=1)
        _team_with_student(game, email="x@example.com")
        self.assertEqual(send_game_reminder(game, round_deadline(game, 1) - timedelta(hours=5)), 0)

    def test_test_accounts_deactivated_users_duplicates_and_blank_emails_are_left_out(self):
        self.stu_b.email = "TEST-3@mailinator.com"
        self.stu_b.save()
        _, ghost = _team_with_student(self.game, "Team C", email="gone@example.com", is_active=False)
        team_d, _ = _team_with_student(self.game, "Team D", email="A@example.com")   # duplicate of a@
        team_e, _ = _team_with_student(self.game, "Team E", email="")
        self.assertEqual(send_game_reminder(self.game, self.inside), 1)
        self.assertEqual([m.to for m in mail.outbox], [["a@example.com"]])

    def test_reference_firms_and_practice_games_are_never_reminded(self):
        models.Team.objects.create(game=self.game, name="Ref", reference_path="heritage")
        practice = _live_game(first_due="2026-03-10", current=4, is_practice=True)
        _team_with_student(practice, email="p@example.com")
        send_due_reminders(self.inside)
        self.assertNotIn(["p@example.com"], [m.to for m in mail.outbox])

    def test_a_round_shorter_than_two_days_is_reminded_half_a_round_before(self):
        self.game.schedule = [{"round": 4, "opens": "2026-03-10", "due": "2026-03-10"}]
        self.assertEqual(lead_time(self.game, 4), timedelta(hours=12))
        self.assertEqual(lead_time(self.game, 99), timedelta(days=1))

    def test_if_nothing_could_be_sent_the_claim_is_given_back_for_the_next_sweep(self):
        with patch("games.reminders.send_mail", side_effect=OSError("mail down")):
            self.assertEqual(send_game_reminder(self.game, self.inside), 0)
        self.game.refresh_from_db()
        self.assertIsNone(self.game.reminder_sent_for_round)
        self.assertEqual(send_game_reminder(self.game, self.inside), 2)      # next sweep retries

    def test_off_switch_and_unconfigured_mail_send_and_claim_nothing(self):
        with override_settings(REMINDER_EMAILS_ENABLED=False):
            self.assertEqual(send_due_reminders(self.inside), 0)
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend"):
            self.assertEqual(send_due_reminders(self.inside), 0)
        self.game.refresh_from_db()
        self.assertIsNone(self.game.reminder_sent_for_round)

    def test_dry_run_logs_instead_of_sending(self):
        with override_settings(EMAIL_DRY_RUN=True,
                               EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend"):
            self.assertEqual(send_due_reminders(self.inside), 2)
        self.assertEqual(len(mail.outbox), 0)


class DeployOpensTheRoundTests(TestCase):
    def test_deploying_to_students_restarts_the_clock_from_today(self):
        admin = User.objects.create_user(username="adm", password="x", role=Role.ADMIN)
        game = models.Game.objects.create(
            name="Late", faculty=admin, timezone="Asia/Kolkata", start_date=date(2020, 1, 1),
            schedule=build_schedule(date(2020, 1, 1), 7, 14), deployed=True)
        game.faculty_members.add(admin)
        client = APIClient()
        client.force_authenticate(admin)
        r = client.post(f"/meridian/api/games/{game.pk}/student-deploy/", {"deployed": True}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        game.refresh_from_db()
        today = today_in("Asia/Kolkata")
        self.assertEqual(game.schedule[0]["opens"], today.isoformat())
        self.assertEqual(game.schedule[0]["due"], (today + timedelta(days=6)).isoformat())
        self.assertEqual(game.start_date, today)
        self.assertEqual(game.status, models.GameStatus.RUNNING)
        self.assertEqual(len(game.schedule), 14)
