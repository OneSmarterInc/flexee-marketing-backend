"""Create or update an admin account from the server, by email and password.

    python manage.py set_admin --email priya@school.edu
    python manage.py set_admin --email priya@school.edu --password '...'

If an account with that email exists it becomes an admin with the new password; otherwise it is
created (its username is the email, and it signs in with the email). Leave --password off to be
asked for it (nothing lands in shell history). Admin is an application role, not a Django
superuser.
"""

import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email

from accounts.models import Role, User


class Command(BaseCommand):
    help = "Create an admin, or set the password of the account with this email and make it admin."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--password", help="Omit to be prompted.")

    def handle(self, *args, **options):
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

        matches = list(User.objects.filter(email__iexact=email))
        if len(matches) > 1:
            raise CommandError(f"{len(matches)} accounts share {email}; resolve that in the admin first.")
        if matches:
            user, created = matches[0], False
        else:
            user, created = User(username=email), True
        user.email = email
        user.role = Role.ADMIN
        user.is_active = True
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} admin {email}. Sign in with that email and the password."))
