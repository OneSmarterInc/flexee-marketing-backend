from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r"games", views.GameViewSet, basename="game")

urlpatterns = [
    path("health/", views.health, name="health"),
    path("cron/round-tick/", views.cron_round_tick, name="cron-round-tick"),
    path("join/<str:token>/", views.join, name="join"),
    path("reference/personas/", views.reference_personas, name="reference-personas"),
    path("reference/advisors/", views.reference_advisors, name="reference-advisors"),
    path("reference/default-config/", views.default_config, name="default-config"),
    path("", include(router.urls)),
]
