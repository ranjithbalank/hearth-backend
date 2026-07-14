# Hearth backend — Django + DRF served by gunicorn
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=hearth.settings.prod

WORKDIR /app

# System deps for psycopg
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a dedicated non-root user, not root (security review 2026-07,
# finding I1) — any future RCE-class bug is then confined to this user's
# privileges instead of root-in-container.
RUN useradd --create-home --shell /bin/bash appuser && chown -R appuser:appuser /app
USER appuser

# Collect static at build (WhiteNoise serves them)
RUN SECRET_KEY=build-only DATABASE_URL=sqlite:////tmp/build.sqlite3 \
    python manage.py collectstatic --noinput || true

EXPOSE 8000

# Run migrations, seed (idempotent) then start gunicorn.
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn hearth.wsgi:application --bind 0.0.0.0:8000 --workers 3"]
