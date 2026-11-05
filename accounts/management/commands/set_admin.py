"""Create or update an admin account from the server.

    python manage.py set_admin --username priya --email priya@school.edu
    python manage.py set_admin --username priya --email priya@school.edu --password '...'

If the username exists it becomes an admin with the new email and password; otherwise it is
created. Leave --password off to be asked for it (nothing lands in shell history). Admin is an
application role, not a Django superuser.
"""

import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email

from accounts.models import Role, User


class Command(BaseCommand):
    help = "Create an admin, or set the email and password of an existing user and make them admin."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", required=True)
        parser.add_argument("--password", help="Omit to be prompted.")

    def handle(self, *args, **options):
        username = options["username"].strip()
        email = options["email"].strip().lower()
        try:
            validate_email(email)
        except ValidationError:
            raise CommandError(f"'{email}' is not a valid email address.")

        password = options["password"]
        if not password:
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Password (again): "):
                raise CommandError("The two passwords do not match.")
        try:
            validate_password(password)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages))

        user, created = User.objects.get_or_create(username=username)
        user.email = email
        user.role = Role.ADMIN
        user.is_active = True
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} admin '{username}' ({email})."))
