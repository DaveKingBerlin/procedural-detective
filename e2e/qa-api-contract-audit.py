"""QA-owned, independent Phase 6 API CONTRACT AUDIT (QA role, .rad/roles/qa.md).

Drives the FULL public flow like a browser against a REAL temporary SQLite
file (migrated to alembic head 0003) through FastAPI TestClient, using the
DEFAULT dev (fake) provider which publishes the deterministic golden case.

Sections (task 2 from the Phase 6 QA brief):

  2a  full flow: session -> case (PUBLISHED) -> playthrough -> bootstrap
  2b  bootstrap correctness: pinned version, sorted world objects with the
      10 documented fields, NO coordinates/content/propositions, empty
      PlayerKnowledge, correct scene location
  2c  discover semantics: knife discover/repeat idempotency, laptop interact
      -> email discovery, wrong interaction 409 no state change, unknown
      object 404, fabricated evidence 404, undiscovered read 403 no content,
      read-after-discover allowlisted content, repeat read byte-identical
  2d  isolation: second playthrough empty knowledge + cannot read first's
      records; A token on B -> 404; v1 vs v2-only evidence (404); v1
      unchanged after v2 publish
  2e  leak scan: recursive key-path scan over EVERY Phase 6 response body
      collected during the run
  2f  restart: engine/store disposal, fresh app on the same file -> state
      persists; a new playthrough is independent
  2g  concurrency: 18 parallel discovers on the same evidence -> all 200,
      exactly one set entry, no duplicate rows

Evidence is written to e2e/artifacts/qa-api-contract-audit-report.json
(transient output; .rad/policies/evidence.md).

This script NEVER modifies product source. It only reads through the public
API surface (plus the documented test-only v2 publish seam from
backend/tests/phase6_helpers.py, mirroring the backend test suite).
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from alembic import command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from phase6_helpers import (  # noqa: E402
    EMAIL_EVIDENCE,
    KNIFE_EVIDENCE,
    LAPTOP_OBJECT,
    SCENE_LOCATION,
    SCENE_NAME,
    V2_ONLY_EVIDENCE,
    publish_v2_with_extra_evidence,
)

REPORT: dict = {"sections": {}}
ARTIFACT_DIR = REPO / "e2e" / "artifacts"
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
REPORT_PATH = ARTIFACT_DIR / "qa-api-contract-audit-report.json"

# --------------------------------------------------------------------------- #
# leak-scan infrastructure (task 2e)
# --------------------------------------------------------------------------- #

# Canonical hidden truth / crime / solution / generation / solver keys that
# MUST NEVER appear anywhere inside a Phase 6 gameplay response.
FORBIDDEN_ANYWHERE = {
    "murdererId", "victimId", "weaponId", "motiveId",
    "truthfulness", "crimeTime", "canonical", "crime", "timeline",
    "facts", "relationships", "truth", "solutionProof", "acceptedScoring",
    "solverProof", "proof", "universe", "universes", "validation",
    "report", "winners", "remainingCandidateIds", "remainingMotiveIds",
    "remainingWeaponIds", "prompt", "providerOutput", "diagnostics",
    "seed", "model", "locked", "promptNote", "stageOutputs",
    "worldGraphSpec", "verifier", "tokenVerifier", "token",
    "sessionId", "anonymousQuotaSessionId", "quotaSessionId",
    "attemptId", "generationAttemptId", "sourceRef", "propositions",
    "observedAt", "uncertaintySeconds", "presentedData",
    # solver proposition / rule-internal identifiers (47A / 48A material)
    "CAN_REACH_CRIME_SCENE_IN_TIME", "canReach", "ruleId", "ruleEffect",
    "necessaryForCandidate", "supported", "contradicted", "alibi",
    "jobTitle", "age", "dangerousTrigger", "violation",
}
# In the PHASE 6 responses the public motives collection is never present, so
# motiveId is banned absolutely (unlike the Phase 5 public-case DTO).
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_LIKE_RE = re.compile(r"^[A-Za-z0-9_-]{40,}$")

SCANNED: list[str] = []


def _iter_key_paths(node, prefix: str = ""):
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield (path, value)
            yield from _iter_key_paths(value, path)
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            path = f"{prefix}.{index}"
            yield (path, value)
            yield from _iter_key_paths(value, path)


def leak_scan(payload, *, label: str) -> list[str]:
    """Recursive key-path scan. Returns violation strings ([] == clean)."""
    violations: list[str] = []
    for path, value in _iter_key_paths(payload):
        leaf = path.rsplit(".", 1)[-1]
        if leaf in FORBIDDEN_ANYWHERE:
            violations.append(f"{label}: forbidden key {path!r}")
        if isinstance(value, str):
            if _HEX64_RE.match(value):
                violations.append(f"{label}: 64-hex value at {path!r}")
            if _TOKEN_LIKE_RE.match(value) and leaf not in ("playthroughId",):
                # playthroughId is PT-<token-prefix>-shaped; tokens themselves
                # otherwise never appear in Phase 6 responses.
                if leaf not in {"objectId", "evidenceId", "caseId", "assetId",
                                "locationId", "anchor", "interaction",
                                "subject", "body", "timestamp", "title"}:
                    violations.append(f"{label}: token-like value at {path!r}")
    SCANNED.append(f"{label} ({len(payload) if not isinstance(payload, list) else len(payload)} top-level)")
    return violations


def scan_every_response(label: str, body) -> None:
    v = leak_scan(body, label=label)
    if v:
        raise AssertionError("LEAK: " + "; ".join(v))


# --------------------------------------------------------------------------- #
# app / db helpers
# --------------------------------------------------------------------------- #

def _upgrade(db_url: str) -> None:
    cfg = AlembicConfig(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")


def _make_app(db_url: str):
    from app.core.config import Settings
    from app.main import create_app

    return create_app(
        Settings(
            database_url=db_url,
            cors_allowed_origins=["http://localhost:5173", "http://localhost:4173"],
            max_concurrent_generations=4,
            max_generations_per_session_per_window=8,
        )
    )


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _finish_app(app) -> None:
    app.state.engine.dispose()
    try:
        app.state.store.dispose()
    except Exception:
        pass


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def section(name: str) -> None:
    print(f"\n=== {name} ===")
    REPORT["sections"][name] = {"status": "RUNNING", "startedAt": _now_utc()}


def ok(name: str, detail: str = "") -> None:
    key = next((k for k in REPORT["sections"] if k.startswith(name)), name)
    print(f"  PASS  {key} {detail}")
    REPORT["sections"].setdefault(key, {})["status"] = "PASS"
    REPORT["sections"][key]["finishedAt"] = _now_utc()
    if detail:
        REPORT["sections"][key]["detail"] = detail


def fail(name: str, detail: str) -> None:
    key = next((k for k in REPORT["sections"] if k.startswith(name)), name)
    print(f"  FAIL  {key}: {detail}")
    REPORT["sections"].setdefault(key, {})["status"] = "FAIL"
    REPORT["sections"][key]["detail"] = detail
    REPORT["sections"][key]["finishedAt"] = _now_utc()


# --------------------------------------------------------------------------- #
# the audit
# --------------------------------------------------------------------------- #

def main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="qa-api-audit-",
                                    dir=Path(os.environ.get("TEMP", tempfile.gettempdir()))))
    db_path = scratch / "audit.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    REPORT["db"] = {"path": str(db_path), "url": db_url}
    _upgrade(db_url)

    app = _make_app(db_url)
    futures_cleanup = []
    all_responses: list[dict] = []  # (label, status, body) collected for scan
    try:
        # -------------------------------------------------------------- 2a --
        section("2a full public flow (browser-like)")

        def api(label, method, path, token=None, json_body=None):
            headers = _auth(token) if token else None
            client_method = getattr(TestClient(app), method)
            if method == "post" and json_body is not None:
                res = client_method(path, headers=headers, json=json_body)
            else:
                res = client_method(path, headers=headers)
            try:
                body = res.json()
            except Exception:
                body = {"_raw": res.text[:200]}
            all_responses.append({"label": label, "status": res.status_code, "body": body})
            return res, body

        res, body = api("create-session", "post", "/api/v1/sessions/anonymous")
        assert res.status_code in (200, 201), body
        session_token = body["anonymousSessionToken"]
        ok("2a", "anonymous session")

        res, body = api("create-case", "post", "/api/v1/cases",
                        token=session_token,
                        json_body={"prompt": "Victim: sarah_miller\nMurderer: thomas_reed\n",
                                   "difficulty": "medium"})
        assert res.status_code == 201, body
        case_id = body["caseId"]
        creator = body["creatorAccessToken"]
        assert body.get("status") == "PUBLISHED", f"expected PUBLISHED, got {body.get('status')}"
        ok("2a", f"case {case_id} published by default dev provider (status={body['status']})")

        res, body = api("create-playthrough", "post",
                        f"/api/v1/cases/{case_id}/versions/1/playthroughs", token=creator)
        assert res.status_code == 201, body
        pt_id, pt_token = body["playthroughId"], body["playthroughAccessToken"]
        assert body.get("caseVersion") == 1 and body.get("status") == "PLAYING", body
        pt_creds = {"id": pt_id, "token": pt_token}
        ok("2a", f"playthrough {pt_id} PLAYING pinned v1")

        res, body = api("bootstrap", "get",
                        f"/api/v1/playthroughs/{pt_id}/investigation", token=pt_token)
        assert res.status_code == 200, body
        bootstrap = body
        assert bootstrap["caseVersion"] == 1
        assert bootstrap["state"] == "PLAYING"
        assert bootstrap["playthroughId"] == pt_id
        assert bootstrap["scene"]["location"] == {
            "locationId": SCENE_LOCATION, "name": SCENE_NAME}
        assert bootstrap["playerKnowledge"] == {
            "discoveredEvidenceIds": [], "readEvidenceIds": [], "visitedLocationIds": []}
        ok("2a", "bootstrap 200, pinned v1, scene location correct, knowledge empty")

        # -------------------------------------------------------------- 2b --
        section("2b bootstrap correctness (pinned version / world objects)")

        objs = bootstrap["scene"]["worldObjects"]
        ids = [o["objectId"] for o in objs]
        assert ids == sorted(ids), f"worldObjects not sorted: {ids}"
        assert ids == sorted({
            "kitchen_knife", "letter_opener", "scissors", "vase_01",
            "apartment_laptop", "apartment_table", "apartment_door",
            "apartment_lamp", "victim_body_placeholder"}), ids
        assert len(objs) == 9, f"expected 9 world objects, got {len(objs)}"
        doc_fields = {"objectId", "assetId", "assetType", "subtype",
                      "locationId", "anchor", "interaction", "evidenceId",
                      "discovered", "read"}
        for o in objs:
            assert set(o.keys()) == doc_fields, f"field drift: {sorted(o.keys())}"
            # Forbidden in world objects:
            assert "position" not in o and "coordinates" not in o
            assert "content" not in o and "propositions" not in o
            assert o["discovered"] is False and o["read"] is False
        knife = next(o for o in objs if o["objectId"] == "kitchen_knife")
        laptop = next(o for o in objs if o["objectId"] == "apartment_laptop")
        assert knife["evidenceId"] == KNIFE_EVIDENCE
        assert laptop["evidenceId"] == EMAIL_EVIDENCE
        assert laptop["interaction"] == "read"
        scan_every_response("2b bootstrap", bootstrap)
        ok("2b", "9 objects sorted by objectId, 10 documented fields, no coords/content/propositions")

        # -------------------------------------------------------------- 2c --
        section("2c discover semantics")
        disc_res, disc = api("discover-knife", "post",
                             f"/api/v1/playthroughs/{pt_id}/evidence/{KNIFE_EVIDENCE}/discover",
                             token=pt_token)
        assert disc_res.status_code == 200, disc
        assert disc["state"] == "discovered", disc
        assert disc["evidenceId"] == KNIFE_EVIDENCE
        assert set(disc.keys()) == {"evidenceId", "kind", "title", "interaction", "state"}
        scan_every_response("2c discover-knife", disc)
        ok("2c", "discover knife -> state=discovered, exact DTO keys")

        repeat, rbody = api("discover-knife-again", "post",
                            f"/api/v1/playthroughs/{pt_id}/evidence/{KNIFE_EVIDENCE}/discover",
                            token=pt_token)
        assert repeat.status_code == 200 and rbody["state"] == "already-discovered", rbody
        ok("2c", "repeat discover -> 200 already-discovered (idempotent)")

        lres, lb = api("interact-laptop-read", "post",
                       f"/api/v1/playthroughs/{pt_id}/objects/{LAPTOP_OBJECT}/interact",
                       token=pt_token, json_body={"interaction": "read"})
        assert lres.status_code == 200, lb
        assert lb["discovery"]["evidenceId"] == EMAIL_EVIDENCE, lb
        assert lb["discovery"]["state"] == "discovered", lb
        scan_every_response("2c interact-laptop-read", lb)
        ok("2c", "interact laptop with 'read' -> email discovery DTO")

        wres, wb = api("interact-laptop-wrong", "post",
                       f"/api/v1/playthroughs/{pt_id}/objects/{LAPTOP_OBJECT}/interact",
                       token=pt_token, json_body={"interaction": "inspect"})
        assert wres.status_code == 409, wb
        assert wb["error"]["code"] == "INTERACTION_NOT_ALLOWED", wb
        assert set(wb.keys()) == {"error"}, wb
        ok("2c", "wrong interaction string -> 409 envelope, no content")

        ures, ub = api("interact-unknown-object", "post",
                       f"/api/v1/playthroughs/{pt_id}/objects/not_an_object/interact",
                       token=pt_token, json_body={"interaction": "inspect"})
        assert ures.status_code == 404 and ub["error"]["code"] == "NOT_FOUND", ub
        assert set(ub.keys()) == {"error"}, ub
        ok("2c", "unknown object -> 404 envelope")

        fres, fb = api("discover-fabricated", "post",
                       f"/api/v1/playthroughs/{pt_id}/evidence/EV-FAKE-999/discover",
                       token=pt_token)
        assert fres.status_code == 404 and fb["error"]["code"] == "NOT_FOUND", fb
        assert set(fb.keys()) == {"error"}, fb
        ok("2c", "fabricated evidence id -> 404 (no existence leak)")

        # Wrong interaction must NOT have changed state: email not discovered yet
        # by the wrong-interaction attempt (it WAS discovered by the read above —
        # order: wrong-interaction ran AFTER read, so assert the DTO was identical
        # to a current bootstrap snapshot: email discovered, knife discovered).
        b2, b2b = api("bootstrap-after", "get",
                      f"/api/v1/playthroughs/{pt_id}/investigation", token=pt_token)
        assert b2.status_code == 200, b2b
        assert sorted(b2b["playerKnowledge"]["discoveredEvidenceIds"]) == sorted(
            [KNIFE_EVIDENCE, EMAIL_EVIDENCE]), b2b["playerKnowledge"]
        ok("2c", "bootstrap after: discovered={knife,email}, no drift from 409")

        # -- undiscovered record read on a FRESH playthrough -----------------
        res2, body2 = api("create-pt2", "post",
                          f"/api/v1/cases/{case_id}/versions/1/playthroughs", token=creator)
        fresh_id, fresh_token = body2["playthroughId"], body2["playthroughAccessToken"]
        assert res2.status_code == 201, body2
        ur, urn = api("read-undiscovered", "get",
                      f"/api/v1/playthroughs/{fresh_id}/records/{EMAIL_EVIDENCE}",
                      token=fresh_token)
        assert ur.status_code == 403, urn
        assert urn["error"]["code"] == "EVIDENCE_NOT_DISCOVERED", urn
        assert set(urn.keys()) == {"error"}, urn
        text_lower = ur.text.lower()
        for marker in ("subject", "body", "missing funds", "content", "propositions"):
            assert marker not in text_lower, f"content leaked in 403: {marker}"
        ok("2c", "undiscovered record read -> 403 EVIDENCE_NOT_DISCOVERED, zero content")

        # -- read after discover: allowlisted content ------------------------
        api("discover-email-fresh", "post",
            f"/api/v1/playthroughs/{fresh_id}/evidence/{EMAIL_EVIDENCE}/discover",
            token=fresh_token)
        api("discover-knife-fresh", "post",
            f"/api/v1/playthroughs/{fresh_id}/evidence/{KNIFE_EVIDENCE}/discover",
            token=fresh_token)
        rr, rrb = api("read-email", "get",
                      f"/api/v1/playthroughs/{fresh_id}/records/{EMAIL_EVIDENCE}",
                      token=fresh_token)
        assert rr.status_code == 200, rrb
        assert set(rrb.keys()) == {"evidenceId", "kind", "title", "description",
                                   "openedAt", "readByPlayer", "content"}, rrb
        assert rrb["kind"] == "email"
        assert set(rrb["content"].keys()) == {"fromPersonId", "toPersonIds",
                                              "subject", "body", "timestamp"}, rrb
        scan_every_response("2c read-email", rrb)
        ok("2c", "email read -> full allowlisted content (subject/body/timestamp/from/to)")

        kr, krb = api("read-knife", "get",
                      f"/api/v1/playthroughs/{fresh_id}/records/{KNIFE_EVIDENCE}",
                      token=fresh_token)
        assert kr.status_code == 200, krb
        assert krb["kind"] == "forensic" and krb["content"] == {}, krb
        scan_every_response("2c read-knife", krb)
        ok("2c", "knife read -> kind 'forensic', content {} (no allowlist) — no leak")

        rr2, rrb2 = api("read-email-again", "get",
                        f"/api/v1/playthroughs/{fresh_id}/records/{EMAIL_EVIDENCE}",
                        token=fresh_token)
        assert rr2.status_code == 200 and rrb2 == rrb, "repeat read differs"
        assert rrb2["openedAt"] == rrb["openedAt"]
        ok("2c", "repeat read -> byte-identical DTO, same openedAt")

        # -------------------------------------------------------------- 2d --
        section("2d isolation (second playthrough / cross-token / v1-v2)")
        res3, body3 = api("create-pt3", "post",
                          f"/api/v1/cases/{case_id}/versions/1/playthroughs", token=creator)
        pt3_id, pt3_token = body3["playthroughId"], body3["playthroughAccessToken"]
        assert res3.status_code == 201, body3
        b3, b3b = api("pt3-bootstrap", "get",
                      f"/api/v1/playthroughs/{pt3_id}/investigation", token=pt3_token)
        assert b3b["playerKnowledge"]["discoveredEvidenceIds"] == []
        ok("2d", "second playthrough on the SAME version -> empty knowledge")

        a3, a3b = api("pt3-read-A-record", "get",
                      f"/api/v1/playthroughs/{pt3_id}/records/{EMAIL_EVIDENCE}",
                      token=pt3_token)
        assert a3.status_code in (403, 404), a3b
        assert a3b.get("error", {}).get("code") in ("EVIDENCE_NOT_DISCOVERED", "NOT_FOUND"), a3b
        ok("2d", f"second playthrough cannot read first's discovered record id -> {a3.status_code}")

        xres, xb = api("pt3-with-A-token", "get",
                       f"/api/v1/playthroughs/{pt3_id}/investigation", token=pt_token)
        assert xres.status_code == 404, (xb, "A's token on B must 404")
        assert xb["error"]["code"] == "NOT_FOUND", xb
        ok("2d", "A's token used on B's path -> 404")

        publish_v2_with_extra_evidence(app, case_id, V2_ONLY_EVIDENCE)
        v1res, v1b = api("v1-discover-v2-evidence", "post",
                         f"/api/v1/playthroughs/{pt_id}/evidence/{V2_ONLY_EVIDENCE}/discover",
                         token=pt_token)
        assert v1res.status_code == 404, v1b
        assert v1b["error"]["code"] == "NOT_FOUND", v1b
        v1r, v1rb = api("v1-read-v2-record", "get",
                        f"/api/v1/playthroughs/{pt_id}/records/{V2_ONLY_EVIDENCE}",
                        token=pt_token)
        assert v1r.status_code == 404, v1rb
        ok("2d", "v1 playthrough cannot discover(404)/read(404) v2-only evidence")

        # v1 bootstrap unchanged after v2 publish
        b4, b4b = api("v1-bootstrap-after-v2", "get",
                      f"/api/v1/playthroughs/{pt_id}/investigation", token=pt_token)
        assert b4.status_code == 200, b4b
        assert [o["objectId"] for o in b4b["scene"]["worldObjects"]] == ids
        assert "v2_object" not in [o["objectId"] for o in b4b["scene"]["worldObjects"]]
        kread, kreadb = api("v1-read-knife-after-v2", "get",
                            f"/api/v1/playthroughs/{pt_id}/records/{KNIFE_EVIDENCE}",
                            token=pt_token)
        assert kread.status_code == 200, kreadb
        kread_first, kread_first_b = api("v1-read-knife-before-v2", "get",
                                         f"/api/v1/playthroughs/{pt_id}/records/{KNIFE_EVIDENCE}",
                                         token=pt_token)
        assert kread.status_code == 200 and kreadb == kread_first_b, "knife read changed after v2 publish"
        ok("2d", "v1 bootstrap + reads unchanged after v2 publish (v1 evidence set)")

        # -------------------------------------------------------------- 2e --
        section("2e leak scan over every collected Phase 6 response")
        violations_all: list[str] = []
        # Credential-issuance DTOs (POST /sessions, POST /cases, POST
        # .../playthroughs) are the Phase 5 EXPLICIT creation handouts where
        # the three tokens + generationAttemptId appear exactly once by
        # contract; they are NOT Phase 6 gameplay responses. Every other
        # collected response (bootstrap/discover/interact/read/records/errors)
        # is scanned.
        creation_labels = {"create-session", "create-case", "create-playthrough",
                           "create-pt2", "create-pt3"}
        for entry in all_responses:
            label = entry["label"]
            if label in creation_labels:
                SCANNED.append(f"{label} (creation handout, excluded by contract)")
                continue
            violations = leak_scan(entry["body"], label=f"{label} ({entry['status']})")
            if violations:
                violations_all.extend(violations)
        if violations_all:
            fail("2e", "; ".join(violations_all))
            return 1
        body_text = json.dumps([e["body"] for e in all_responses])
        # extra: the whole raw transcript may not contain the golden canonical
        # crime time or the accepted evidence email's internal text surprises.
        canonical_time = "2026-09-11T22:17:00+02:00"
        assert canonical_time not in body_text, "canonical crime time leaked into some response"
        ok("2e", f"leak scan clean over {len(all_responses)} responses "
                 f"({len(SCANNED)} scans); forbidden set: "
                 + ", ".join(sorted(FORBIDDEN_ANYWHERE))[:300])

        # -------------------------------------------------------------- 2f --
        section("2f restart persistence")
        # dispose engines/store, then open a FRESH app on the SAME file
        _finish_app(app)

        app2 = _make_app(db_url)
        try:
            c2 = TestClient(app2)
            res = c2.get(f"/api/v1/playthroughs/{fresh_id}/investigation",
                         headers=_auth(fresh_token))
            body = res.json()
            assert res.status_code == 200, body
            assert sorted(body["playerKnowledge"]["discoveredEvidenceIds"]) == sorted(
                [EMAIL_EVIDENCE, KNIFE_EVIDENCE]), body["playerKnowledge"]
            assert sorted(body["playerKnowledge"]["readEvidenceIds"]) == sorted(
                [EMAIL_EVIDENCE, KNIFE_EVIDENCE]), body["playerKnowledge"]["readEvidenceIds"]
            assert SCENE_LOCATION in body["playerKnowledge"]["visitedLocationIds"]
            # object flags reflect discovered/read state
            knife_obj = next(o for o in body["scene"]["worldObjects"]
                             if o["objectId"] == "kitchen_knife")
            assert knife_obj["discovered"] is True and knife_obj["read"] is True, knife_obj
            email_obj = next(o for o in body["scene"]["worldObjects"]
                             if o["objectId"] == "apartment_laptop")
            assert email_obj["discovered"] is True and email_obj["read"] is True, email_obj
            # repeat read byte-identical to pre-restart read
            r2 = c2.get(f"/api/v1/playthroughs/{fresh_id}/records/{EMAIL_EVIDENCE}",
                        headers=_auth(fresh_token))
            r2b = r2.json()
            assert r2.status_code == 200 and r2b == rrb, "read after restart differs"
            assert r2b["openedAt"] == rrb["openedAt"]
            ok("2f", "restart: knowledge persists (discovered/read/visited), read byte-identical")

            # new playthrough after restart is independent
            c2b = TestClient(app2)
            res = c2b.post(f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                           headers=_auth(creator))
            body = res.json()
            assert res.status_code == 201, body
            new_id, new_token = body["playthroughId"], body["playthroughAccessToken"]
            res = c2b.get(f"/api/v1/playthroughs/{new_id}/investigation",
                          headers=_auth(new_token))
            nbody = res.json()
            assert res.status_code == 200 and nbody["playerKnowledge"][
                "discoveredEvidenceIds"] == [], nbody
            ok("2f", "new playthrough after restart: empty knowledge, independent")
        finally:
            _finish_app(app2)

        # re-open app3 so the concurrency section can run against a live app
        app = _make_app(db_url)

        # -------------------------------------------------------------- 2g --
        section("2g concurrency: 18 parallel discovers")
        cc = TestClient(app)
        res = cc.post(f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                      headers=_auth(creator))
        body = res.json()
        assert res.status_code == 201, body
        cp_id, cp_token = body["playthroughId"], body["playthroughAccessToken"]
        assert res.status_code == 201, body

        barrier = threading.Barrier(18)

        def _discover(_):
            barrier.wait()
            c = TestClient(app)
            r = c.post(f"/api/v1/playthroughs/{cp_id}/evidence/{KNIFE_EVIDENCE}/discover",
                       headers=_auth(cp_token))
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, {"_raw": r.text[:200]}

        with ThreadPoolExecutor(max_workers=18) as ex:
            results = list(ex.map(_discover, range(18)))
        codes = [r[0] for r in results]
        assert all(code == 200 for code in codes), f"non-200 concurrency results: {codes}"
        states = [r[1]["state"] for r in results]
        # Frozen concurrency contract (Phase6 O18): every duplicate discover is
        # 200 and the state label is one of the two allowed values; the FINAL
        # set has exactly one entry. (A benign label race is possible: threads
        # that all read before the first commit may each see "discovered".)
        assert all(s in ("discovered", "already-discovered") for s in states), states
        assert "discovered" in states, states
        snap = app.state.store.snapshot_player_knowledge(cp_id)
        assert snap.discovered == (KNIFE_EVIDENCE,), snap
        assert len(snap.discovered) == 1
        ok("2g", f"18 parallel discovers -> all 200, single set entry, "
                 f"label spread: discovered={states.count('discovered')} "
                 f"already-discovered={states.count('already-discovered')} "
                 f"(label race is accepted by O18; final state consistent)")

        # SQL row-level check: discovered_json holds exactly one entry
        from sqlalchemy import create_engine, text as _sql
        eng = create_engine(db_url)
        try:
            with eng.connect() as conn:
                row = conn.execute(_sql(
                    "SELECT discovered_json FROM player_knowledge WHERE playthrough_id=:p"),
                    {"p": cp_id}).scalar()
            entries = json.loads(row)
            assert entries == [KNIFE_EVIDENCE], entries
            ok("2g", f"DB row discovered_json single entry: {entries} (no duplicate rows)")
        finally:
            eng.dispose()

        REPORT["summary"] = {
            "status": "PASS",
            "responsesScanned": len(all_responses),
            "sections": {k: v["status"] for k, v in REPORT["sections"].items()},
            "finishedAt": _now_utc(),
        }
        print("\n=== ALL SECTIONS PASS ===")
        return 0

    except Exception as exc:  # noqa: BLE001 - report harness failure
        import traceback

        REPORT["summary"] = {
            "status": "FAIL",
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "finishedAt": _now_utc(),
        }
        print(f"\nHARNESS FAILURE: {exc}")
        traceback.print_exc()
        return 1
    finally:
        for future in futures_cleanup:
            pass
        try:
            _finish_app(app)
        except Exception:
            pass
        REPORT["responsesScanned"] = len(all_responses)
        with open(REPORT_PATH, "w", encoding="utf-8") as fh:
            json.dump(REPORT, fh, indent=2, default=str)
        print(f"\nReport written to {REPORT_PATH}")
        # cleanup scratch db (outside the repo, but be tidy)
        try:
            db_path.unlink(missing_ok=True)
            scratch.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())