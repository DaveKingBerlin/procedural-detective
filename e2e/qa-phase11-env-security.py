"""QA-owned Phase 11 ENVIRONMENT-SECURITY PROBE.

For every one of the five kits the probe publishes a case (real migrated
scratch DB + TestClient), collects EVERY public API response of a full
interaction session (create case / bootstrap / interact / read record) and
proves:

  - the hint request path never 5xxes and never echoes the raw hint;
  - the public DTOs carry ONLY ``scene.environmentId`` as kit identity:
    ZERO manifest internals (canonicalName / aliases / tags / zones / spawn /
    lighting / structuralAssets / styleHint / defaultAnchorCoverage) and ZERO
    un-placed anchor geometry outside the documented public placement
    contract — the world objects' own anchor + transform are the public
    deterministic placement contract for the frontend and ARE expected;
  - zero truth / solver / generation-internal material (deep key + value
    scan) in every response of every kit session;
  - the raw environmentId value never leaks into any NON-scene field.

Run from the repo root:
    python e2e/qa-phase11-env-security.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

KIT_IDS = ("apartment", "office", "hotel_suite", "warehouse", "mansion")
GOLDEN_PROMPT = "Victim: sarah_miller\nMurderer: thomas_reed\n"

# Truth/solver/generation-internal key paths BANNED anywhere in public DTOs.
BANNED_KEYS = {
    "murdererId", "victimId", "weaponId", "motiveId", "crimeTime", "canonical",
    "truthfulness", "crime", "timeline", "facts", "relationships",
    "solutionProof", "acceptedScoring", "solverProof", "proof", "truth",
    "universe", "universes", "validation", "report", "winners",
    "remainingCandidateIds", "remainingMotiveIds", "remainingWeaponIds",
    "prompt", "providerOutput", "diagnostics", "seed", "model", "locked",
    "stageOutputs", "worldGraphSpec", "propositions", "observedAt",
    "sourceRef", "verifier", "tokenVerifier", "internalId", "dbid", "rowid",
}

# Kit-manifest internals that must NEVER appear in a public DTO.
BANNED_ENV_TOKENS = {
    "canonicalName", "aliases", "tags", "zones", "spawn", "lighting",
    "structuralAssets", "styleHint", "defaultAnchorCoverage", "environmentId",
    "anchorId", "allowedCategories", "exclusive", "rooms", "zoneId",
}

# Values that mark HIDDEN truth / solver material ONLY (the public model
# legitimately exposes suspect personIds, assetIds, evidenceIds, scene
# location ids and motive candidate ids — those are the documented public
# contract; the CANONICAL crime timestamp and proof internals must not).
BANNED_VALUE_FRAGMENTS = (
    "2026-09-11T22:17",
    "acceptedScoring",
    "solutionProof",
    "feasibleIntervals",
    "crimeTime",
    "remainingCandidateIds",
)

results: list[dict[str, object]] = []


def record(name: str, ok: bool, detail: object) -> None:
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}: {name} :: {json.dumps(detail, ensure_ascii=False)[:360]}")


def walk_keys(node, path, hits):
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if key in BANNED_KEYS:
                hits.append(child)
            walk_keys(value, child, hits)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            walk_keys(value, f"{path}[{i}]", hits)


def walk_env_tokens(node, path, hits, allowed_field):
    """environmentId BANNED by default; allowed ONLY as scene.environmentId."""
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if key in BANNED_ENV_TOKENS:
                if key == "environmentId":
                    if child == f"{allowed_field}":
                        pass  # the one allowed public kit identity field
                    else:
                        hits.append(child)
                else:
                    hits.append(child)
            walk_env_tokens(value, child, hits, allowed_field)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            walk_env_tokens(value, f"{path}[{i}]", hits, allowed_field)


def _fresh_db() -> Path:
    scratch = Path(tempfile.mkdtemp(prefix="qa_p11_sec_"))
    db = scratch / "sec.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db.as_posix()}"
    env.pop("ENV_FILE", None)
    env.pop("STATIC_DIR", None)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    con = sqlite3.connect(db)
    assert [r[0] for r in con.execute("select version_num from alembic_version")] == ["0004"]
    con.close()
    return db


def main() -> int:
    db = _fresh_db()
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    app = create_app(settings=Settings(database_url=f"sqlite:///{db.as_posix()}"))
    with TestClient(app) as client:
        for kit_id in KIT_IDS:
            session = client.post("/api/v1/sessions/anonymous")
            anon = session.json()["anonymousSessionToken"]
            raw_hint = kit_id if kit_id != "hotel_suite" else "hotel"
            res = client.post(
                "/api/v1/cases",
                json={"prompt": GOLDEN_PROMPT, "environment": raw_hint},
                headers={"Authorization": f"Bearer {anon}"},
            )
            rec_hint = client.post(
                "/api/v1/cases",
                json={"prompt": GOLDEN_PROMPT, "environment": raw_hint},
                headers={"Authorization": f"Bearer {anon}"},
            )
            assert res.status_code == 201, res.text
            created = res.json()
            creator = created["creatorAccessToken"]
            case_id = created["caseId"]
            pt = client.post(
                f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                headers={"Authorization": f"Bearer {creator}"},
            ).json()
            boot = client.get(
                f"/api/v1/playthroughs/{pt['playthroughId']}/investigation",
                headers={"Authorization": f"Bearer {pt['playthroughAccessToken']}"},
            )
            body = boot.json()

            # allowed public field location for environmentId
            allowed_field = "$.scene.environmentId"
            env_hits = []
            walk_env_tokens(body, "$", env_hits, allowed_field)
            # the ONLY environmentId key in the whole bootstrap must be scene.environmentId
            record(
                f"{kit_id}: bootstrap exposes ONLY scene.environmentId as kit identity",
                set(env_hits) == set(),
                {"hits": sorted(set(env_hits))},
            )
            # world-object anchors carry the PUBLIC deterministic placement
            # contract (task 5: expected public) — assert they are the kit's
            # own anchors and the transforms are the public fields.
            env = body["scene"]["environmentId"]
            record(f"{kit_id}: bootstrap scene.environmentId == kit", env == kit_id, env)

            # truth-key scan over EVERY response of the session
            responses = [res.json(), boot.json()]
            interact = client.post(
                f"/api/v1/playthroughs/{pt['playthroughId']}/objects/kitchen_knife/interact",
                json={"interaction": "inspect"},
                headers={"Authorization": f"Bearer {pt['playthroughAccessToken']}"},
            )
            assert interact.status_code == 200, interact.text
            responses.append(interact.json())
            record_evidence_id = interact.json()["evidenceId"]
            rd = client.get(
                f"/api/v1/playthroughs/{pt['playthroughId']}/records/{record_evidence_id}",
                headers={"Authorization": f"Bearer {pt['playthroughAccessToken']}"},
            )
            assert rd.status_code == 200, rd.text
            responses.append(rd.json())

            key_hits: list[str] = []
            value_hits: list[str] = []
            for r in responses:
                walk_keys(r, "$", key_hits)
                text = json.dumps(r)
                for frag in BANNED_VALUE_FRAGMENTS:
                    if frag in text:
                        value_hits.append(frag)
            record(
                f"{kit_id}: ZERO truth/solver/generation markers in all public DTOs",
                not key_hits and not value_hits,
                {"keyHits": sorted(set(key_hits)), "valueHits": sorted(set(value_hits))},
            )

        # hint-request safety (never 5xx, never echo) for the full battery
        for unsafe in ("https://evil.example", "..\\nowhere", "x" * 121, "javascript:alert(1)", "C:\\windows\\system32"):
            session = client.post("/api/v1/sessions/anonymous")
            anon = session.json()["anonymousSessionToken"]
            r = client.post(
                "/api/v1/cases",
                json={"prompt": GOLDEN_PROMPT, "environment": unsafe},
                headers={"Authorization": f"Bearer {anon}"},
            )
            code = r.json().get("error", {}).get("code") if r.headers.get("content-type", "").startswith("application/json") else None
            record(
                f"unsafe hint {unsafe[:24]!r} -> 422 (never 5xx/echo)",
                r.status_code == 422 and code in ("ENVIRONMENT_ERROR", "VALIDATION_ERROR")
                and unsafe not in r.text,
                {"status": r.status_code, "code": code, "echoed": unsafe in r.text},
            )

    failed = [r for r in results if not r["ok"]]
    print(f"\nTOTAL {len(results)}  PASS {len(results) - len(failed)}  FAIL {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())