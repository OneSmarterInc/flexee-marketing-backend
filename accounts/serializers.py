from django.contrib.auth import authenticate
from rest_framework import serializers

from .models import Role, User


class UserSerializer(serializers.ModelSerializer):
    is_faculty = serializers.BooleanField(read_only=True)
    is_admin = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = ("id", "username", "email", "role", "is_faculty", "is_admin",
                  "is_staff", "is_superuser", "first_name", "last_name")
        read_only_fields = ("is_staff", "is_superuser")


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ("id", "username", "email", "password", "role", "first_name", "last_name")

    def validate_role(self, value):
        if value not in Role.values:
            raise serializers.ValidationError("invalid role")
        # Anyone can reach this endpoint, so it can never mint an admin. Admins are set from
        # the server with `manage.py set_admin` (or promoted by an existing admin).
        if value == Role.ADMIN:
            raise serializers.ValidationError("admin accounts cannot be created here")
        return value

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        login = attrs["username"].strip()
        username = login
        # Signing in with an email works too (admins are created with just an email). An exact
        # username match wins; otherwise the email must belong to exactly one account.
        if "@" in login and not User.objects.filter(username=login).exists():
            matches = list(User.objects.filter(email__iexact=login)[:2])
            if len(matches) == 1:
                username = matches[0].username
        user = authenticate(username=username, password=attrs["password"])
        if not user:
            raise serializers.ValidationError("invalid credentials")
        attrs["user"] = user
        return attrs
