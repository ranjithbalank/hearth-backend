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

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third party
    "rest_framework",
    "corsheaders",
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
]

MIDDLEWARE = [
    "apps.accounts.security.SecurityHeadersMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
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
}

CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ORIGINS",
    default=["http://127.0.0.1:5173", "http://localhost:5173"],
)

# Hearth domain constants
CURRENCY = "INR"

# Roles for which MFA is mandatory (BRD SR-040). Empty in dev so demo logins work;
# production should enforce e.g. ["Managing Director", "General Manager"].
MFA_ENFORCED_ROLES = env.list("MFA_ENFORCED_ROLES", default=[])
