"""Production settings. Requires DATABASE_URL (PostgreSQL) and a real SECRET_KEY."""
from .base import *  # noqa: F401,F403

DEBUG = False

# Security hardening (baseline; full Section 6 controls are a later iteration).
# SSL redirect is env-gated so the app runs behind a TLS-terminating proxy in
# production (set true) but also under plain-HTTP compose for local trials.
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)  # noqa: F405
SESSION_COOKIE_SECURE = env.bool("COOKIE_SECURE", default=False)  # noqa: F405
CSRF_COOKIE_SECURE = env.bool("COOKIE_SECURE", default=False)  # noqa: F405
SECURE_HSTS_SECONDS = 31536000 if SECURE_SSL_REDIRECT else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
