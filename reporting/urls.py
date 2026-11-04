from django.urls import path, re_path

from . import views

# Mounted under /meridian/api/ (see config/urls.py).
api_urlpatterns = [
    path("oauth/reporting/register", views.register),
    path("oauth/reporting/token", views.token),
    path("mcp/reporting", views.mcp_endpoint),
    path("oauth/reporting/check", views.Check.as_view()),
    path("oauth/reporting/approve", views.Approve.as_view()),
    path("oauth/reporting/deny", views.Deny.as_view()),
    path("admin/reporting", views.Connections.as_view()),
    path("admin/reporting/connections/<int:pk>/revoke", views.Revoke.as_view()),
]

# Discovery documents live at the host root: the bare form and the form with the resource's path
# added (RFC 9728 / RFC 8414).
root_urlpatterns = [
    re_path(r"^\.well-known/oauth-protected-resource(?:/meridian/api/mcp/reporting)?$", views.protected_resource),
    re_path(r"^\.well-known/oauth-authorization-server(?:/meridian/api/oauth/reporting)?$", views.authorization_server),
]
