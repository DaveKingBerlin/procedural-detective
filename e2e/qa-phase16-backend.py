"""QA-owned Phase 16 browser-stack backend launcher (e2e; REAL product app).

Runs the FULL shipped ``create_app`` (all routes / middleware / publication /
static behavior) with the canonical Settings stack (env vars + dotenv + safe
defaults) and a FRESH migrated scratch DB — the only difference from a
plain ``uvicorn app.main:app`` is the explicit Alembic migration to head on a
scratch DATABASE_URL (the container entrypoint does the same). No product
code is modified; the QA seam is OPERATOR CONFIGURATION ONLY:

- part (a) — provider defaults: ``GENERATION_PROVIDER`` unset -> fake.
- part (b) — ``GENERATION_PROVIDER=ollama`` + ``OLLAMA_BASE_URL`` pointed at
  the QA-owned fake Ollama server (e2e/qa-phase16-fake-ollama.py) on an
  allowed loopback port, so the REAL endpoint/probe/generation path works
  end-to-end over REAL HTTP.

Usage (via tools/process_guard, as every QA gate):
    python -m tools.process_guard launch --cmd python --args e2e/qa-phase16-backend.py --port 8000 --meta <path>
  with env: DATABASE_URL=sqlite:///<scratch>, CORS_ALLOWED_ORIGINS=...,
            API_PORT=8000, [GENERATION_PROVIDER=ollama OLLAMA_BASE_URL=...]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))


def main() -> int:
    from alembic import command as alembic_command
    from alembic.config import Config as AlembicConfig

    from app.core.config import Settings
    from app.main import create_app

    settings = Settings(
        max_generations_per_session_per_window=64,
        max_generations_global_per_window=400,
    )
    # QA_SKIP_MIGRATE=1: the degraded controlled state needs the app UP but
    # pointed at an UNMIGRATED database (readiness 503 NOT_READY).
    if not os.environ.get("QA_SKIP_MIGRATE"):
        cfg = AlembicConfig(str(REPO_ROOT / "backend" / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", settings.database_url)
        alembic_command.upgrade(cfg, "head")

    app = create_app(settings)
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=settings.api_port, log_level="warning", proxy_headers=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())