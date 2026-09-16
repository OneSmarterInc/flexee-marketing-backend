from rest_framework import permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Role, User
from .serializers import LoginSerializer, RegisterSerializer, UserSerializer


class IsAdmin(permissions.BasePermission):
    message = "Admin only."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_admin)


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        token, _ = Token.objects.get_or_create(user=user)
        return Response(
            {"token": token.key, "user": UserSerializer(user).data},
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user": UserSerializer(user).data})


class MeView(APIView):
    def get(self, request):
        return Response(UserSerializer(request.user).data)


class UsersView(APIView):
    """Admin-only: list every user (platform administration)."""

    permission_classes = [IsAdmin]

    def get(self, request):
        users = User.objects.order_by("role", "username")
        return Response(UserSerializer(users, many=True).data)


class FacultyListView(APIView):
    """Faculty/admin: list faculty users (for assigning them to a sim). Admins are not
    listed as assignable options; the creator is added automatically on create."""

    def get(self, request):
        if not request.user.is_faculty:
            return Response({"detail": "Faculty only."}, status=status.HTTP_403_FORBIDDEN)
        users = User.objects.filter(role=Role.FACULTY).order_by("username")
        return Response(UserSerializer(users, many=True).data)


class SetRoleView(APIView):
    """Admin-only: change a user's role."""

    permission_classes = [IsAdmin]

    def post(self, request):
        username = request.data.get("username")
        role = request.data.get("role")
        if role not in Role.values:
            raise ValidationError({"role": f"must be one of {Role.values}"})
        user = User.objects.filter(username=username).first()
        if not user:
            raise ValidationError({"username": "unknown user"})
        user.role = role
        user.save(update_fields=["role"])
        return Response(UserSerializer(user).data)


class DeleteUserView(APIView):
    """Admin-only: permanently remove a faculty or student account. Admins cannot be
    deleted here (nor can you delete yourself) — that guards the platform's operators."""

    permission_classes = [IsAdmin]

    def delete(self, request, username):
        user = User.objects.filter(username=username).first()
        if not user:
            raise ValidationError({"username": "unknown user"})
        if user.id == request.user.id:
            raise ValidationError({"detail": "You can't delete your own account."})
        if user.role == Role.ADMIN:
            raise ValidationError({"detail": "Admin accounts can't be deleted here."})
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
