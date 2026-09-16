"""
Django settings for the Flexee Marketing Management backend.

This is the "developer's layer" described in the handoff docs: accounts/auth, a
database, and the API that wraps the verified (locked) simulation engine. The engine
itself is imported through the ``flexee_engine`` seam (see flexee_engine/README.md).
"""

from pathlib import Path
import os

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env if present (never commit real secrets).
load_dotenv(BASE_DIR / ".env")


def _env_bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).lower() in {"1", "true", "yes", "on"}


# --- Core -------------------------------------------------------------------------
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "dev-insecure-change-me-before-deploying-anything"
)
DEBUG = _env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
if DEBUG and "testserver" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append("testserver")  # Django/DRF test client default host

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third-party
    "rest_framework",
    "rest_framework.authtoken",
    "corsheaders",
    # local
    "accounts",
    "games",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Database ---------------------------------------------------------------------
# SQLite for local dev. Point DATABASE_URL-style env vars at Postgres for production
# (the persistence schema is storage-agnostic per the handoff).
if os.environ.get("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["POSTGRES_DB"],
            "USER": os.environ.get("POSTGRES_USER", "postgres"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- DRF --------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

# --- CORS -------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = [
    o for o in os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:3001,http://localhost:5173",
    ).split(",") if o
]
CORS_ALLOW_CREDENTIALS = True

# --- Email + invite links ---------------------------------------------------------
# Student invites are emailed a join link. Delivery backend is chosen by MAIL_BACKEND:
#   mailjet -> Mailjet Send API (config.mailjet_backend, needs MAILJET_API_KEY/SECRET)
#   smtp    -> SMTP (needs EMAIL_HOST/…)
#   else    -> console backend, so dev/CI log the message instead of failing.
# FRONTEND_URL builds the join link students receive.
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000").rstrip("/")

MAILJET_API_KEY = os.environ.get("MAILJET_API_KEY", "")
MAILJET_API_SECRET = os.environ.get("MAILJET_API_SECRET", "")
MAIL_FROM_ADDRESS = os.environ.get("MAIL_FROM_ADDRESS", "no-reply@flexee.local")
MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "Flexee")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", f"{MAIL_FROM_NAME} <{MAIL_FROM_ADDRESS}>")

_MAIL_BACKEND = os.environ.get("MAIL_BACKEND", "").lower()
if _MAIL_BACKEND == "mailjet":
    EMAIL_BACKEND = "config.mailjet_backend.EmailBackend"
elif _MAIL_BACKEND == "smtp" or os.environ.get("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = _env_bool("EMAIL_USE_TLS", True)
else:
    EMAIL_BACKEND = os.environ.get(
        "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")

# --- Flexee engine + LLM ----------------------------------------------------------
# The AI voice layer (personas/advisors/defense grader) is driven by a model client.
# Defaults to Anthropic on the cheapest current model (Claude Haiku 4.5 — $1/$5 per MTok),
# so LLM spend stays minimal. Without an ANTHROPIC_API_KEY (or the `anthropic` SDK), the
# client construction fails and get_model_client() falls back to the deterministic
# "offline" client — so the app still runs keyless in dev/CI.
# NOTE: FLEXEE_LLM_MODEL must be set explicitly; the vendored model_client.py otherwise
# defaults the anthropic provider to a pricier Sonnet, which we do not want.
# See flexee_engine/README.md and the (hash-verified) model_client.py.
FLEXEE_LLM_PROVIDER = os.environ.get("FLEXEE_LLM_PROVIDER", "anthropic")
FLEXEE_LLM_MODEL = os.environ.get("FLEXEE_LLM_MODEL", "claude-haiku-4-5")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "flexee": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
