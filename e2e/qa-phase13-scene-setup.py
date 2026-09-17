"""QA-owned Phase 13 scene setup (transient; e2e/artifacts output).

Drives the REAL GenerationService over the SAME migrated scratch DB the live
backend will serve, with the Phase 13 OPT-IN declarative procedural path
enabled (FakeAssetSpecProvider + generate_unknown_assets), so the published
payload carries 4 real `proc.*` world objects with embedded generated
definitions. Writes the credentials the Playwright spec needs to pin a
playthrough to the case through the PUBLIC API (full chain: service ->
immutable published_versions -> bootstrap projection -> browser).

Usage:
    python e2e/qa-phase13-scene-setup.py <database_url> [<out.json>]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "backend" / "tests"))

from fixtures.asset_specs import GOLDEN_SPEC_CONTENT, GOLDEN_UNKNOWN_REQUESTS  # noqa: E402
from app.assets.generated_cache import GeneratedAssetCache  # noqa: E402
from app.assets.spec_provider import FakeAssetSpecProvider  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.persistence.store import Store  # noqa: E402
from app.services.generation import GenerationService  # noqa: E402


def main() -> int:
    database_url = sys.argv[1]
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO_ROOT / "e2e" / "artifacts" / "phase13-scene-credentials.json"
    settings = Settings(database_url=database_url)
    store = Store(settings.database_url)
    service = GenerationService(
        settings=settings,
        store=store,
        spec_provider=FakeAssetSpecProvider(GOLDEN_SPEC_CONTENT),
        generate_unknown_assets=True,
        generated_cache=GeneratedAssetCache(),
    )
    session = service.create_anonymous_quota_session()
    started = service.start_case_generation(
        "Victim: sarah_miller\nMurderer: thomas_reed\n",
        anonymous_quota_session_id=session.anonymous_quota_session_id,
        difficulty="medium",
        environment="office",
        unknown_asset_requests=list(GOLDEN_UNKNOWN_REQUESTS),
    )
    if started.status != "PUBLISHED":
        print(f"FATAL: case status {started.status!r}, expected PUBLISHED", file=sys.stderr)
        return 2
    payload_json = store.get_published(started.case_id, 1).payload_json
    import json as _json

    payload = _json.loads(payload_json)
    proc_ids = sorted(
        o["object_id"]
        for o in payload["draft"]["objects"]
        if o["asset_id"].startswith("proc.")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "caseId": started.case_id,
                "caseVersion": 1,
                "creatorAccessToken": started.creator_access_token,
                "proceduralObjectIds": proc_ids,
                "environmentId": payload["draft"]["scene"].get("environment_id"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("SETUP_OK", json.dumps(
        {"caseId": started.case_id, "status": started.status,
         "proceduralObjectIds": proc_ids},
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())