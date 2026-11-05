"""Seed a demo game: one faculty, four teams with a student each, config from defaults.

    python manage.py seed_demo

Idempotent-ish: re-running creates a fresh game named "Demo Game N".
"""

from django.core.management.base import BaseCommand

from accounts.models import Role, User
from flexee_engine import api
from games import models


class Command(BaseCommand):
    help = "Create a demo game with faculty + four teams for local testing."

    def handle(self, *args, **options):
        faculty, created = User.objects.get_or_create(
            username="faculty", defaults={"email": "faculty@example.com",
                                          "role": Role.FACULTY})
        if created:
            faculty.set_password("facultypass123")
            faculty.save()
            self.stdout.write("created faculty / facultypass123")

        n = models.Game.objects.count() + 1
        config = api.default_config()
        game = models.Game.objects.create(
            name=f"Demo Game {n}", num_rounds=14, takeover_round=4,
            config_snapshot=config, reveal_policy=config["reveal_policy"],
            faculty=faculty, status=models.GameStatus.SETUP,
        )

        for label in ("A", "B", "C", "D"):
            team = models.Team.objects.create(game=game, name=f"Team {label}",
                                              members=[f"student_{label.lower()}"])
            uname = f"student_{label.lower()}"
            student, s_created = User.objects.get_or_create(
                username=uname, defaults={"role": Role.STUDENT})
            if s_created:
                student.set_password("studentpass123")
                student.save()
            team.users.add(student)

        self.stdout.write(self.style.SUCCESS(
            f"Seeded '{game.name}' (id={game.id}) with 4 teams. "
            f"Students: student_a..d / studentpass123. Engine mode: {api.ENGINE_MODE}"))
