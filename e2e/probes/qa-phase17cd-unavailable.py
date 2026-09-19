"""QA-owned Phase 17C/17D Wave-3 privacy/failure spot (REAL, cheap).

Runs the FULL production app (create_app + public API) in a subprocess with
the OPERATOR .env (GENERATION_PROVIDER=ollama, hermes3:8b) but with an env
OVERRIDE OLLAMA_BASE_URL=http://127.0.0.1:1 (the unreachable override; the
REAL operator LAN URL stays a secret and never enters this probe). Proves the
Phase17C §13 FAILURE POLICY on the real surface:

  1. POST /api/v1/cases in ollama mode against the unreachable provider yields
     a SANITIZED provider-unavailable result -- Never a traceback, never a
     fake/demo fallback (no world publishes), never the host/URL in the body;
  2. GET /api/v1/generation-capabilities degrades to local available:false
     with zero host/URL material (Local AI shown unavailable; Demo remains
     separately selectable in the UI -- covered by phase16-modes);
  3. the generated case is NOT served (no partial generated world publishes).

Stdlib + the product's own test/API stack only. QA-owned; writes the
sanitized evidence to e2e/artifacts/qa-phase17cd-unavailable.json.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

# The unreachable override is THE point of this probe (connection refused).
_UNREACHABLE_BASE_URL = "http://127.0.0.1:1"
os.environ["OLLAMA_BASE_URL"] = _UNREACHABLE_BASE_URL
os.environ["OLLAMA_TIMEOUT_SECONDS"] = "10"

_HOST_TOKENS = ("11434", "http://", "https://", "host.docker.internal")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
TRACEBACK_RE = re.compile(r"Traceback|File \"[^\"]+\"|line \d+|OSError|ConnectionRefusedError", re.I)


def _host_hits(text: str) -> list[str]:
    hits = [t for t in _HOST_TOKENS if t in text.lower()]
    if IPV4_RE.search(text):
        hits.append("IPv4")
    return hits


def main() -> int:
    from alembic import command as alembic_command
    from alembic.config import Config as AlembicConfig
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    evidence = {
        "probe": "phase17cd-unavailable",
        "provider": settings_summary_data(),
        "overrideBaseUrl": _UNREACHABLE_BASE_URL,
        "results": {},
    }

    scratch_dir = REPO_ROOT / "e2e" / "artifacts" / "qa-phase17cd-unavailable-scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    db_path = scratch_dir / "unavailable.db"
    database_url = f"sqlite:///{db_path.as_posix()}"
    settings = Settings(
        database_url=database_url,
        generation_provider="ollama",
        cors_allowed_origins=["http://localhost:4173", "http://localhost:5173"],
        max_generations_per_session_per_window=16,
        max_generations_global_per_window=32,
        generation_deadline_seconds=60,
    )
    cfg = AlembicConfig(str(REPO_ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    alembic_command.upgrade(cfg, "head")

    app = create_app(settings)
    client = TestClient(app)

    # -- 1. capability probe degrades honestly ---------------------------------
    caps = client.get("/api/v1/generation-capabilities")
    caps_body = caps.json()
    local = next((m for m in caps_body.get("modes", []) if m.get("id") == "local"), None)
    assert local is not None, "capability DTO must carry the local mode"
    assert local["available"] is False, "unreachable provider must report local unavailable"
    caps_hits = _host_hits(caps.text)
    assert caps_hits == [], f"capability DTO leaked host material: {caps_hits}"
    evidence["results"]["capabilities"] = {
        "status": caps.status_code,
        "localAvailable": local["available"],
        "modes": [m["id"] for m in caps_body.get("modes", [])],
        "hostHits": caps_hits,
    }

    # -- 2. POST /cases -> sanitized provider-unavailable ---------------------
    session = client.post("/api/v1/sessions/anonymous")
    assert session.status_code == 201, f"anonymous session: {session.status_code}"
    created_dto = None
    try:
        case = client.post(
            "/api/v1/cases",
            headers={"Authorization": f"Bearer {session.json()['anonymousSessionToken']}"},
            json={"prompt": "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\n", "difficulty": "medium"},
        )
    except Exception as exc:  # noqa: BLE001 - a hard transport crash is a finding
        evidence["results"]["createCase"] = {
            "raised": f"{type(exc).__name__}: {str(exc)[:200]}",
            "sanitized": False,
        }
        print(json.dumps(evidence, indent=2, sort_keys=True))
        return 2

    body_text = case.text
    traceback_hits = TRACEBACK_RE.findall(body_text)
    host_hits = _host_hits(body_text)
    if case.status_code == 201:
        try:
            created_dto = case.json()
        except ValueError:
            created_dto = None
        published = bool(created_dto and created_dto.get("status") == "PUBLISHED")
        evidence["results"]["createCase"] = {
            "status": case.status_code,
            "dtoStatus": None if created_dto is None else created_dto.get("status"),
            "publishedWorld": published,
            "tracebackHits": traceback_hits,
            "hostHits": host_hits,
        }
        assert not published, "FAILURE POLICY VIOLATION: a world published from an unreachable provider"
    else:
        evidence["results"]["createCase"] = {
            "status": case.status_code,
            "contentType": case.headers.get("content-type", ""),
            "tracebackHits": traceback_hits,
            "hostHits": host_hits,
            "message": body_text[:200],
        }
        assert "application/json" in case.headers.get("content-type", "").lower()
    assert traceback_hits == [], f"response leaked a traceback: {traceback_hits}"
    assert host_hits == [], f"response leaked host/URL material: {host_hits}"

    # -- 3. no partial world publishes -----------------------------------------
    if created_dto is not None:
        get_case = client.get(
            f"/api/v1/cases/{created_dto['caseId']}",
            headers={"Authorization": f"Bearer {created_dto['creatorAccessToken']}"},
        )
        evidence["results"]["getCase"] = {
            "status": get_case.status_code,
            "expected": 404,  # nothing PUBLISHED may ever be served
        }
        assert get_case.status_code == 404, "a non-published case must not be servable"

    out_path = REPO_ROOT / "e2e" / "artifacts" / "qa-phase17cd-unavailable.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2, sort_keys=True))
    print("RESULT: SANITIZED PROVIDER-UNAVAILABLE (no traceback, no fake fallback, no host)")
    # Best-effort scratch cleanup (Windows may briefly hold the DB file).
    try:
        app.state.store.engine.dispose()
    except Exception:  # noqa: BLE001
        pass
    shutil.rmtree(scratch_dir, ignore_errors=True)
    return 0


def settings_summary_data() -> dict:
    """Public-safe summary of the effective provider settings (never the URL)."""
    from app.core.config import Settings

    s = Settings()
    return {
        "generationProvider": s.generation_provider,
        "model": s.ollama_model,
        "timeout": s.ollama_timeout_seconds,
        "numCtx": s.ollama_num_ctx,
        "baseUrlSetByOperatorDotEnv": bool(s.ollama_base_url),
    }


if __name__ == "__main__":
    sys.exit(main())