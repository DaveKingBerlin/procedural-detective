#!/bin/sh
# Procedural Detective — container entrypoint (Phase8 I2/I3/J).
#
#  1. Prepare the durable volume  (/data, root-owned on first mount).
#  2. Run migrations exactly ONCE before the server starts (alembic upgrade
#     head); any migration failure aborts startup with a non-zero exit.
#  3. Drop privileges to the unprivileged `app` user and serve the API + SPA
#     on 0.0.0.0:8000.
#
# Runs as root ONLY to prepare /data and hand over to the app user; the
# server process itself never runs as root.
set -e

mkdir -p /data
chown -R app:app /data 2>/dev/null || true

# Controlled-startup migrations (idempotent; safe on restart).
su -s /bin/sh -c "python -m alembic -c /app/backend/alembic.ini upgrade head" app

# Serve. `--app-dir` + WORKDIR make the import CWD-independent.
# `--no-proxy-headers` is load-bearing (DEF-094): uvicorn's platform default
# `--proxy-headers` trusts loopback and would rewrite request.client from a
# hostile X-Forwarded-For before the app's TRUST_PROXY-gated identity decision.
# Forwarded headers are honored ONLY by the app (the Caddy edge sets them and
# TRUST_PROXY=true in production); uvicorn must never double-process them.
exec su -s /bin/sh -c "exec uvicorn --app-dir /app app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers" app