"""Phase 24 — hermetic tests for ``tools.docker_smoke`` (canned HTTP layer).

Drives the full smoke journey against a FAKE HTTP transport implementing the
deterministic CI fake-world contract (the same shapes ``qa-p19c-fake-stack.py``
assumes over the wire). NEVER a live Docker stack, NEVER a live server: the
harness calls ``tools.docker_smoke.run_smoke`` with an injected canned HTTP
function, so the smoke logic itself is unit-tested hermetic (no network, no
daemon) exactly like the rest of the root suite.

The canned server mirrors what the Phase 24 CI compose (docker-compose.yml +
docker-compose.ci.yml, FAKE_PROVIDER_SCRIPT=compose/fake-worlds/
ci-activity-log-world.json) publishes on the PUBLIC API:
  - sessions -> anonymous token
  - generate -> PUBLISHED golden case (creator token)
  - playthrough -> tokens
  - laptop(read) discovers the ACTIVITY_LOG record (cctv_thomas_scene_01)
  - records read -> 20 rows {time,text}, canonical 22:17 exactly once
  - knife(inspect) discovers a forensic record
  - witness emily_reed interview TIME (deterministic, repeat-idempotent)
  - bridge-disabled: /bridge/* 404, WS route 404
  - accusation + reveal -> REVEALED 4/4 solved
"""

from __future__ import annotations

import json
from typing import Any, Callable

import pytest

from tools import docker_smoke


GOLDEN_TIME = "2026-09-11T22:17:00+02:00"
ACTIVITY_LOG_CANONICAL = docker_smoke.ACTIVITY_LOG_CANONICAL

# A deterministic 20-row ACTIVITY_LOG entries list (mirrors the golden
# fixture's compact {time,text} rendering contract).
GOLDEN_LOG_ENTRIES = [
    {"time": "2026-09-11T21:31:40+02:00", "text": "System resumed from sleep"},
    {"time": "2026-09-11T21:36:40+02:00", "text": "User session login recorded"},
    {"time": "2026-09-11T21:41:40+02:00", "text": "Mail client synchronized"},
    {"time": "2026-09-11T21:46:40+02:00", "text": "Browser tab opened"},
    {"time": "2026-09-11T21:51:40+02:00", "text": "Research document accessed"},
    {"time": "2026-09-11T21:56:40+02:00", "text": "File explorer opened"},
    {"time": "2026-09-11T22:01:40+02:00", "text": "Text editor application opened"},
    {"time": "2026-09-11T22:06:40+02:00", "text": "Cloud synchronization completed"},
    {"time": "2026-09-11T22:11:40+02:00", "text": "Background synchronization started"},
    {"time": "2026-09-11T22:12:40+02:00", "text": "Network activity detected"},
    {"time": ACTIVITY_LOG_CANONICAL, "text": "Local user activity detected"},
    {"time": "2026-09-11T22:22:40+02:00", "text": "Document autosaved"},
    {"time": "2026-09-11T22:27:40+02:00", "text": "User session unlocked"},
    {"time": "2026-09-11T22:32:40+02:00", "text": "Local file written"},
    {"time": "2026-09-11T22:37:40+02:00", "text": "Keyboard activity detected"},
    {"time": "2026-09-11T22:42:40+02:00", "text": "File copied to local workspace"},
    {"time": "2026-09-11T22:52:40+02:00", "text": "Document autosaved"},
    {"time": "2026-09-11T22:57:40+02:00", "text": "Browser activity detected"},
    {"time": "2026-09-11T23:01:40+02:00", "text": "Mail client synchronized"},
    {"time": "2026-09-11T23:06:40+02:00", "text": "System entered idle state"},
]

FORENSIC_ENTRIES = [
    {"text": "Kitchen knife examined", "role": "weapon"},
]

WITNESS_STATE = {
    "witnessId": "emily_reed",
    "displayName": "Emily Reed",
    "presence": "REMOTE_STATEMENT",
    "atScene": False,
    "questions": [
        {"questionType": q, "label": q}
        for q in ("OBSERVATION", "TIME", "PERSON", "OBJECT", "LOCATION", "SOUND")
    ],
}

WITNESS_STATEMENT = {
    "summary": "Neighbour Emily Reed's account of the evening.",
    "observations": [
        {"time": "22:10", "text": "I heard shouting from the apartment around 22:10."},
    ],
}


class CannedStack:
    """A bounded in-memory fake implementing the deterministic CI wire shape."""

    def __init__(self) -> None:
        self.case_id = "CASE-SMOKE-1"
        self.generation_id = "GEN-SMOKE-1"
        self.session_token = "anon-token-smoke"
        self.creator_token = "creator-token-smoke"
        self.pt_id = "PT-SMOKE-1"
        self.pt_token = "pt-token-smoke"
        self.evidence_id = docker_smoke.ACTIVITY_LOG_EVIDENCE
        self.discovered: list[str] = []
        self.witness_asked_times = 0
        self.calls: list[tuple[str, str]] = []

    def route(self, method: str, path: str, body: object | None) -> tuple[int, Any]:
        self.calls.append((method, path))
        # readiness
        if method == "GET" and path == "/readiness":
            return 200, {"status": "ready", "database": "ok", "migrations": "ok"}
        if method == "GET" and path == "/generation-capabilities":
            return 200, {"modes": [{"id": "demo", "available": True}],
                         "configuredProvider": "fake"}
        if method == "POST" and path == "/sessions/anonymous":
            return 201, {"anonymousSessionToken": self.session_token,
                         "quotaWindowEndsAt": "2026-09-12T00:00:00+02:00"}
        if method == "POST" and path == "/cases":
            return 201, {"caseId": self.case_id,
                         "generationId": self.generation_id,
                         "generationAttemptId": "ATT-1",
                         "creatorAccessToken": self.creator_token,
                         "status": "PUBLISHED",
                         "failureCode": None}
        if method == "GET" and path == f"/cases/{self.case_id}":
            return 200, self._public_case()
        if method == "GET" and path == f"/generations/{self.generation_id}":
            return 200, {"caseId": self.case_id, "generationId": self.generation_id,
                         "status": "PUBLISHED", "progress": 100,
                         "stage": None, "failureCode": None}
        if method == "POST" and path == f"/cases/{self.case_id}/versions/1/playthroughs":
            return 201, {"playthroughId": self.pt_id,
                         "caseId": self.case_id, "caseVersion": 1,
                         "playthroughAccessToken": self.pt_token, "status": "PLAYING"}
        if path == f"/playthroughs/{self.pt_id}/investigation":
            if method == "GET":
                return 200, self._bootstrap()
            return 405, {"error": {"code": "METHOD_NOT_ALLOWED", "message": "no"}}
        if method == "POST" and path == (
            f"/playthroughs/{self.pt_id}/objects/{docker_smoke.LAPTOP_OBJECT}/interact"
        ):
            self.discovered.append(self.evidence_id)
            return 200, {
                "objectId": docker_smoke.LAPTOP_OBJECT,
                "interaction": docker_smoke.LAPTOP_INTERACTION,
                "evidenceId": self.evidence_id,
                "discovery": {
                    "evidenceId": self.evidence_id,
                    "kind": "cctv_observation",
                    "title": "Kitchen CCTV shows Thomas at 22:16:40",
                    "interaction": "read",
                    "state": "discovered",
                },
                "result": "interacted",
                "inspection": {"relevant": True, "label": "Apartment Laptop"},
            }
        if method == "POST" and path == (
            f"/playthroughs/{self.pt_id}/objects/{docker_smoke.KNIFE_OBJECT}/interact"
        ):
            self.discovered.append("forensic_knife_match_01")
            return 200, {
                "objectId": docker_smoke.KNIFE_OBJECT,
                "interaction": docker_smoke.KNIFE_INTERACTION,
                "evidenceId": "forensic_knife_match_01",
                "discovery": {
                    "evidenceId": "forensic_knife_match_01",
                    "kind": "forensic",
                    "title": "Forensic knife comparison",
                    "interaction": "inspect",
                    "state": "discovered",
                },
                "result": "interacted",
                "inspection": {"relevant": True, "label": "Kitchen Knife"},
            }
        # records
        if method == "GET" and path == f"/playthroughs/{self.pt_id}/records/{self.evidence_id}":
            return 200, self._log_record()
        if method == "GET" and path == (
            f"/playthroughs/{self.pt_id}/records/forensic_knife_match_01"
        ):
            return 200, {
                "evidenceId": "forensic_knife_match_01",
                "kind": "forensic",
                "title": "Forensic knife comparison",
                "openedAt": "2026-09-11T23:00:00Z",
                "readByPlayer": True,
                "content": {"renderType": "FORENSIC", "entries": FORENSIC_ENTRIES},
            }
        # witness
        if method == "GET" and path == f"/playthroughs/{self.pt_id}/witnesses/{docker_smoke.WITNESS_ID}":
            return 200, WITNESS_STATE
        if method == "POST" and path == (
            f"/playthroughs/{self.pt_id}/witnesses/{docker_smoke.WITNESS_ID}/interview"
        ):
            self.witness_asked_times += 1
            return 200, {
                "witnessId": docker_smoke.WITNESS_ID,
                "displayName": "Emily Reed",
                "questionType": "TIME",
                "statement": WITNESS_STATEMENT,
                "discovery": (
                    {
                        "newlyDiscovered": self.witness_asked_times == 1,
                        "record": {
                            "evidenceId": "witness_statement_emily_01",
                            "kind": "witness_statement",
                            "readByPlayer": True,
                            "content": {"renderType": "BODY_OBSERVATION", "summary": "w"},
                        },
                    }
                    if self.witness_asked_times == 1
                    else {
                        "newlyDiscovered": False,
                        "record": {
                            "evidenceId": "witness_statement_emily_01",
                            "kind": "witness_statement",
                            "readByPlayer": True,
                            "content": {"renderType": "BODY_OBSERVATION", "summary": "w"},
                        },
                    }
                ),
            }
        # bridge-disabled: REST 404, WS path unmounted
        if path.startswith("/bridge/"):
            return 404, {"error": {"code": "NOT_FOUND", "message": "Not found",
                                   "details": None}}
        if method == "POST" and path == f"/playthroughs/{self.pt_id}/accusation":
            return 200, {
                "playthroughId": self.pt_id,
                "caseId": self.case_id,
                "caseVersion": 1,
                "status": "ACCUSED",
                "accusation": docker_smoke.GOLDEN_ACCUSATION,
            }
        if method == "GET" and path == f"/playthroughs/{self.pt_id}/reveal":
            return 200, {
                "playthroughId": self.pt_id,
                "caseId": self.case_id,
                "caseVersion": 1,
                "status": "REVEALED",
                "truth": {
                    "murdererId": "thomas_reed",
                    "murdererName": "Thomas Reed",
                    "motiveId": "cover_up_embezzlement",
                    "motiveLabel": "Cover up the embezzlement",
                    "weaponId": "kitchen_knife",
                    "weaponName": "Kitchen Knife",
                    "crimeTime": GOLDEN_TIME,
                },
                "player": {"accusation": docker_smoke.GOLDEN_ACCUSATION},
                "result": {
                    "murdererCorrect": True,
                    "motiveCorrect": True,
                    "weaponCorrect": True,
                    "timeCorrect": True,
                    "overall": "solved",
                },
                "score": {"correctDimensions": 4, "totalDimensions": 4},
                "timeline": [],
                "explanation": {"evidence": [], "dimensions": {}},
            }
        return 404, {"error": {"code": "NOT_FOUND", "message": "Not found",
                               "details": None}}

    def _public_case(self) -> dict[str, Any]:
        return {
            "caseId": self.case_id,
            "caseVersion": 1,
            "title": "Smoke",
            "scene": {"locationId": "miller_apartment_kitchen",
                      "name": "Miller Apartment - Kitchen"},
            "persons": [
                {"personId": "thomas_reed", "name": "Thomas Reed", "role": "suspect",
                 "affordances": ["SUSPECT_ELIGIBLE"]},
                {"personId": "sarah_miller", "name": "Sarah Miller", "role": "victim",
                 "affordances": []},
            ],
            "motives": [
                {"motiveId": "cover_up_embezzlement", "label": "Cover up the embezzlement",
                 "affordances": ["MOTIVE_CANDIDATE"]},
            ],
            "objects": [
                {"objectId": "kitchen_knife", "assetId": "PROP_KITCHEN_KNIFE_01",
                 "affordances": ["POTENTIAL_WEAPON"]},
            ],
            "locations": [{"locationId": "miller_apartment_kitchen", "name": "Kitchen"}],
            "travelRules": [],
            "evidence": [
                {"id": self.evidence_id, "kind": "cctv_observation", "reliability": "high",
                 "title": "Kitchen CCTV", "description": "Kitchen CCTV shows Thomas"},
                {"id": "forensic_knife_match_01", "kind": "forensic", "reliability": "high",
                 "title": "Forensic knife comparison", "description": "Knife match"},
            ],
            "worldGraph": {"locations": [], "placements": []},
        }

    def _bootstrap(self) -> dict[str, Any]:
        return {
            "playthroughId": self.pt_id,
            "caseId": self.case_id,
            "caseVersion": 1,
            "state": "PLAYING",
            "playerKnowledge": {
                "discoveredEvidenceIds": list(self.discovered),
                "readEvidenceIds": list(self.discovered),
                "visitedLocationIds": [],
            },
            "scene": {
                "locationId": "miller_apartment_kitchen",
                "name": "Miller Apartment - Kitchen",
                "environmentId": "apartment",
                "environmentVersion": 1,
                "worldObjects": [
                    {"objectId": docker_smoke.LAPTOP_OBJECT,
                     "assetId": "PROP_LAPTOP_01", "assetType": "electronics",
                     "locationId": "miller_apartment_kitchen", "anchor": "desk_main",
                     "interaction": docker_smoke.LAPTOP_INTERACTION,
                     "evidenceId": self.evidence_id,
                     "discovered": self.evidence_id in self.discovered,
                     "read": self.evidence_id in self.discovered},
                    {"objectId": docker_smoke.KNIFE_OBJECT,
                     "assetId": "PROP_KITCHEN_KNIFE_01", "assetType": "sharp_weapon",
                     "locationId": "miller_apartment_kitchen", "anchor": "kitchen_counter",
                     "interaction": docker_smoke.KNIFE_INTERACTION,
                     "evidenceId": "forensic_knife_match_01",
                     "discovered": "forensic_knife_match_01" in self.discovered,
                     "read": "forensic_knife_match_01" in self.discovered},
                ],
            },
            "candidates": {
                "suspects": [{"id": "thomas_reed", "name": "Thomas Reed"}],
                "motives": [{"id": "cover_up_embezzlement", "label": "Cover up the embezzlement"}],
                "weapons": [{"id": "kitchen_knife", "assetId": "PROP_KITCHEN_KNIFE_01",
                             "name": "Kitchen Knife"}],
            },
            "witnesses": [
                {"witnessId": "emily_reed", "displayName": "Emily Reed",
                 "presence": "REMOTE_STATEMENT", "sceneObjectId": None},
            ],
        }

    def _log_record(self) -> dict[str, Any]:
        return {
            "evidenceId": self.evidence_id,
            "kind": "cctv_observation",
            "title": "Kitchen CCTV shows Thomas at 22:16:40",
            "description": "Kitchen CCTV activity log around the observed time.",
            "openedAt": "2026-09-11T23:00:00Z",
            "readByPlayer": True,
            "content": {
                "renderType": "ACTIVITY_LOG",
                "entries": GOLDEN_LOG_ENTRIES,
            },
        }


def _http(stack: CannedStack) -> Callable[..., Any]:
    def _fn(base_url: str, method: str, path: str, token: str | None = None,
            body: object | None = None) -> tuple[int, Any]:
        return stack.route(method, path, body)
    _fn.stack = stack  # type: ignore[attr-defined]
    return _fn


def _run(stack: CannedStack) -> list[Any]:
    report: list[Any] = []
    docker_smoke.run_smoke(_http(stack), "http://127.0.0.1:8000", report=report)
    return report


def test_full_smoke_journey_passes() -> None:
    stack = CannedStack()
    report = _run(stack)
    failed = [r for r in report if not r.ok]
    assert not failed, [r.detail for r in failed]
    assert all(r.ok for r in report)
    # Bridge disabled: the fake stack answers 404 exactly like the real profile.
    hits = [r.check for r in report if "bridge" in r.check]
    assert hits and all(r.ok for r in report if r.check in hits)


def test_activity_log_contract_asserted() -> None:
    stack = CannedStack()
    _run(stack)
    log_rows = [r for r in _run(stack) if r.check.startswith("log ")]
    checks = {r.check: r for r in log_rows}
    assert checks["log 15-20 rows"].ok
    assert checks["log compact time rendering {time,text}"].ok
    assert checks["log canonical time exactly once"].ok
    assert checks["log reload-identical persisted content"].ok
    assert checks["log DTO pre-reveal clean"].ok


def test_witness_idempotency_and_notebook() -> None:
    stack = CannedStack()
    report = _run(stack)
    records = {r.check: r for r in report}
    assert records["witness TIME deterministic statement"].ok
    assert records["witness idempotent statement (repeat identical)"].ok
    assert records["witness repeat no new discovery"].ok
    assert records["notebook update persisted (discovered ids grown)"].ok
    assert records["reload-identical notebook bootstrap"].ok


def test_leak_state_never_exposed_pre_reveal() -> None:
    stack = CannedStack()
    report = _run(stack)
    for r in report:
        if "pre-reveal clean" in r.check:
            assert r.ok, r.detail


def test_service_output_never_leaks_case_secrets() -> None:
    """The HTML report text must never carry tokens/truth/provider output."""
    stack = CannedStack()
    report = _run(stack)
    import io
    import contextlib

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        docker_smoke._emit(report, argparse_type())
    text = captured.getvalue()
    assert "creator-token-smoke" not in text
    assert "pt-token-smoke" not in text
    assert "anon-token-smoke" not in text
    assert "murdererName" not in text


def argparse_type() -> object:
    import argparse

    return argparse.Namespace(plain=True, report=None, base_url="http://127.0.0.1:8000")


def test_report_never_embeds_the_targeted_base_url() -> None:
    """DEF-004: the sanitized report (plain AND JSON) must not embed baseUrl.

    A recovered CI artifact must never reveal the targeted stack address. The
    structural fields (schema / passed / total / check rows) stay intact.
    """
    import argparse
    import contextlib
    import io

    stack = CannedStack()
    report = _run(stack)

    # Plain mode.
    plain = io.StringIO()
    with contextlib.redirect_stdout(plain):
        docker_smoke._emit(report, argparse.Namespace(
            plain=True, report=None, base_url="http://127.0.0.1:8000"
        ))
    text = plain.getvalue()
    assert docker_smoke.DEFAULT_BASE_URL not in text
    assert "baseUrl" not in text

    # JSON mode (the artifact format).
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        docker_smoke._emit(report, argparse.Namespace(
            plain=False, report=None, base_url="http://127.0.0.1:8000"
        ))
    doc = json.loads(out.getvalue())
    assert "baseUrl" not in doc
    # Every other sanitized field stays.
    assert doc["schema"] == "docker-smoke-v1"
    assert doc["passed"] == doc["total"]
    check_names = {row["check"] for row in doc["checks"]}
    assert "reveal REVEALED" in check_names  # real journey checks stay intact
    assert all({"check", "ok", "detail"} == set(row.keys()) for row in doc["checks"])


def test_wrong_golden_canonical_fails() -> None:
    """The canonical-time-once assertion is real (fail when it changes)."""
    stack = CannedStack()
    original = docker_smoke.ACTIVITY_LOG_CANONICAL
    docker_smoke.ACTIVITY_LOG_CANONICAL = "2026-09-11T23:59:00+02:00"
    try:
        from tools import docker_smoke as ds

        ds.ACTIVITY_LOG_CANONICAL = "2026-09-11T23:59:00+02:00"
        report: list[Any] = []
        with pytest.raises(ds.SmokeFailure):
            ds.run_smoke(_http(stack), "http://127.0.0.1:8000", report=report)
    finally:
        docker_smoke.ACTIVITY_LOG_CANONICAL = original