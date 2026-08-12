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

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN or self.is_superuser

    @property
    def is_faculty(self) -> bool:
        # admins have every faculty capability
        return self.role in (Role.FACULTY, Role.ADMIN) or self.is_staff or self.is_superuser

    def __str__(self) -> str:
        return f"{self.username} ({self.role})"
