import os

from django.core.wsgi import get_wsgi_application

# The production Dockerfile always sets this explicitly (ENV
# DJANGO_SETTINGS_MODULE=hearth.settings.prod) — no silent dev-mode default
# here, so a deployment that runs gunicorn some other way and forgets this
# env var fails to boot instead of silently serving with DEBUG=True and
# CORS wide open (security review 2026-07, finding B1). `manage.py` keeps
# its own dev-friendly default for local `runserver` convenience.
if "DJANGO_SETTINGS_MODULE" not in os.environ:
    raise RuntimeError(
        "DJANGO_SETTINGS_MODULE must be set explicitly to run this app via "
        "WSGI — refusing to silently default to dev settings in what looks "
        "like a deployment context.")
application = get_wsgi_application()
