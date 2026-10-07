"""Phase 32 backend regression — SavegameV1 reveal-gated export (REPLAYABLE
SAVES, backend track).

Covers (Phase32-SALC-R §42 FORMAT/EXPORT + §20/§23/§34, ADR-003):

- FORMAT: the frozen constants (format / formatVersion / extension / MIME /
  size bound); the committed canonical demo fixture is valid v1 and
  round-trips the deterministic serializer byte-for-byte; the live demo
  projection is content-consistent with the canonical fixture;
- EXPORT: 403 REVEAL_NOT_AVAILABLE before the truth is reveal-eligible
  (fresh PLAYING playthrough), 200 once ACCUSED (the reveal gate is
  {ACCUSED, REVEALED}) and after REVEALED; the body is the canonical MIME
  type with a safe attachment filename; explicit allowlist (closed key sets +
  a deep scanner proving no internal/truth/provider key anywhere);
  ReplayTruthV1 carries the four ids + canonical time + tolerance + public
  labels and NO internal CaseTruth object;
- SECRET SENTINELS (§34): sentinel values placed into NON-allowlisted payload
  fields never reach the exported bytes; the projection never serializes the
  whole payload;
- ISOLATION: exporting does not mutate the payload/DB; concurrent service
  reads are clean;
- DETERMINISM: two projections of the same payload are byte-identical
  (sorted keys);
- SIZE BOUND (§20): the canonical demo export is far below the documented
  ``MAX_EXPORT_BYTES`` (5 MiB) and the serializer enforces the cap.
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
from app.services import savegame as sg  # noqa: E402
from conftest import upgrade_db  # noqa: E402
from phase5_helpers import auth, create_session  # noqa: E402

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_CANONICAL_FIXTURE = (
    _BACKEND_DIR
    / "tests"
    / "fixtures"
    / "savegame"
    / "v1_demo_apartment.pdcase.json"
)

# Pinned demo-apartment facts (Phase 28 / test_phase7_helpers golden).
_CANONICAL_CRIME_TIME = "2026-09-11T22:17:00+02:00"
_APT_PROMPT = (
    "Victim: Sarah Miller\n"
    "Murderer: Thomas Reed\n"
    "Motive: \u20ac240,000 embezzlement\n"
    "Weapon: Kitchen knife\n"
    "Time: 22:17\n"
    "Witness: Emily Reed\n"
)
_PINNED_EXPORTED_AT = "2026-10-06T20:00:00Z"

# Shared test app (migrated, deterministic fake provider).


def _make_app(database_url):
    upgrade_db(database_url)
    return create_app(
        Settings(
            database_url=database_url,
            cors_allowed_origins=["http://localhost:5173"],
            max_concurrent_generations=4,
            max_generations_per_session_per_window=8,
        )
    )


def _dispose(application) -> None:
    application.state.engine.dispose()
    if application.state.store is not None:
        try:
            application.state.store.dispose()
        except Exception:  # noqa: BLE001 - teardown must never mask
            pass


def _publish_demo_apartment(database_url, difficulty: str | None = "medium"):
    """API-drive a PUBLISHED demo-apartment case; returns (app, caseId,
    creator token, payload)."""
    application = _make_app(database_url)
    with TestClient(application) as c:
        session_token, _ = create_session(c)
        body = {"prompt": _APT_PROMPT, "demoCaseId": "demo-apartment"}
        if difficulty is not None:
            body["difficulty"] = difficulty
        res = c.post(
            "/api/v1/cases",
            json=body,
            headers=auth(session_token),
        )
        assert res.status_code == 201, res.text
        created = res.json()
        assert created["status"] == "PUBLISHED", created
        payload = json.loads(
            application.state.store.get_published(created["caseId"], 1).payload_json
        )
        return application, created["caseId"], created["creatorAccessToken"], payload


def _create_playthrough(client, case_id, creator):
    res = client.post(
        f"/api/v1/cases/{case_id}/versions/1/playthroughs",
        headers=auth(creator),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    return body["playthroughId"], body["playthroughAccessToken"]


def _winning_body(payload) -> dict[str, str]:
    crime = payload["truth"]["crime"]
    return {
        "murdererId": crime["murderer_id"],
        "motiveId": crime["motive_id"],
        "weaponId": crime["weapon_id"],
        "crimeTime": crime["crime_time"]["canonical"],
    }


def _iter_key_paths(node, prefix: str = ""):
    """Yield the dotted key path of every dictionary key at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path
            yield from _iter_key_paths(value, path)
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            yield from _iter_key_paths(value, f"{prefix}.{index}")


# --------------------------------------------------------------------------- #
# FORMAT (§42) — constants + canonical fixture
# --------------------------------------------------------------------------- #


def test_savegame_format_constants():
    assert sg.SAVEGAME_FORMAT == "procedural-detective-case"
    assert sg.SAVEGAME_FORMAT_VERSION == 1
    assert sg.SAVEGAME_EXTENSION == ".pdcase"
    assert sg.SAVEGAME_MIME_TYPE == "application/vnd.procedural-detective.case+json"
    assert sg.SAVEGAME_SOURCES == frozenset({"generated", "demo"})
    assert sg.MAX_EXPORT_BYTES == 5 * 1024 * 1024


def test_canonical_fixture_exists_and_is_valid_v1():
    assert _CANONICAL_FIXTURE.is_file()
    doc = json.loads(_CANONICAL_FIXTURE.read_text(encoding="utf-8"))
    assert doc["format"] == sg.SAVEGAME_FORMAT
    assert doc["formatVersion"] == sg.SAVEGAME_FORMAT_VERSION
    assert isinstance(doc["exportedAt"], str) and doc["exportedAt"]
    case = doc["case"]
    assert set(case.keys()) == {
        "metadata",
        "publicCase",
        "scene",
        "candidates",
        "witnesses",
        "evidence",
        "replayTruth",
    }
    assert set(doc.keys()) == {"format", "formatVersion", "exportedAt", "case"}
    meta = case["metadata"]
    assert meta["title"] == "Victim: Sarah Miller"
    assert meta["difficulty"] == "medium"
    assert meta["source"] == "demo"
    assert meta["sourceCaseId"].startswith("CASE-")
    assert meta["environmentId"] == "apartment"
    truth = case["replayTruth"]
    assert truth["crimeTime"] == _CANONICAL_CRIME_TIME
    assert truth["accusationToleranceSeconds"] == 300
    assert truth["murdererId"] == "thomas_reed"


def test_canonical_fixture_round_trips_the_deterministic_serializer():
    """parse(fixture) -> serialize() == fixture bytes (the committed document
    was produced by the SAME sorted-key serializer plus a trailing newline)."""
    fixture_text = _CANONICAL_FIXTURE.read_text(encoding="utf-8")
    doc = json.loads(fixture_text)
    assert sg.serialize_savegame_v1(doc) + "\n" == fixture_text


def test_live_demo_projection_matches_canonical_fixture_content(database_url):
    """A fresh demo-apartment export is content-consistent with the committed
    canonical fixture (same deterministic case content; only the run-specific
    case id / export timestamp differ — the export is never byte-locked to the
    server's generated ids)."""
    application, _case_id, _creator, payload = _publish_demo_apartment(database_url)
    try:
        live = sg.project_savegame_v1(
            payload,
            difficulty="medium",
            exported_at=_PINNED_EXPORTED_AT,
        )
        fixture = json.loads(_CANONICAL_FIXTURE.read_text(encoding="utf-8"))
        assert live["case"]["replayTruth"] == fixture["case"]["replayTruth"]
        assert live["case"]["metadata"]["source"] == fixture["case"]["metadata"]["source"]
        assert (
            live["case"]["metadata"]["environmentId"]
            == fixture["case"]["metadata"]["environmentId"]
        )
        assert (
            [e["evidenceId"] for e in live["case"]["evidence"]]
            == [e["evidenceId"] for e in fixture["case"]["evidence"]]
        )
        assert live["case"]["candidates"] == fixture["case"]["candidates"]
        assert (
            [w["witnessId"] for w in live["case"]["witnesses"]]
            == [w["witnessId"] for w in fixture["case"]["witnesses"]]
        )
        assert live["case"]["scene"]["location"] == fixture["case"]["scene"]["location"]
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# EXPORT — reveal gate (§5/§42)
# --------------------------------------------------------------------------- #


def test_export_unavailable_before_reveal(database_url):
    """A fresh PLAYING playthrough (truth not yet reveal-eligible) MUST answer
    403 REVEAL_NOT_AVAILABLE — the replay truth is never exportable pre-reveal."""
    application, case_id, creator, _payload = _publish_demo_apartment(database_url)
    try:
        with TestClient(application) as c:
            pt_id, pt_token = _create_playthrough(c, case_id, creator)
            assert application.state.store.get_playthrough_state(pt_id) == "PLAYING"
            res = c.get(
                f"/api/v1/playthroughs/{pt_id}/savegame",
                headers=auth(pt_token),
            )
            assert res.status_code == 403, res.text
            assert res.json()["error"]["code"] == "REVEAL_NOT_AVAILABLE"
    finally:
        _dispose(application)


def test_export_available_once_accused(database_url):
    """The SAME gate as reveal: {ACCUSED, REVEALED}. An ACCUSED playthrough
    (truth not yet fetched through /reveal) may already export."""
    application, case_id, creator, payload = _publish_demo_apartment(database_url)
    try:
        with TestClient(application) as c:
            pt_id, pt_token = _create_playthrough(c, case_id, creator)
            res = c.post(
                f"/api/v1/playthroughs/{pt_id}/accusation",
                json=_winning_body(payload),
                headers=auth(pt_token),
            )
            assert res.status_code == 200, res.text
            assert application.state.store.get_playthrough_state(pt_id) == "ACCUSED"
            export = c.get(
                f"/api/v1/playthroughs/{pt_id}/savegame",
                headers=auth(pt_token),
            )
            assert export.status_code == 200, export.text
            doc = json.loads(export.text)
            assert doc["case"]["replayTruth"]["murdererId"] == "thomas_reed"
    finally:
        _dispose(application)


def test_export_available_after_reveal_and_wire_headers(database_url):
    """After the legitimate reveal flow: 200, canonical MIME type, safe
    attachment filename and a valid SavegameV1 body."""
    application, case_id, creator, payload = _publish_demo_apartment(database_url)
    try:
        with TestClient(application) as c:
            pt_id, pt_token = _create_playthrough(c, case_id, creator)
            acc = c.post(
                f"/api/v1/playthroughs/{pt_id}/accusation",
                json=_winning_body(payload),
                headers=auth(pt_token),
            )
            assert acc.status_code == 200, acc.text
            reveal = c.get(
                f"/api/v1/playthroughs/{pt_id}/reveal", headers=auth(pt_token)
            )
            assert reveal.status_code == 200, reveal.text
            assert application.state.store.get_playthrough_state(pt_id) == "REVEALED"
            export = c.get(
                f"/api/v1/playthroughs/{pt_id}/savegame",
                headers=auth(pt_token),
            )
            assert export.status_code == 200, export.text
            assert export.headers["content-type"].startswith(sg.SAVEGAME_MIME_TYPE)
            assert (
                export.headers["content-disposition"]
                == f'attachment; filename="procedural-detective-case-{case_id}.pdcase"'
            )
            doc = json.loads(export.text)
            assert doc["format"] == sg.SAVEGAME_FORMAT
            assert doc["formatVersion"] == 1
            assert doc["case"]["metadata"]["sourceCaseId"] == case_id
    finally:
        _dispose(application)


def test_export_wrong_credential_answers_404(database_url):
    """The shared playthrough-token binding applies: token A on playthrough B
    -> generic 404 (no existence leak)."""
    application, case_id, creator, _payload = _publish_demo_apartment(database_url)
    try:
        with TestClient(application) as c:
            pt_a, token_a = _create_playthrough(c, case_id, creator)
            pt_b, _token_b = _create_playthrough(c, case_id, creator)
            res = c.get(
                f"/api/v1/playthroughs/{pt_a}/savegame", headers=auth(token_a)
            )
            # A different playthrough's token, same case.
            res2 = c.get(
                f"/api/v1/playthroughs/{pt_b}/savegame", headers=auth(token_a)
            )
            assert res.status_code == 403
            assert res2.status_code == 404, res2.text
            assert res2.json()["error"]["code"] == "NOT_FOUND"
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# EXPORT — strict allowlist / no internal truth / no CaseTruth
# --------------------------------------------------------------------------- #

# Keys that must NEVER appear (as a dictionary key) anywhere in a savegame:
# internal payload sections, truth/proof material, tokens, config.
_SAVEGAME_FORBIDDEN_KEYS = frozenset(
    {
        # the frozen published-payload internal sections (never exported)
        "schemaVersion",
        "generationAttemptId",
        "attemptId",
        "publishedAt",
        "seed",
        "model",
        "prompt",
        "locked",
        "truth",
        "crime",
        "canonical",
        "tolerance",
        "universes",
        "universe",
        "solverProof",
        "solutionProof",
        "proof",
        "report",
        "validation",
        "winners",
        "winner",
        "isWinner",
        "designated",
        "designation",
        "remainingCandidateIds",
        "sourceRef",
        "propositions",
        "providerOutput",
        "diagnostics",
        "promptNote",
        "stageOutputs",
        "worldGraphSpec",
        "caseTruth",
        # tokens / credentials / internal identities
        "token",
        "verifier",
        "tokenVerifier",
        "apiKey",
        "sessionId",
        "anonymousQuotaSessionId",
        "quotaSessionId",
        "pairingSessionId",
        "pairingCode",
        "payload",
    }
)


def test_export_is_a_strict_allowlist_deep_scan(database_url):
    application, _case_id, _creator, payload = _publish_demo_apartment(database_url)
    try:
        doc = sg.project_savegame_v1(payload, difficulty="medium", exported_at=_PINNED_EXPORTED_AT)
        violations = [
            path
            for path in _iter_key_paths(doc)
            if path.rsplit(".", 1)[-1] in _SAVEGAME_FORBIDDEN_KEYS
        ]
        assert violations == [], violations
        # No whole-payload serialization: the document is a FRESH dict.
        assert json.dumps(doc, sort_keys=True) != json.dumps(payload, sort_keys=True)
        assert "propositions" not in json.dumps(doc, sort_keys=True)
    finally:
        _dispose(application)


def test_replay_truth_shape_and_values(database_url):
    """ReplayTruthV1: the four canonical ids + canonical time + the REQUIRED
    tolerance (the reveal DTO omits it) + the public winner labels; nothing
    else."""
    application, _case_id, _creator, payload = _publish_demo_apartment(database_url)
    try:
        doc = sg.project_savegame_v1(payload, difficulty="medium", exported_at=_PINNED_EXPORTED_AT)
        truth = doc["case"]["replayTruth"]
        assert set(truth.keys()) == {
            "murdererId",
            "motiveId",
            "weaponId",
            "crimeTime",
            "accusationToleranceSeconds",
            "murdererName",
            "motiveLabel",
            "weaponName",
        }
        crime = payload["truth"]["crime"]
        assert truth["murdererId"] == crime["murderer_id"]
        assert truth["motiveId"] == crime["motive_id"]
        assert truth["weaponId"] == crime["weapon_id"]
        assert truth["crimeTime"] == crime["crime_time"]["canonical"]
        assert (
            truth["accusationToleranceSeconds"]
            == crime["crime_time"]["accusation_tolerance_seconds"]
        )
        # Public (semantic, DEF-081) labels, never internal ids.
        assert truth["murdererName"] == "Thomas Reed"
        assert truth["weaponName"] == "Kitchen Knife"
        assert "embezzlement" in truth["motiveLabel"]
        # No internal CaseTruth keys anywhere in the document.
        assert not any(
            path.rsplit(".", 1)[-1] == "facts"
            or path.rsplit(".", 1)[-1] == "relationships"
            for path in _iter_key_paths(doc)
        )
    finally:
        _dispose(application)


def test_evidence_records_carry_player_read_content(database_url):
    """Every evidence record carries the player-visible read content shape
    (render payload + Phase 23 interview tags), so the replay runtime can
    serve record reads with zero new parsing."""
    application, _case_id, _creator, payload = _publish_demo_apartment(database_url)
    try:
        doc = sg.project_savegame_v1(payload, difficulty="medium", exported_at=_PINNED_EXPORTED_AT)
        records = doc["case"]["evidence"]
        by_id = {r["evidenceId"]: r for r in records}
        assert "forensic_knife_match_01" in by_id
        rec = by_id["forensic_knife_match_01"]
        assert set(rec.keys()) == {
            "evidenceId",
            "kind",
            "reliability",
            "title",
            "description",
            "content",
        }
        assert rec["content"]["renderType"] == "FORENSIC_COMPARISON"
        ws = by_id["witness_statement_emily_01"]
        assert ws["content"]["renderType"] == "BODY_OBSERVATION"
        assert ws["content"]["questionType"] == "OBSERVATION"
        assert ws["content"]["witnessId"] == "emily_reed"
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# SECRET SENTINELS (§34) — zero occurrences in the exported bytes
# --------------------------------------------------------------------------- #

_API_KEY_SENTINEL = "SECRET-PHASE32-MUST-NOT-PERSIST"
_SESSION_TOKEN_SENTINEL = "PT-SESSION-PHASE32-SECRET-TOKEN"
_BRIDGE_TOKEN_SENTINEL = "BRIDGE-PAIR-PHASE32-SECRET"
_PROMPT_MARKER_SENTINEL = "INTERNAL-PROMPT-PHASE32-SECRET-MARKER"


def _payload_with_sentinels(payload) -> dict:
    """A deep-copied payload with sentinel values planted ONLY in
    NON-allowlisted fields (the projection must never read them)."""
    import copy

    hostile = copy.deepcopy(dict(payload))
    hostile["prompt"] = f"{_PROMPT_MARKER_SENTINEL}\n{hostile.get('prompt', '')}"
    hostile["model"] = _API_KEY_SENTINEL
    hostile["seed"] = _SESSION_TOKEN_SENTINEL
    hostile["locked"] = {"nested": {"secret": _BRIDGE_TOKEN_SENTINEL}}
    hostile["report"] = {"nested": {"secret": _API_KEY_SENTINEL}}
    hostile["universes"] = {"nested": {"secret": _BRIDGE_TOKEN_SENTINEL}}
    hostile["solverProof"] = {"nested": {"secret": _SESSION_TOKEN_SENTINEL}}
    hostile["truth"]["crime"]["internal_secret_note"] = _PROMPT_MARKER_SENTINEL
    hostile["draft"]["evidence"][0]["source_ref"]["internal_secret"] = (
        _API_KEY_SENTINEL
    )
    hostile["draft"]["evidence"][0]["propositions"][0]["internal_secret"] = (
        _SESSION_TOKEN_SENTINEL
    )
    return hostile


def test_secret_sentinels_absent_from_exported_bytes(database_url):
    application, _case_id, _creator, payload = _publish_demo_apartment(database_url)
    try:
        hostile = _payload_with_sentinels(payload)
        exported = sg.serialize_savegame_v1(
            sg.project_savegame_v1(hostile, difficulty="medium", exported_at=_PINNED_EXPORTED_AT)
        )
        for sentinel in (
            _API_KEY_SENTINEL,
            _SESSION_TOKEN_SENTINEL,
            _BRIDGE_TOKEN_SENTINEL,
            _PROMPT_MARKER_SENTINEL,
        ):
            assert exported.count(sentinel) == 0, sentinel
        # The projection is a strict allowlist: the source payload itself is
        # never serialized (a marker in NON-allowlisted fields never reaches
        # the output).
        assert json.dumps(hostile, sort_keys=True) != exported
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# ISOLATION + DETERMINISM + SIZE BOUND
# --------------------------------------------------------------------------- #


def test_export_does_not_mutate_payload_or_db(database_url):
    """The export is read-only: the published payload bytes, the playthrough
    lifecycle and the case row are never touched by a successful export."""
    application, case_id, creator, payload = _publish_demo_apartment(database_url)
    try:
        store = application.state.store
        before_payload = store.get_published(case_id, 1).payload_json
        before_case = store.get_case(case_id)
        with TestClient(application) as c:
            pt_id, pt_token = _create_playthrough(c, case_id, creator)
            c.post(
                f"/api/v1/playthroughs/{pt_id}/accusation",
                json=_winning_body(payload),
                headers=auth(pt_token),
            )
            before_state = store.get_playthrough_state(pt_id)
            res = c.get(
                f"/api/v1/playthroughs/{pt_id}/savegame", headers=auth(pt_token)
            )
            assert res.status_code == 200
            after_state = store.get_playthrough_state(pt_id)
        assert after_state == before_state == "ACCUSED"
        assert store.get_published(case_id, 1).payload_json == before_payload
        after_case = store.get_case(case_id)
        assert after_case.difficulty == before_case.difficulty
        assert after_case.title == before_case.title
    finally:
        _dispose(application)


def test_concurrent_service_reads_are_clean_and_deterministic(database_url):
    """Two projections of the same payload are byte-identical (sorted keys) and
    repeat service reads return identical bytes."""
    application, _case_id, _creator, payload = _publish_demo_apartment(database_url)
    try:
        first = sg.serialize_savegame_v1(
            sg.project_savegame_v1(payload, difficulty="medium", exported_at=_PINNED_EXPORTED_AT)
        )
        second = sg.serialize_savegame_v1(
            sg.project_savegame_v1(payload, difficulty="medium", exported_at=_PINNED_EXPORTED_AT)
        )
        assert first == second
        # A different export timestamp changes ONLY exportedAt.
        later = sg.project_savegame_v1(payload, difficulty="medium", exported_at="2026-10-07T00:00:00Z")
        first_doc = json.loads(first)
        assert later["exportedAt"] == "2026-10-07T00:00:00Z"
        assert later["case"] == first_doc["case"]
    finally:
        _dispose(application)


def test_canonical_and_live_exports_below_size_bound(database_url):
    """The canonical demo export is ~25 KiB, orders of magnitude below the
    documented 5 MiB hard cap."""
    fixture_bytes = _CANONICAL_FIXTURE.stat().st_size
    assert fixture_bytes < 1 * 1024 * 1024
    application, _case_id, _creator, payload = _publish_demo_apartment(database_url)
    try:
        exported = sg.serialize_savegame_v1(
            sg.project_savegame_v1(payload, difficulty="medium", exported_at=_PINNED_EXPORTED_AT)
        )
        assert len(exported.encode("utf-8")) < sg.MAX_EXPORT_BYTES
        assert len(exported.encode("utf-8")) < 1 * 1024 * 1024
    finally:
        _dispose(application)


def test_serializer_enforces_size_bound():
    """An over-bound export raises ``SavegameTooLargeError`` (sanitized 500 at
    the API): the transport can never emit an unbounded document."""
    doc = {
        "format": sg.SAVEGAME_FORMAT,
        "formatVersion": sg.SAVEGAME_FORMAT_VERSION,
        "case": {"metadata": {}, "replayTruth": {}},
    }
    with pytest.raises(sg.SavegameTooLargeError):
        sg.serialize_savegame_v1(doc, max_bytes=10)
    # The real bound accepts the same document.
    text = sg.serialize_savegame_v1(doc)
    assert len(text.encode("utf-8")) <= sg.MAX_EXPORT_BYTES


# --------------------------------------------------------------------------- #
# safe filename/MIME (§42 safe filename)
# --------------------------------------------------------------------------- #


def test_savegame_filename_is_safe():
    assert (
        sg.savegame_filename("CASE-9rrTX0vTGdDA")
        == "procedural-detective-case-CASE-9rrTX0vTGdDA.pdcase"
    )
    # Dots/slashes/backslashes/spaces/quotes are all reduced to safe chars —
    # no `..` shape, no separators, no quotes can survive.
    assert (
        sg.savegame_filename('a/b\\c..d "e"')
        == "procedural-detective-case-a_b_c_d_e.pdcase"
    )
    assert sg.savegame_filename("") == "procedural-detective-case-case.pdcase"
    for hostile in ('..\\..', "/etc/passwd", "C:\\secret", "\x00\x1f", ".", "..."):
        name = sg.savegame_filename(hostile)
        assert ".." not in name
        assert "/" not in name and "\\" not in name
        assert name.startswith("procedural-detective-case-")
        assert name.endswith(".pdcase")


def test_projection_rejects_payload_without_draft():
    with pytest.raises(ValueError):
        sg.project_savegame_v1({"caseId": "CASE-1"}, difficulty=None)


def test_projection_fails_closed_without_truth():
    """A payload with a draft but NO truth section can never export a partial
    replay truth (the SAME fail-closed rule as the reveal projection)."""
    from app.services.reveal import RevealProjectionError

    with pytest.raises(RevealProjectionError):
        sg.project_savegame_v1(
            {
                "draft": {"scene": {}, "evidence": []},
                "caseId": "CASE-1",
                "caseVersion": 1,
                "title": "T",
                "model": None,
            },
            difficulty=None,
        )


# --------------------------------------------------------------------------- #
# DEF-051 (ADV-32F-06) — fail closed on a scene-less/malformed scene; never
# emit `scene.location: {locationId: null, name: null}` (which the frontend
# import rejects).
# --------------------------------------------------------------------------- #


def test_projection_fails_closed_when_scene_is_absent(database_url):
    """A full real payload (truth intact) with NO ``draft.scene`` raises a
    TYPED ``SavegameProjectionError`` instead of emitting a null-location
    scene — an export the pipeline could produce is always importable. The
    SAME payload WITHOUT the mutation still projects a non-null location (the
    fix must not reject normal payloads)."""
    application, _case_id, _creator, payload = _publish_demo_apartment(database_url)
    try:
        import copy

        intact = sg.project_savegame_v1(
            payload,
            difficulty="medium",
            exported_at=_PINNED_EXPORTED_AT,
        )
        location = intact["case"]["scene"]["location"]
        assert location["locationId"] and location["name"]

        hostile = copy.deepcopy(dict(payload))
        hostile["draft"]["scene"] = None
        with pytest.raises(sg.SavegameProjectionError):
            sg.project_savegame_v1(
                hostile,
                difficulty="medium",
                exported_at=_PINNED_EXPORTED_AT,
            )
    finally:
        _dispose(application)


def test_projection_fails_closed_when_scene_and_other_sections_missing(database_url):
    """A payload missing BOTH ``draft.scene`` AND other required sections (the
    truth section) still FAILS CLOSED with a TYPED projection error — the
    pipeline never falls through to a document that the frontend import would
    reject (DEF-051 / ADV-32F-06)."""
    application, _case_id, _creator, payload = _publish_demo_apartment(database_url)
    try:
        import copy

        from app.services.reveal import RevealProjectionError

        hostile = copy.deepcopy(dict(payload))
        hostile["draft"]["scene"] = None
        hostile.pop("truth", None)
        with pytest.raises((sg.SavegameProjectionError, RevealProjectionError)):
            sg.project_savegame_v1(
                hostile,
                difficulty="medium",
                exported_at=_PINNED_EXPORTED_AT,
            )
    finally:
        _dispose(application)


def test_projection_fails_closed_when_scene_is_malformed(database_url):
    """A scene that is NOT a mapping or lacks a non-empty location_id/name
    fails closed with the same typed error (never ``locationId: null``)."""
    application, _case_id, _creator, payload = _publish_demo_apartment(database_url)
    try:
        import copy

        for scene_variant in ({}, {"location_id": None, "name": "Kitchen"}, "STRING"):
            hostile = copy.deepcopy(dict(payload))
            hostile["draft"]["scene"] = scene_variant
            with pytest.raises(sg.SavegameProjectionError):
                sg.project_savegame_v1(
                    hostile,
                    difficulty="medium",
                    exported_at=_PINNED_EXPORTED_AT,
                )
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# DEF-052 (ADV-32F-07) — difficulty is bounded to the documented enum
# (easy|medium|hard) or null; an out-of-vocabulary label is normalized
# deterministically so the export stays importable.
# --------------------------------------------------------------------------- #


def test_difficulty_out_of_vocabulary_is_normalized_to_null(database_url):
    """``metadata.difficulty`` accepts ONLY the documented closed vocabulary
    (REQUIREMENTS 35 / ``SAVEGAME_DIFFICULTIES``); ``"extreme"``,
    ``"REQUIREMENTS"`` and any other unknown/non-string value normalize to
    ``null`` deterministically (DEF-052 / ADV-32F-07). Every emitted document
    carries a difficulty the frontend import accepts
    (``metadata.difficulty in {None, "easy", "medium", "hard"}``)."""
    application, _case_id, _creator, payload = _publish_demo_apartment(database_url)
    try:
        assert sg.SAVEGAME_DIFFICULTIES == frozenset({"easy", "medium", "hard"})
        for difficulty in ("extreme", "REQUIREMENTS", "", None, 42, {}, "HARD-CORE"):
            doc = sg.project_savegame_v1(
                payload,
                difficulty=difficulty,  # type: ignore[arg-type]
                exported_at=_PINNED_EXPORTED_AT,
            )
            emitted = doc["case"]["metadata"]["difficulty"]
            assert emitted is None, difficulty
            assert emitted in (None, "easy", "medium", "hard")
        for difficulty in ("easy", "medium", "hard"):
            doc = sg.project_savegame_v1(
                payload,
                difficulty=difficulty,
                exported_at=_PINNED_EXPORTED_AT,
            )
            emitted = doc["case"]["metadata"]["difficulty"]
            assert emitted == difficulty
            assert emitted in (None, "easy", "medium", "hard")
    finally:
        _dispose(application)


def test_wire_export_of_out_of_vocabulary_difficulty_stays_importable(database_url):
    """The full route path: a case row whose difficulty is out of vocabulary is
    exported with ``difficulty: null`` (the frontend import accepts that)."""
    application, case_id, creator, payload = _publish_demo_apartment(
        database_url, difficulty="extreme"
    )
    try:
        with TestClient(application) as c:
            pt_id, pt_token = _create_playthrough(c, case_id, creator)
            c.post(
                f"/api/v1/playthroughs/{pt_id}/accusation",
                json=_winning_body(payload),
                headers=auth(pt_token),
            )
            export = c.get(
                f"/api/v1/playthroughs/{pt_id}/savegame", headers=auth(pt_token)
            )
            assert export.status_code == 200, export.text
            doc = json.loads(export.text)
            assert doc["case"]["metadata"]["difficulty"] is None
    finally:
        _dispose(application)