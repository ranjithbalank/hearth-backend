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

# Real mail in production, and a real messaging adapter to reach it. Both stay
# overridable by env so a deploy can point at a gateway (SendGrid/SES/MSG91)
# instead — but the defaults here must not be the dev console backend and the
# mock provider, which between them make a password-reset email look like it
# was sent and deliver nothing.
EMAIL_BACKEND = env(  # noqa: F405
    "EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
MESSAGING_PROVIDER = env(  # noqa: F405
    "MESSAGING_PROVIDER", default="apps.integrations.providers.EmailMessagingProvider")

# SMTP host is required once the SMTP backend is in play — an empty host fails
# at send time, i.e. when a locked-out user is waiting for the mail, which is
# the worst possible moment to discover it.
if EMAIL_BACKEND.endswith("smtp.EmailBackend") and not EMAIL_HOST:  # noqa: F405
    raise ImproperlyConfigured(
        "EMAIL_HOST must be set in production (password reset sends mail), or "
        "set EMAIL_BACKEND/MESSAGING_PROVIDER to a gateway adapter instead.")
