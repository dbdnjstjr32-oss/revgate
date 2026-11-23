#!/bin/sh
# Apply migrations as the schema owner, then serve as the restricted app role.
set -eu
cd /app/apps/api
echo "[api] alembic upgrade head"
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips='*'
