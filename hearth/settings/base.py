"""Base Django settings for Hearth — shared across dev and prod."""
from pathlib import Path

import dj_database_url
import environ

# backend/
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# Load .env if present (next to manage.py)
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

SECRET_KEY = env("SECRET_KEY", default="dev-insecure-change-me")
DEBUG = env.bool("DEBUG", default=True)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["127.0.0.1", "localhost"])
# Public base URL of the frontend — printed on bills (feedback QR/link, order status).
FRONTEND_BASE_URL = env("FRONTEND_BASE_URL", default="http://localhost:5173")

# Outbound mail — carries the password-reset link, so it is the difference
# between "forgot password" working and silently doing nothing.
#
# Dev prints the message to the console: a developer can copy the reset link
# straight out of the runserver output, and nothing is transmitted by accident.
# Prod overrides EMAIL_BACKEND to SMTP (see prod.py). MESSAGING_PROVIDER is the
# separate switch that decides whether notify() reaches Django's mail layer at
# all — it defaults to the mock, which sends nothing on any channel.
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="Hearth <no-reply@hearth.local>")
# Which adapter notify() sends through. Mock = nothing leaves the machine.
MESSAGING_PROVIDER = env(
    "MESSAGING_PROVIDER", default="apps.integrations.providers.MockMessagingProvider")

# Encrypts aggregator (Swiggy/Zomato) webhook secrets at rest (apps.pos.crypto).
# Dev derives a stable key from SECRET_KEY (so stored secrets survive a dev
# server restart); prod MUST set its own AGGREGATOR_SECRET_KEY env var —
# deriving from SECRET_KEY is a dev convenience, not a production practice.
def _dev_fernet_key():
    import base64
    import hashlib
    digest = hashlib.sha256(SECRET_KEY.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode()


# `or` (not `default=`) so an explicitly-blank .env entry still falls back —
# same pattern DATABASE_URL uses above — rather than handing Fernet an
# invalid empty-string key.
AGGREGATOR_SECRET_KEY = env("AGGREGATOR_SECRET_KEY", default="") or _dev_fernet_key()


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third party
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "drf_spectacular",
    # local apps
    "apps.accounts",
    "apps.rooms",
    "apps.reservations",
    "apps.frontoffice",
    "apps.housekeeping",
    "apps.pos",
    "apps.tax",
    "apps.crm",
    "apps.reports",
    "apps.revenue",
    "apps.channel",
    "apps.booking",
    "apps.inventory",
    "apps.recipes",
    "apps.procurement",
    "apps.banquets",
    "apps.hr",
    "apps.notifications",
    "apps.integrations",
    "apps.matreq",
    "apps.masters",
]

MIDDLEWARE = [
    "apps.accounts.security.SecurityHeadersMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "hearth.urls"

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

WSGI_APPLICATION = "hearth.wsgi.application"
ASGI_APPLICATION = "hearth.asgi.application"

# Database — SQLite for local dev (no DATABASE_URL), Postgres in prod via DATABASE_URL.
_default_sqlite = f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL", default="") or _default_sqlite,
        conn_max_age=600,
    )
}

AUTH_USER_MODEL = "accounts.User"

# Usernames are stored lower-case and compared without regard to capitals, so
# one person has exactly one login however they type it (apps/accounts/
# auth_backends.py explains the fallback rules for older mixed-case accounts).
AUTHENTICATION_BACKENDS = ["apps.accounts.auth_backends.CaseInsensitiveUsernameBackend"]

# Local-memory cache backs DRF throttling in dev; swap for Redis in production.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "hearth-cache",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "apps.accounts.security.BreachedPasswordValidator"},
]

LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- DRF + JWT ---
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Anti-brute-force / anti-automation (BRD SR-023, SR-045).
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.ScopedRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "auth": "10/min",      # login / token issuance
        "sensitive": "30/min",  # OTP, coupon/loyalty redemption, etc.
    },
}

from datetime import timedelta  # noqa: E402

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=8),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    # Rotation alone doesn't revoke the old refresh token server-side — a
    # stolen one stays valid for its full 7-day lifetime even after the
    # legitimate client rotates past it. Blacklisting closes that
    # (security review 2026-07, finding B9).
    "BLACKLIST_AFTER_ROTATION": True,
}

CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ORIGINS",
    default=["http://127.0.0.1:5173", "http://localhost:5173"],
)

SPECTACULAR_SETTINGS = {
    "TITLE": "Hearth API",
    "DESCRIPTION": "Hotel & Restaurant OS — REST API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# Hearth domain constants
CURRENCY = "INR"

# Roles for which MFA is mandatory (BRD SR-040). Empty in dev so demo logins work;
# production should enforce e.g. ["Managing Director", "General Manager"].
MFA_ENFORCED_ROLES = env.list("MFA_ENFORCED_ROLES", default=[])
