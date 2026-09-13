"""Phase 7 test helpers (accusation/reveal suite).

Adds the Phase 6 API babysitters the shared phase5/phase6 helpers do not
provide: accusation/reveal call wrappers, a truth bundle reader over the
persisted published payload, the deterministic v2 publication used by the
cross-version tests, restart rebuilds over the SAME sqlite file, and the
Phase 7 leak scanners (nested key-path walkers).

The leak scanners are Phase 7-specific on purpose: the Phase 5 scanner
(banned murdererId/weaponId/crimeTime anywhere) does NOT apply to Phase 7
responses that legitimately echo the player's accusation or the reveal truth.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from phase5_helpers import auth, create_case, create_playthrough, create_session
from phase6_helpers import case_for, client, playthrough

# --------------------------------------------------------------------------- #
# Golden dev-mode case facts (verified against the persisted payload at
# runtime by ``truth_bundle``; litter here only for human readability).
# --------------------------------------------------------------------------- #

CANONICAL_CRIME_TIME = "2026-09-11T22:17:00+02:00"
GOLDEN_MURDERER = "thomas_reed"
GOLDEN_MOTIVE = "cover_up_embezzlement"
GOLDEN_WEAPON = "kitchen_knife"

# Deterministic v2-only universe candidates (Phase7 N10/N23).
V2_MURDERER_ID = "alex_carter"
V2_MURDERER_NAME = "Alex Carter"
V2_MOTIVE_ID = "v2_only_motive"
V2_MOTIVE_LABEL = "V2-Only Motive"
V2_WEAPON_ID = "v2_cand_weapon"
V2_WEAPON_ASSET = "PROP_CANDLE_STICK_02"


# --------------------------------------------------------------------------- #
# API babysitters
# --------------------------------------------------------------------------- #


def session_token(test_client: TestClient) -> str:
    """POST /api/v1/sessions/anonymous -> the anonymousSessionToken."""
    token, _ = create_session(test_client)
    return token


def create_published_case_and_playthrough(app) -> dict[str, Any]:
    """POST /api/v1/cases with the dev-mode provider (any prompt -> PUBLISHED
    golden case) then create ONE v1 playthrough. Returns the full bundle:
    caseId / creator / playthroughId / playthroughToken / truth.
    """
    case_id, creator = case_for(app)
    pt_id, pt_token = playthrough(app, case_id, creator, version=1)
    return {
        "caseId": case_id,
        "creator": creator,
        "playthroughId": pt_id,
        "playthroughToken": pt_token,
        "truth": truth_bundle(app, case_id, 1),
        "app": app,
    }


def new_playthrough(app, case_id: str, creator: str, version: int = 1):
    """An ADDITIONAL playthrough pinned to ``version`` (same case)."""
    return playthrough(app, case_id, creator, version=version)


def truth_bundle(app, case_id: str, version: int = 1) -> dict[str, Any]:
    """Truth + candidate universes of the PINNED published payload (read from
    the DB every time — never hardcoded)."""
    row = app.state.store.get_published(case_id, version)
    assert row is not None, f"published v{version} missing for {case_id}"
    payload = json.loads(row.payload_json)
    crime = payload["truth"]["crime"]
    crime_time = crime["crime_time"]
    universes = payload.get("universes") or {}
    return {
        "payload": payload,
        "murdererId": str(crime["murderer_id"]),
        "motiveId": str(crime["motive_id"]),
        "weaponId": str(crime["weapon_id"]),
        "canonical": str(crime_time["canonical"]),
        "tolerance": int(crime_time["accusation_tolerance_seconds"]),
        "suspect_ids": sorted(str(i) for i in (universes.get("suspect_ids") or ())),
        "motive_ids": sorted(str(i) for i in (universes.get("motive_ids") or ())),
        "weapon_ids": sorted(str(i) for i in (universes.get("weapon_ids") or ())),
    }


def make_accusation(
    test_client,
    pt_id: str,
    pt_token: str,
    body: dict[str, str],
):
    """POST .../accusation with the frozen body dict; returns the raw response."""
    return test_client.post(
        f"/api/v1/playthroughs/{pt_id}/accusation",
        json=body,
        headers=auth(pt_token),
    )


def get_reveal(test_client, pt_id: str, pt_token: str):
    """GET .../reveal; returns the raw ``httpx.Response``."""
    return test_client.get(
        f"/api/v1/playthroughs/{pt_id}/reveal", headers=auth(pt_token)
    )


def winning_body(truth: dict[str, Any], crime_time: str | None = None) -> dict[str, str]:
    """The all-correct accusation body for the golden truth."""
    return {
        "murdererId": truth["murdererId"],
        "motiveId": truth["motiveId"],
        "weaponId": truth["weaponId"],
        "crimeTime": crime_time if crime_time is not None else truth["canonical"],
    }


def suspect_out_of(truth: dict[str, Any]) -> str:
    """A universe-member suspect that is NOT the murderer (wrong-WHO helper)."""
    others = [i for i in truth["suspect_ids"] if i != truth["murdererId"]]
    assert others, "golden truth needs at least two suspects for a wrong-WHO case"
    return others[0]


def motive_out_of(truth: dict[str, Any]) -> str:
    others = [i for i in truth["motive_ids"] if i != truth["motiveId"]]
    assert others, "golden truth needs at least two motives for a wrong-WHY case"
    return others[0]


def weapon_out_of(truth: dict[str, Any]) -> str:
    others = [i for i in truth["weapon_ids"] if i != truth["weaponId"]]
    assert others, "golden truth needs at least two weapons for a wrong-WEAPON case"
    return others[0]


def accuse_then_reveal(app, pt_id: str, pt_token: str, body: dict[str, str]) -> dict[str, Any]:
    """Accuse then reveal (both through the API); returns the reveal body."""
    with client(app) as c:
        res = make_accusation(c, pt_id, pt_token, body)
        assert res.status_code == 200, res.json()
        reveal = get_reveal(c, pt_id, pt_token)
        assert reveal.status_code == 200, reveal.json()
    return reveal.json()


# --------------------------------------------------------------------------- #
# deterministic v2 publication (no provider call)
# --------------------------------------------------------------------------- #


def publish_v2(app, case_id: str) -> dict[str, Any]:
    """Insert a deterministic v2 published row whose universe/truth DIFFER from
    v1: three v2-only candidates are added and the v2 truth's murderer changes.
    Used by N10 (cross-version candidate) and N23 (v1 releases v1 truth after
    v2 publication). Returns the crafted v2 payload."""
    store = app.state.store
    now = float(app.state.clock.now())
    v1 = store.get_published(case_id, 1)
    assert v1 is not None
    payload = json.loads(v1.payload_json)
    payload["caseVersion"] = 2
    payload["publishedAt"] = now
    payload["draft"]["persons"].append(
        {"person_id": V2_MURDERER_ID, "name": V2_MURDERER_NAME}
    )
    payload["draft"]["motives"].append(
        {"motive_id": V2_MOTIVE_ID, "label": V2_MOTIVE_LABEL}
    )
    payload["draft"]["objects"].append(
        {
            "object_id": V2_WEAPON_ID,
            "asset_id": V2_WEAPON_ASSET,
            "affordances": [],
            "subtype": "decor",
        }
    )
    universes = payload["universes"]
    universes.setdefault("suspect_ids", []).append(V2_MURDERER_ID)
    universes.setdefault("motive_ids", []).append(V2_MOTIVE_ID)
    universes.setdefault("weapon_ids", []).append(V2_WEAPON_ID)
    payload["truth"]["crime"]["murderer_id"] = V2_MURDERER_ID
    store.create_case_version(
        case_id=case_id, version=2, state="PUBLISHED", generation_id="GEN-2", created_at=now
    )
    store.insert_published(
        case_id=case_id,
        case_version=2,
        payload_json=json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ),
        published_at=now,
    )
    return payload


# --------------------------------------------------------------------------- #
# restart over the SAME sqlite file
# --------------------------------------------------------------------------- #


def reopen_app(database_url: str):
    """A fresh application over ``database_url`` (same file, nothing in memory)."""
    from app.core.config import Settings
    from app.main import create_app

    return create_app(
        Settings(
            database_url=database_url,
            cors_allowed_origins=["http://localhost:5173"],
            max_generations_per_session_per_window=8,
            max_concurrent_generations=2,
        )
    )


# --------------------------------------------------------------------------- #
# Phase 7 leak scanners (nested key-path walkers, Phase7 N24/N25/N26)
# --------------------------------------------------------------------------- #

# Keys that — pre-reveal — would designate the canonical solution, leak truth
# sections, scoring internals or provider/token material.
_PRE_REVEAL_FORBIDDEN_KEYS = frozenset(
    {
        # canonical truth / designation
        "truth", "truthfulness", "canonical", "canonicalCrimeTime", "crime",
        "crimeTime", "acceptedScoring", "acceptedScoringTimeSet",
        "murdererCorrect", "motiveCorrect", "weaponCorrect", "timeCorrect",
        "correctDimensions", "totalDimensions", "overall", "isCorrect",
        "winner", "winning", "winners", "isWinner", "designated",
        "designation", "rank", "selected", "murderer", "victim",
        # raw payload / solver / provider internals
        "universe", "universes", "solution", "solutionProof", "solverProof",
        "proof", "prompt", "providerOutput", "diagnostics", "seed", "model",
        "verifier", "tokenVerifier", "attemptId", "generationAttemptId",
        "propositions", "sourceRef", "schemaVersion", "draft", "publishedAt",
        "payload", "quotaSessionId", "anonymousQuotaSessionId",
    }
)

# The one-from-body echo of the frozen accusation contract (allowed in the
# accusation 200 body ONLY, and only as a player-submission echo).
_ECHO_KEYS = frozenset({"murdererId", "motiveId", "weaponId", "crimeTime"})

# Reveal-internal keys: the reveal DTO may carry truth, player + result, but
# NEVER solver/validation/provider/token/internal material.
_REVEAL_FORBIDDEN_KEYS = frozenset(
    {
        "solverProof", "solutionProof", "proof", "acceptedScoring",
        "acceptedScoringTimeSet", "prompt", "providerOutput", "diagnostics",
        "seed", "model", "verifier", "tokenVerifier", "attemptId",
        "generationAttemptId", "propositions", "sourceRef", "universe",
        "universes", "canonical", "canonicalCrimeTime", "truthfulness",
        "winner", "winning", "winners", "isWinner", "designated",
        "designation", "rank", "selected", "schemaVersion", "draft",
        "publishedAt", "payload", "quotaSessionId", "anonymousQuotaSessionId",
        "token", "sessionId",
    }
)


def iter_key_paths(node: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    """Yield (dotted-key-path, value) for EVERY node — any nesting depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield (path, value)
            yield from iter_key_paths(value, path)
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            path = f"{prefix}.{index}" if prefix else str(index)
            yield (path, value)
            yield from iter_key_paths(value, path)


def assert_no_pre_reveal_material(
    payload: Any,
    *,
    echo: bool = False,
    known_tokens: set[str] = frozenset(),
    canonical_time: str | None = None,
) -> list[str]:
    """Recursive scan of a PRE-REVEAL response body (investigation / discover /
    interact / read / accusation): no canonical designation, no truth/scoring/
    proof/provider section and no token value at ANY nesting level.

    ``echo=True`` (accusation 200 body) additionally allows the frozen player
    submission echo keys (murdererId/motiveId/weaponId/crimeTime).
    """
    forbidden = _PRE_REVEAL_FORBIDDEN_KEYS
    if echo:
        forbidden = forbidden - _ECHO_KEYS
    violations: list[str] = []
    for path, value in iter_key_paths(payload):
        leaf_key = path.rsplit(".", 1)[-1]
        if leaf_key in forbidden:
            violations.append(f"forbidden key {path!r}")
        if isinstance(value, str):
            if known_tokens and value in known_tokens:
                violations.append(f"known token value leaked at {path!r}")
            if canonical_time and value == canonical_time:
                violations.append(f"canonical time leaked at {path!r}")
    assert not violations, (
        "pre-reveal canonical designation / internal material found: "
        + "; ".join(violations)
    )
    return violations


def assert_no_reveal_internal_material(
    payload: Any, *, known_tokens: set[str] = frozenset()
) -> list[str]:
    """Recursive scan of the REVEAL DTO: no solver proof / accepted scoring /
    provider / prompt / token / verifier / internal id material (N25/N26)."""
    violations: list[str] = []
    for path, value in iter_key_paths(payload):
        leaf_key = path.rsplit(".", 1)[-1]
        if leaf_key in _REVEAL_FORBIDDEN_KEYS:
            violations.append(f"forbidden key {path!r}")
        if isinstance(value, str):
            if known_tokens and value in known_tokens:
                violations.append(f"known token value leaked at {path!r}")
    assert not violations, (
        "reveal DTO leaked internal material: " + "; ".join(violations)
    )
    return violations


# --------------------------------------------------------------------------- #
# candidates block (Phase7 J/K) — alphabetical + never winner-marked
# --------------------------------------------------------------------------- #

_SUSPECT_KEYS = frozenset({"id", "name"})
_MOTIVE_KEYS = frozenset({"id", "label"})
_WEAPON_KEYS = frozenset({"id", "assetId", "name"})


def assert_candidates_unmarked(candidates: dict[str, Any], truth: dict[str, Any]) -> None:
    """Phase7 J/K contract: every candidates list is sorted alphabetically by
    id, every entry carries EXACTLY the frozen allowlisted keys (an extra key
    would be a winner-designation marker -> fail), and the winning ids appear
    UNmarked."""
    suspects = candidates["suspects"]
    motives = candidates["motives"]
    weapons = candidates["weapons"]
    assert [s["id"] for s in suspects] == sorted(s["id"] for s in suspects)
    assert [m["id"] for m in motives] == sorted(m["id"] for m in motives)
    assert [w["id"] for w in weapons] == sorted(w["id"] for w in weapons)
    assert all(set(s) == _SUSPECT_KEYS for s in suspects), (
        "a suspect entry carries an extra (designation) key"
    )
    assert all(set(m) == _MOTIVE_KEYS for m in motives), (
        "a motive entry carries an extra (designation) key"
    )
    assert all(set(w) == _WEAPON_KEYS for w in weapons), (
        "a weapon entry carries an extra (designation) key"
    )
    suspect_ids = {s["id"] for s in suspects}
    motive_ids = {m["id"] for m in motives}
    weapon_ids = {w["id"] for w in weapons}
    assert truth["murdererId"] in suspect_ids, "winning suspect missing"
    assert truth["motiveId"] in motive_ids, "winning motive missing"
    assert truth["weaponId"] in weapon_ids, "winning weapon missing"
    for s in suspects:
        assert s["id"] and isinstance(s["name"], str) and s["name"]
    for m in motives:
        assert m["id"] and isinstance(m["label"], str) and m["label"]
    for w in weapons:
        assert w["id"] and isinstance(w["assetId"], str) and w["assetId"]
        assert isinstance(w["name"], str) and w["name"]


__all__ = [
    "CANONICAL_CRIME_TIME",
    "GOLDEN_MOTIVE",
    "GOLDEN_MURDERER",
    "GOLDEN_WEAPON",
    "V2_MOTIVE_ID",
    "V2_MOTIVE_LABEL",
    "V2_MURDERER_ID",
    "V2_MURDERER_NAME",
    "V2_WEAPON_ASSET",
    "V2_WEAPON_ID",
    "accuse_then_reveal",
    "assert_candidates_unmarked",
    "assert_no_pre_reveal_material",
    "assert_no_reveal_internal_material",
    "create_published_case_and_playthrough",
    "get_reveal",
    "iter_key_paths",
    "make_accusation",
    "motive_out_of",
    "new_playthrough",
    "publish_v2",
    "reopen_app",
    "session_token",
    "suspect_out_of",
    "truth_bundle",
    "weapon_out_of",
    "winning_body",
]