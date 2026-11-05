"""Password reset by an emailed six-digit code."""
import re
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from . import password_reset as pr
from .models import Role, User

BASE = "/meridian/api/auth/password-reset"


def _code_from_outbox() -> str:
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ann", password="old-password", email="ann@example.com", role=Role.STUDENT)
        self.client = APIClient()

    def _request(self, email="ann@example.com"):
        return self.client.post(f"{BASE}/request/", {"email": email}, format="json")

    def _confirm(self, otp, new="a-new-password", confirm=None, email="ann@example.com"):
        return self.client.post(f"{BASE}/confirm/", {
            "email": email, "otp": otp, "new_password": new,
            "confirm_password": confirm if confirm is not None else new}, format="json")

    def test_the_answer_is_the_same_whether_or_not_the_email_has_an_account(self):
        known, unknown = self._request(), self._request("nobody@example.com")
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.json(), unknown.json())
        self.assertEqual(len(mail.outbox), 1)        # only the real account got a code

    def test_a_code_works_once_resets_the_password_and_signs_everyone_out(self):
        Token.objects.create(user=self.user)
        self._request()
        code = _code_from_outbox()
        self.assertEqual(self.client.post(f"{BASE}/verify/", {"email": "ann@example.com", "otp": code},
                                          format="json").status_code, 200)
        self.assertEqual(self._confirm(code).status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("a-new-password"))
        self.assertFalse(Token.objects.filter(user=self.user).exists())
        self.assertEqual(self._confirm(code, new="another-password").status_code, 400)   # used up

    def test_every_way_of_being_wrong_gives_one_message(self):
        self._request()
        code = _code_from_outbox()
        wrong = "000000" if code != "000000" else "111111"
        bad = [
            self._confirm(wrong),
            self._confirm(code, email="nobody@example.com"),
            self.client.post(f"{BASE}/verify/", {"email": "ann@example.com"}, format="json"),
        ]
        for r in bad:
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.json(), {"detail": "Invalid or expired code"})

    def test_five_wrong_guesses_drop_the_code(self):
        self._request()
        code = _code_from_outbox()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(pr.OTP_MAX_ATTEMPTS):
            self.assertEqual(self._confirm(wrong).status_code, 400)
        self.assertEqual(self._confirm(code).status_code, 400)      # even the right code is gone
        self.user.refresh_from_db()
        self.assertEqual(self.user.reset_otp_hash, "")

    def test_an_expired_code_is_refused(self):
        self._request()
        code = _code_from_outbox()
        User.objects.filter(pk=self.user.pk).update(reset_otp_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self._confirm(code).status_code, 400)

    def test_a_second_request_inside_a_minute_sends_nothing_new(self):
        self._request()
        self._request()
        self.assertEqual(len(mail.outbox), 1)
        User.objects.filter(pk=self.user.pk).update(reset_otp_sent_at=timezone.now() - timedelta(seconds=61))
        self._request()
        self.assertEqual(len(mail.outbox), 2)

    def test_password_rules(self):
        self._request()
        code = _code_from_outbox()
        self.assertEqual(self._confirm(code, new="short", confirm="short").status_code, 400)
        self.assertEqual(self._confirm(code, new="a-new-password", confirm="different-one").status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("old-password"))
        self.assertEqual(self._confirm(code).status_code, 200)       # the code survived those refusals

    def test_deactivated_accounts_and_test_inboxes_get_no_code(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self._request()
        User.objects.create_user(username="t1", password="x", email="test-7@mailinator.com")
        self._request("test-7@mailinator.com")
        self.assertEqual(len(mail.outbox), 0)

    def test_an_address_shared_by_two_accounts_is_ambiguous_and_gets_no_code(self):
        User.objects.create_user(username="ann2", password="x", email="ANN@example.com")
        self._request()
        self.assertEqual(len(mail.outbox), 0)

    def test_dry_run_logs_instead_of_sending(self):
        with override_settings(EMAIL_DRY_RUN=True):
            self._request()
        self.assertEqual(len(mail.outbox), 0)
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.reset_otp_hash, "")

    def test_the_code_is_stored_only_as_a_hash(self):
        self._request()
        code = _code_from_outbox()
        self.user.refresh_from_db()
        self.assertNotIn(code, self.user.reset_otp_hash)

    def test_a_mail_failure_does_not_reveal_anything(self):
        with patch("accounts.password_reset.send_mail", side_effect=OSError("down")):
            self.assertEqual(self._request().json(), {"message": pr.REQUEST_MESSAGE})


class SignUpAndAdminTests(TestCase):
    def test_sign_up_makes_students_and_faculty_but_never_an_admin(self):
        client = APIClient()
        for role in ("student", "faculty"):
            r = client.post("/meridian/api/auth/register/", {
                "username": f"new_{role}", "email": f"{role}@example.com",
                "password": "a-long-password", "role": role}, format="json")
            self.assertEqual(r.status_code, 201, r.content)
        r = client.post("/meridian/api/auth/register/", {
            "username": "sneaky", "email": "s@example.com",
            "password": "a-long-password", "role": "admin"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(User.objects.filter(username="sneaky").exists())

    def test_set_admin_by_email_creates_then_updates_and_can_sign_in(self):
        from django.core.management import call_command
        call_command("set_admin", email="Boss@Example.com", password="first-long-pass")
        u = User.objects.get(email="boss@example.com")
        self.assertEqual(u.role, Role.ADMIN)
        self.assertFalse(u.is_superuser)
        login = lambda who, pw: APIClient().post(
            "/meridian/api/auth/login/", {"username": who, "password": pw}, format="json")
        self.assertEqual(login("boss@example.com", "first-long-pass").status_code, 200)
        self.assertEqual(login("BOSS@example.com", "first-long-pass").status_code, 200)
        call_command("set_admin", email="boss@example.com", password="second-long-pass")
        self.assertEqual(User.objects.filter(email="boss@example.com").count(), 1)
        self.assertEqual(login("boss@example.com", "first-long-pass").status_code, 400)
        self.assertEqual(login("boss@example.com", "second-long-pass").status_code, 200)

    def test_set_admin_promotes_an_existing_account_with_that_email(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        User.objects.create_user(username="ann", email="ann@example.com", password="x-old-password", role=Role.FACULTY)
        call_command("set_admin", email="ann@example.com", password="brand-new-pass")
        self.assertEqual(User.objects.get(username="ann").role, Role.ADMIN)
        self.assertEqual(User.objects.count(), 1)
        with self.assertRaises(CommandError):
            call_command("set_admin", email="not-an-email", password="brand-new-pass")
        with self.assertRaises(CommandError):
            call_command("set_admin", email="x@example.com", password="123")

    def test_email_login_is_ambiguous_safe(self):
        User.objects.create_user(username="a1", email="same@example.com", password="pw-long-enough")
        User.objects.create_user(username="a2", email="same@example.com", password="pw-long-enough")
        r = APIClient().post("/meridian/api/auth/login/",
                             {"username": "same@example.com", "password": "pw-long-enough"}, format="json")
        self.assertEqual(r.status_code, 400)
