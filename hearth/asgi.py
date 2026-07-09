import os

from django.core.asgi import get_asgi_application

# See wsgi.py for why this doesn't silently default to dev settings
# (security review 2026-07, finding B1).
if "DJANGO_SETTINGS_MODULE" not in os.environ:
    raise RuntimeError(
        "DJANGO_SETTINGS_MODULE must be set explicitly to run this app via "
        "ASGI — refusing to silently default to dev settings in what looks "
        "like a deployment context.")
application = get_asgi_application()
