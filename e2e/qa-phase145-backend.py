"""QA-owned Phase 14_5 browser-stack backend launcher (e2e; dev-mode wiring).

The Phase 14_5 unseen-object pipeline needs an ``AssetSpecProvider`` behind
the narrow provider interface. The product ships a DETERMINISTIC app-owned
provider for the four Phase 13 showcase objects (``KnownObjectSpecProvider``);
the three Phase 14_5 unseen fixtures are TEST fixtures on purpose (they must
NOT exist in any production lookup). For the BROWSER E2E the QA stack
therefore runs the REAL ``create_app``/``GenerationService``/public API with a
deterministic provider scripted with the fixture specs — exactly the same
"deterministic fake provider" seam the Phase 13 mandate prescribes
("mandatory tests use a deterministic fake AssetSpec provider"; a live model
adapter is a later-phase item). No product code is modified.

Wiring performed (QA-owned, e2e/):
  1. ``Settings`` read from the canonical env vars (DATABASE_URL,
     CORS_ALLOWED_ORIGINS, API_PORT) with test-scale admission limits
     (64 generations/session, 400/global — the documented QA limits);
  2. migration to head (alembic, same as every QA gate);
  3. ``create_app(settings)`` — the FULL production app (routes/middleware/
     publication/static) with zero product modifications;
  4. ``app.state.generation_service`` replaced with a ``GenerationService``
     whose ``spec_provider`` is a ``FakeAssetSpecProvider`` scripted with
     ``fixtures.asset_specs_unseen.UNSEEN_SPEC_CONTENT`` (the three Phase
     14_5 unseen specs) and a fresh ``GeneratedAssetCache`` — the ONLY
     difference from the shipped service;
  5. uvicorn serving the app on the configured API_PORT.

Usage (via tools/process_guard, as every QA gate):
    python -m tools.process_guard launch --cmd python --args e2e/qa-phase145-backend.py --port 8000 --meta <path>
  with env: DATABASE_URL=sqlite:///<scratch>, CORS_ALLOWED_ORIGINS=..., API_PORT=8000
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "backend" / "tests"))


def _build_dev_spec_provider():
    """Chained dev-mode provider: the SHIPPED app-owned showcase provider
    (the four Phase 13 procedural fixtures) first, then the Phase 14_5
    fixture-scripted provider for the three genuinely unseen objects. Both
    halves are deterministic declarative data; the chain keeps every prior
    showcase flow (custom trophy / antique opener / sample rack / desk award)
    AND the unseen-noun path working on the SAME public API."""
    from app.assets.spec_provider import AssetSpecRequest, AssetSpecResponse
    from app.assets.spec_provider import FakeAssetSpecProvider
    from app.world.composer import KnownObjectSpecProvider

    from fixtures.asset_specs_unseen import UNSEEN_SPEC_CONTENT

    class _DevModeProvider:
        def __init__(self):
            self._known = KnownObjectSpecProvider()
            self._unseen = FakeAssetSpecProvider(UNSEEN_SPEC_CONTENT)

        def generate(self, request: AssetSpecRequest) -> AssetSpecResponse:
            known = self._known.generate(request)
            if known.content is not None:
                return known
            return self._unseen.generate(request)

    return _DevModeProvider()


def main() -> int:
    from alembic import command as alembic_command
    from alembic.config import Config as AlembicConfig

    from app.assets.generated_cache import GeneratedAssetCache
    from app.core.config import Settings
    from app.main import create_app
    from app.services.generation import GenerationService

    from fixtures.asset_specs_unseen import UNSEEN_SPEC_CONTENT

    settings = Settings(
        max_generations_per_session_per_window=64,
        max_generations_global_per_window=400,
    )
    # QA_SKIP_MIGRATE=1: the degraded/backend-down controlled states need the
    # app UP but pointing at an UNMIGRATED database (readiness 503 NOT_READY —
    # same as every prior QA gate's degraded relaunch).
    if not os.environ.get("QA_SKIP_MIGRATE"):
        # 1. migrate the scratch DB to head (idempotent on a fresh file).
        cfg = AlembicConfig(str(REPO_ROOT / "backend" / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", settings.database_url)
        alembic_command.upgrade(cfg, "head")

    # 2. the REAL production app (all routes/middleware/publication).
    app = create_app(settings)

    # 3. replace ONLY the spec-provider wiring (QA dev-mode seam).
    app.state.generation_service = GenerationService(
        settings=settings,
        store=app.state.store,
        clock=app.state.clock,
        publication=app.state.publication_service,
        spec_provider=_build_dev_spec_provider(),
        generated_cache=GeneratedAssetCache(),
    )

    # 4. serve the public API (uvicorn, same bind the shipped launcher uses).
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=settings.api_port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())