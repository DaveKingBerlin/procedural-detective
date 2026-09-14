# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Procedural Detective — single-container production build (Phase8 J)
#
# Stage 1  builds the React/TypeScript/Vite frontend (npm ci + npm run build).
# Stage 2  installs the FastAPI backend, packages the built frontend as
#          STATIC_DIR, runs migrations on controlled startup (entrypoint) and
#          serves API + SPA from ONE container on port 8000.
#
# Runtime posture:
#   - NON-ROOT `app` user (uid 1001) for the server process;
#   - durable SQLite volume at /data (DATABASE_URL default below);
#   - GENERATION_PROVIDER=fake by default (deterministic demo, zero
#     credentials); live mode is opt-in via env (see docs/DEPLOYMENT.md);
#   - /api/v1/health as the mandatory HEALTHCHECK probe.
# ---------------------------------------------------------------------------

# ---------- Stage 1: frontend production build ------------------------------
FROM node:24-alpine AS frontend-build

WORKDIR /build/frontend

# Layer-cache friendly: lockfile + manifest first, `npm ci` then source.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: runtime ------------------------------------------------
FROM python:3.12-slim AS runtime

WORKDIR /app

ENV \
    # Defaults can all be overridden by docker-compose / -e flags.
    DATABASE_URL=sqlite:////data/procedural_detective.db \
    GENERATION_PROVIDER=fake \
    STATIC_DIR=/app/static \
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    # Run the backend from the SOURCE TREE: backend/app/db/session.py derives
    # the Alembic chain path (<backend>/alembic.ini + <backend>/alembic/) from
    # its own __file__, which only exists in the source layout. The pip install
    # below provides third-party deps; PYTHONPATH makes `app` resolve here so
    # migrations-on-startup and /api/v1/readiness see the same files.
    PYTHONPATH=/app/backend

# Backend + Alembic chain. The dev extras include httpx (the live-provider
# adapter imports it at module import time) — required at runtime too.
COPY backend/ ./backend/
RUN pip install --no-cache-dir "./backend[dev]" \
    && rm -rf /root/.cache/pip

# Built SPA -> the directory the backend serves when STATIC_DIR is set.
COPY --from=frontend-build /build/frontend/dist ./static

# Non-root runtime user + durable volume ownership.
RUN useradd --system --uid 1001 --create-home --shell /bin/sh app \
    && mkdir -p /data \
    && chown -R app:app /app /data

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=5)"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]