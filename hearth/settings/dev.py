"""Development settings."""
from .base import *  # noqa: F401,F403

DEBUG = True
# Permissive CORS in dev so the Vite server connects without fuss.
CORS_ALLOW_ALL_ORIGINS = True
