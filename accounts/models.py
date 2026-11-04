from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    ADMIN = "admin", "Admin"
    FACULTY = "faculty", "Faculty"
    STUDENT = "student", "Student"


class User(AbstractUser):
    """Accounts + auth are the developer's layer (see the handoff).

    Three roles:
      * student — plays on a team, sees only student-safe output.
      * faculty — configures/steers a game, sees everything in their games.
      * admin — platform administrator: all faculty powers PLUS user management
        (list users, change roles) across every game. NOT a Django superuser.
    Team membership lives on Team.
    """

    role = models.CharField(max_length=16, choices=Role.choices, default=Role.STUDENT)

    # Password reset by emailed code (see accounts/password_reset.py). Only a hash of the
    # code is kept; null/empty when none is outstanding.
    reset_otp_hash = models.CharField(max_length=128, blank=True, default="")
    reset_otp_expires_at = models.DateTimeField(null=True, blank=True)
    # Wrong guesses against the current code; the code is dropped once they reach the limit.
    reset_otp_attempts = models.PositiveSmallIntegerField(default=0)
    reset_otp_sent_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN or self.is_superuser

    @property
    def is_faculty(self) -> bool:
        # admins have every faculty capability
        return self.role in (Role.FACULTY, Role.ADMIN) or self.is_staff or self.is_superuser

    def __str__(self) -> str:
        return f"{self.username} ({self.role})"
