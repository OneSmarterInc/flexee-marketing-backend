from rest_framework import permissions


class IsFaculty(permissions.BasePermission):
    message = "Faculty only."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated
                    and request.user.is_faculty)


class IsAdmin(permissions.BasePermission):
    message = "Admin only."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated
                    and request.user.is_admin)


def team_for_user(game, user):
    """The team a student belongs to in this game, or None. Faculty get None (they see all) —
    except in their own practice game, where they play as their own firm."""
    if user.is_faculty:
        from .practice import my_team
        return my_team(game, user)
    return game.teams.filter(users=user).first()


def can_act_for_team(game, user, team) -> bool:
    """Faculty may act for any team; a student only for their own."""
    if user.is_faculty:
        return True
    return game.teams.filter(pk=team.pk, users=user).exists()
