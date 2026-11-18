#!/bin/sh
# Wait for PostgreSQL, then run the web process or the command given to the container.
set -eu
cd /app

mkdir -p /app/media /app/staticfiles
chown app:app /app/media /app/staticfiles

python /app/deploy/check_env.py

if [ -z "${DJANGO_CACHE_URL:-}" ]; then
  DJANGO_CACHE_URL=$(python -c 'import os, urllib.parse; print("redis://:%s@redis:6379/1" % urllib.parse.quote(os.environ["REDIS_PASSWORD"], safe=""))')
  export DJANGO_CACHE_URL
fi

python - <<'PY'
import os
import socket
import sys
import time

host = os.environ["DJANGO_DB_HOST"]
port = int(os.environ.get("DJANGO_DB_PORT", "5432"))
for _ in range(60):
    try:
        with socket.create_connection((host, port), 2):
            sys.exit(0)
    except OSError:
        time.sleep(1)
sys.exit("PostgreSQL не ответил: %s:%s" % (host, port))
PY

if [ "${1:-}" = "web" ]; then
  runuser -u app -- python manage.py migrate --noinput
  runuser -u app -- python manage.py collectstatic --noinput
  exec runuser -u app -- gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${WEB_WORKERS:-2}" \
    --timeout 120 \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile -
fi

exec runuser -u app -- "$@"
