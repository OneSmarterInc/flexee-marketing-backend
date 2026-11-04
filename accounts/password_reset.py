"""Password reset by an emailed six-digit code.

- The answer to a request is the same whether or not the email belongs to an account, so it
  can't be used to find out who has one.
- A code is valid 15 minutes, can be requested again after 60 seconds, and is dropped after 5
  wrong guesses. Only a hash of it is stored (on the user).
- Every way a code can be wrong (no such account, no code, expired, too many tries, wrong
  code) gives the one message "Invalid or expired code".
- Resetting works once, clears the code, and signs out every existing session.
"""
from __future__ import annotations

import logging
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import send_mail
from django.utils import timezone
from rest_framework.authtoken.models import Token

from .models import User

log = logging.getLogger("flexee.accounts")

OTP_TTL = timedelta(minutes=15)
OTP_RESEND = timedelta(seconds=60)
OTP_MAX_ATTEMPTS = 5
MIN_PASSWORD = 8

REQUEST_MESSAGE = "If that email has an account, a reset code is on its way."
INVALID = "Invalid or expired code"

# Provisioned test accounts live on a public inbox (test-N@mailinator.com) and have a fixed
# password; a code sent there could be read by anyone, so none is ever sent.
_TEST_EMAIL = re.compile(r"^test-\d+@mailinator\.com$", re.IGNORECASE)


def _find(email: str) -> User | None:
    """The one active account with this email, else None (none, or ambiguous)."""
    matches = list(User.objects.filter(email__iexact=(email or "").strip(), is_active=True)[:2])
    return matches[0] if len(matches) == 1 else None


def _email_body(user: User, code: str) -> str:
    minutes = int(OTP_TTL.total_seconds() // 60)
    return (
        f"Hello {user.first_name or user.username},\n\n"
        f"Use this code to set a new password for Flexee Marketing Management. "
        f"It works for {minutes} minutes:\n\n    {code}\n\n"
        f"If you did not ask for this, ignore this email. Your password has not changed.\n"
    )


def request_reset(email: str) -> str:
    """Email a reset code if the address belongs to an account. Always the same answer."""
    user = _find(email)
    if user is None or _TEST_EMAIL.match(user.email or ""):
        return REQUEST_MESSAGE
    now = timezone.now()
    if user.reset_otp_sent_at and now - user.reset_otp_sent_at < OTP_RESEND:
        return REQUEST_MESSAGE
    code = f"{secrets.randbelow(900000) + 100000}"
    user.reset_otp_hash = make_password(code)
    user.reset_otp_expires_at = now + OTP_TTL
    user.reset_otp_attempts = 0
    user.reset_otp_sent_at = now
    user.save(update_fields=["reset_otp_hash", "reset_otp_expires_at",
                             "reset_otp_attempts", "reset_otp_sent_at"])
    try:
        if getattr(settings, "EMAIL_DRY_RUN", False):
            log.info("[dry run] password reset code for user %s", user.pk)
        else:
            send_mail("Your Flexee password reset code", _email_body(user, code),
                      settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
    except Exception:
        log.exception("password reset code for user %s could not be emailed", user.pk)
    return REQUEST_MESSAGE


def check_otp(email: str, otp: str | None) -> User:
    """The user a code belongs to, or ValueError(INVALID). Counts wrong guesses."""
    user = _find(email)
    now = timezone.now()
    if (
        user is None or not otp or not user.reset_otp_hash
        or user.reset_otp_expires_at is None or user.reset_otp_expires_at < now
        or user.reset_otp_attempts >= OTP_MAX_ATTEMPTS
    ):
        raise ValueError(INVALID)
    if not check_password(str(otp).strip(), user.reset_otp_hash):
        attempts = user.reset_otp_attempts + 1
        if attempts >= OTP_MAX_ATTEMPTS:        # too many wrong guesses: drop the code
            user.reset_otp_hash, user.reset_otp_expires_at, user.reset_otp_attempts = "", None, 0
        else:
            user.reset_otp_attempts = attempts
        user.save(update_fields=["reset_otp_hash", "reset_otp_expires_at", "reset_otp_attempts"])
        raise ValueError(INVALID)
    return user


def reset_password(email: str, otp: str | None, new_password: str, confirm_password: str) -> None:
    """Set a new password with a valid code. The code works once."""
    if new_password != confirm_password:
        raise ValueError("Passwords do not match")
    if len(new_password or "") < MIN_PASSWORD:
        raise ValueError(f"Password must be at least {MIN_PASSWORD} characters")
    user = check_otp(email, otp)
    user.set_password(new_password)
    user.reset_otp_hash, user.reset_otp_expires_at, user.reset_otp_attempts = "", None, 0
    user.save()
    Token.objects.filter(user=user).delete()    # sign out every existing session
