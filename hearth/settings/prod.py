"""Production settings. Requires DATABASE_URL (PostgreSQL) and a real SECRET_KEY."""
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

DEBUG = False

# A prod deploy that forgets SECRET_KEY entirely, or leaves the dev
# placeholder in place, must not boot — signing/session/CSRF security all
# derive from this value (security review 2026-07, finding B2).
if not SECRET_KEY or SECRET_KEY == "dev-insecure-change-me":  # noqa: F405
    raise ImproperlyConfigured(
        "SECRET_KEY must be set to a real secret in production — refusing to "
        "start with the dev placeholder or an empty value.")

# Security hardening (baseline; full Section 6 controls are a later iteration).
# SSL redirect is env-gated so the app runs behind a TLS-terminating proxy in
# production, but defaults SECURE — an operator running genuinely plain-HTTP
# (e.g. a local compose trial) must now opt OUT explicitly rather than a
# missing env var silently opting everyone OUT of HSTS/secure cookies
# (security review 2026-07, finding I2).
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)  # noqa: F405
SESSION_COOKIE_SECURE = env.bool("COOKIE_SECURE", default=True)  # noqa: F405
CSRF_COOKIE_SECURE = env.bool("COOKIE_SECURE", default=True)  # noqa: F405
SECURE_HSTS_SECONDS = 31536000 if SECURE_SSL_REDIRECT else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
