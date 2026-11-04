from django.contrib import admin
from django.urls import include, path

from reporting.urls import api_urlpatterns as reporting_api, root_urlpatterns as reporting_root

urlpatterns = [
    path("admin/", admin.site.urls),
    *reporting_root,                                   # /.well-known/... at the host root
    path("meridian/api/auth/", include("accounts.urls")),
    path("meridian/api/", include(reporting_api)),
    path("meridian/api/", include("games.urls")),
]
