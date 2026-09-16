from django.urls import path

from .views import (
    DeleteUserView, FacultyListView, LoginView, MeView, RegisterView, SetRoleView, UsersView,
)

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("me/", MeView.as_view(), name="me"),
    path("users/", UsersView.as_view(), name="users"),
    path("users/set-role/", SetRoleView.as_view(), name="set-role"),
    path("users/<str:username>/", DeleteUserView.as_view(), name="delete-user"),
    path("faculty/", FacultyListView.as_view(), name="faculty-list"),
]
