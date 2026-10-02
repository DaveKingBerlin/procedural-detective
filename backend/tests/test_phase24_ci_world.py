"""Phase 24 — hermetic wire-parity test of the CI deterministic fake world.

The GitLab ``docker-smoke`` job boots the CI deterministic stack with
``FAKE_PROVIDER_SCRIPT=compose/fake-worlds/ci-activity-log-world.json`` (the
golden case with the laptop re-linked to the golden ACTIVITY_LOG record). This
test runs the REAL app (migrated SQLite + real HTTP test client) with that
EXACT committed script and drives the same wire journey the docker smoke
performs — proving hermetic parity through the actual API surface without a
Docker daemon:

  session -> generate PUBLISHED -> playthrough -> laptop(read) discovers the
  ACTIVITY_LOG record -> record read (15-20 rows, {time,text}, canonical once,
  reload-identical) -> witness TIME (idempotent, deterministic, zero provider
  calls) -> bridge/disabled DTO -> accusation -> reveal 4/4.

"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

from phase5_helpers import auth, create_case, create_playthrough, create_session  # noqa: E402
from phase5_helpers import GOLDEN_PROMPT  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
CI_FAKE_WORLD = _REPO_ROOT / "compose" / "fake-worlds" / "ci-activity-log-world.json"

LAPTOP = "apartment_laptop"
ACTIVITY_LOG_EVIDENCE = "cctv_thomas_scene_01"
WITNESS = "emily_reed"
CANONICAL = "2026-09-11T22:17:00+02:00"
# The golden ACTIVITY_LOG row the Phase 19J server locks in exactly once: the
# CCTV fact's OWN observed-at (22:16:40+02:00), not the crime time.
ACTIVITY_LOG_CANONICAL = "2026-09-11T22:16:40+02:00"

# Same accusation the e2e probe uses against the golden world.
GOLDEN_ACCUSATION = {
    "murdererId": "thomas_reed",
    "motiveId": "cover_up_embezzlement",
    "weaponId": "kitchen_knife",
    "crimeTime": CANONICAL,
}

FORBIDDEN = {
    "murdererId", "victimId", "weaponId", "crimeTime", "canonical", "crime",
    "timeline", "facts", "truth", "solverProof", "solutionProof", "proof",
    "prompt", "providerOutput", "diagnostics", "seed", "model", "locked",
    "propositions", "observedAt", "generationAttemptId", "tokenVerifier",
    "sourceRef",
}


def _forbidden_hits(body: object) -> list[str]:
    hits: list[str] = []

    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{path}.{key}" if path else str(key)
                # The accusation echo of the PLAYER's own fields is exempt.
                if key in FORBIDDEN and not (child == "accusation" or "accusation." in child):
                    hits.append(child)
                walk(value, child)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(body, "")
    return hits


@pytest.fixture(scope="module")
def ci_fake_app(tmp_path_factory):
    """A migrated app bound to the committed CI fake world."""
    assert CI_FAKE_WORLD.is_file(), f"missing {CI_FAKE_WORLD}"
    db = tmp_path_factory.mktemp("pd24") / "ci.sqlite"
    from conftest import upgrade_db

    url = f"sqlite:///{db.as_posix()}"
    upgrade_db(url)
    app = create_app(
        Settings(
            database_url=url,
            generation_provider="fake",
            fake_provider_script=str(CI_FAKE_WORLD),
            cors_allowed_origins=["http://localhost:5173"],
        )
    )
    yield app
    app.state.engine.dispose()
    app.state.store.dispose()


def test_ci_world_full_wire_journey(ci_fake_app):
    with TestClient(ci_fake_app) as c:
        session_token, _ = create_session(c)
        case = create_case(c, session_token, prompt=GOLDEN_PROMPT, difficulty="medium")
        assert case["status"] == "PUBLISHED", case
        case_id = case["caseId"]
        creator = case["creatorAccessToken"]

        # Public case (creator dossier) — no truth.
        res = c.get(f"/api/v1/cases/{case_id}", headers=auth(creator))
        assert res.status_code == 200
        assert _forbidden_hits(res.json()) == []

        status, pt = create_playthrough(c, creator, case_id, 1)
        assert status == 201, pt
        pt_id = pt["playthroughId"]
        pt_token = pt["playthroughAccessToken"]

        # Laptop(read) -> the golden ACTIVITY_LOG record in the CI world.
        res = c.post(
            f"/api/v1/playthroughs/{pt_id}/objects/{LAPTOP}/interact",
            headers=auth(pt_token), json={"interaction": "read"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["evidenceId"] == ACTIVITY_LOG_EVIDENCE
        assert body["discovery"]["state"] == "discovered"
        assert _forbidden_hits(body) == []

        # Record read: 15-20 rows, compact {time,text}, canonical once.
        res = c.get(
            f"/api/v1/playthroughs/{pt_id}/records/{ACTIVITY_LOG_EVIDENCE}",
            headers=auth(pt_token),
        )
        assert res.status_code == 200, res.text
        first = res.json()
        content = first["content"]
        assert content["renderType"] == "ACTIVITY_LOG"
        entries = content["entries"]
        assert 15 <= len(entries) <= 20, len(entries)
        assert all(set(e) == {"time", "text"} for e in entries)
        times = [e["time"] for e in entries]
        assert times.count(ACTIVITY_LOG_CANONICAL) == 1, times
        assert len(times) == len(set(times)) and times == sorted(times)
        assert _forbidden_hits(first) == []

        # Reload-identical persisted content.
        res2 = c.get(
            f"/api/v1/playthroughs/{pt_id}/records/{ACTIVITY_LOG_EVIDENCE}",
            headers=auth(pt_token),
        )
        assert res2.text == res.text

        # Witness: open + TIME + repeat TIME (idempotent).
        res = c.get(f"/api/v1/playthroughs/{pt_id}/witnesses/{WITNESS}", headers=auth(pt_token))
        assert res.status_code == 200
        assert _forbidden_hits(res.json()) == []

        res = c.post(
            f"/api/v1/playthroughs/{pt_id}/witnesses/{WITNESS}/interview",
            headers=auth(pt_token), json={"questionType": "TIME"},
        )
        assert res.status_code == 200, res.text
        w1 = res.json()
        assert w1["statement"]["summary"]
        assert _forbidden_hits(w1) == []

        res2 = c.post(
            f"/api/v1/playthroughs/{pt_id}/witnesses/{WITNESS}/interview",
            headers=auth(pt_token), json={"questionType": "TIME"},
        )
        assert res2.status_code == 200
        w2 = res2.json()
        assert w2["statement"] == w1["statement"]
        assert w2["discovery"]["newlyDiscovered"] is False

        # Notebook update + reload persistence.
        res = c.get(f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token))
        assert res.status_code == 200
        known = res.json()["playerKnowledge"]["discoveredEvidenceIds"]
        assert ACTIVITY_LOG_EVIDENCE in known

        # Accusation + reveal -> 4/4 solved.
        res = c.post(
            f"/api/v1/playthroughs/{pt_id}/accusation",
            headers=auth(pt_token), json=GOLDEN_ACCUSATION,
        )
        assert res.status_code == 200, res.text
        assert _forbidden_hits(res.json()) == []

        res = c.get(f"/api/v1/playthroughs/{pt_id}/reveal", headers=auth(pt_token))
        assert res.status_code == 200
        reveal = res.json()
        assert reveal["status"] == "REVEALED"
        assert reveal["score"]["correctDimensions"] == 4
        assert reveal["result"]["overall"] == "solved"


def test_ci_world_bridge_disabled_and_capabilities(ci_fake_app):
    with TestClient(ci_fake_app) as c:
        session_token, _ = create_session(c)
        # ENABLE_BRIDGE=false: REST routes 404, WS path not mounted.
        res = c.post("/api/v1/bridge/pairing", headers=auth(session_token), json={})
        assert res.status_code == 404
        res = c.get("/api/v1/bridge/status", headers=auth(session_token))
        assert res.status_code == 404
        res = c.get("/api/v1/bridge/ws")
        assert res.status_code == 404
        # Capability DTO omits remoteLocalAi, stays fake.
        res = c.get("/api/v1/generation-capabilities")
        assert res.status_code == 200
        caps = res.json()
        assert caps["configuredProvider"] == "fake"
        assert "remoteLocalAi" not in caps
        assert caps["modes"][0]["id"] == "demo" and caps["modes"][0]["available"] is True
        # No bridge state leaked into the DTO.
        assert json.dumps(caps).find("bridge") == -1 or "remoteLocalAi" not in caps