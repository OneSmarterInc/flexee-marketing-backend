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
        user = authenticate(username=attrs["username"], password=attrs["password"])
        if not user:
            raise serializers.ValidationError("invalid credentials")
        attrs["user"] = user
        return attrs
