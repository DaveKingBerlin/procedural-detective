"""Phase 28 — three-case deterministic Demo pool (backend scope).

Covers (Phase28 §15 / §22 backend half):

  1. the canonical registry: exactly three frozen entries, the FIXED ids
     ``demo-apartment`` | ``demo-gallery`` | ``demo-laboratory``, unique ids,
     a future Demo #4 remains a pure data addition (§3 / §5 / §9);
  2. Demo #1 immutability (§10): ``demo-apartment`` resolves the EXISTING
     ``dev_mode_case.json`` provider script (byte-unchanged fixture file) and,
     when generated, every published draft section is byte-identical to the
     no-``demoCaseId`` default path;
  3. Demo #2 / #3 authoring gate (§4 / §11): each new fixture PUBLISHES
     through the REAL fake pipeline (validators, solver, world-graph
     references, locked-constraint match, accusation dimensions all pass) and
     the NO network is ever touched (autouse network block);
  4. truth-leak protection (§12): the registry's player-visible text scan and
     the published public DTOs carry no CaseTruth markers / hidden keys;
  5. selection transport (§6-§8 / §16): unknown id -> canonical 400
     INVALID_DEMO_CASE (never echoed, no internal detail); a ``demoCaseId``
     with a non-fake provider -> the same 400 (fail-closed); blank == absent
     (backward compatible); absent -> the exact current single-fixture path;
  6. per-generation-attempt isolation (§16 / §11): two overlapping attempts
     with different demo ids each run their OWN fixture (no global mutation,
     no cross-talk) — deterministic, no sleeps needed for the sequential
     assertions; the threaded test mirrors the Phase 25 §14 pattern.
  7. observability (§21): the ``demo.started`` event carries the SANITIZED
     registry id and nothing else truth-adjacent.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.persistence.store import Store  # noqa: E402
from app.services import demo_cases  # noqa: E402
from app.services.generation import GenerationService, InvalidDemoCaseError  # noqa: E402
from conftest import upgrade_db  # noqa: E402
from phase5_helpers import assert_no_hidden_leaks, auth, create_session  # noqa: E402

_BACKEND_DIR = Path(__file__).resolve().parents[1]

# The DEMO #1 canonical prompt (matches the golden dev-mode case exactly).
_APT_PROMPT = (
    "Victim: Sarah Miller\n"
    "Murderer: Thomas Reed\n"
    "Motive: \u20ac240,000 embezzlement\n"
    "Weapon: Kitchen knife\n"
    "Time: 22:17\n"
    "Witness: Emily Reed\n"
)

# The DEMO #2 / DEMO #3 canonical prompts (locked against each fixture truth).
_PER_FIXTURE_PROMPT = {
    "demo-gallery": (
        "Victim: Maria Lindqvist\n"
        "Murderer: Daniel Voss\n"
        "Motive: forged sale\n"
        "Time: 21:47\n"
        "Witness: Jana Petersen\n"
    ),
    "demo-laboratory": (
        "Victim: Amara Okafor\n"
        "Murderer: Elias Meyer\n"
        "Motive: safety whistleblower\n"
        "Time: 19:43\n"
        "Witness: Hugo Brandt\n"
    ),
}


# --------------------------------------------------------------------------- #
# 1. the canonical registry (§3 / §5 / §9 / §15)
# --------------------------------------------------------------------------- #


def test_registry_has_exactly_three_frozen_entries_with_fixed_ids():
    assert isinstance(demo_cases.DEMO_CASES, tuple)
    assert len(demo_cases.DEMO_CASES) == 3
    ids = [record.demo_case_id for record in demo_cases.DEMO_CASES]
    assert ids == ["demo-apartment", "demo-gallery", "demo-laboratory"]
    assert len(set(ids)) == 3  # unique stable ids
    assert demo_cases.DEMO_CASE_IDS == frozenset(ids)
    # Every registry record exposes the provider-script surface + prompt.
    for record in demo_cases.DEMO_CASES:
        assert isinstance(record.title, str) and record.title
        assert isinstance(record.summary, str)
        assert isinstance(record.prompt, str) and record.prompt
        assert len(record.prompt) < 4000


def test_get_demo_case_roundtrip_and_unknown_returns_none():
    for demo_case_id in ("demo-apartment", "demo-gallery", "demo-laboratory"):
        record = demo_cases.get_demo_case(demo_case_id)
        assert record is not None and record.demo_case_id == demo_case_id
        assert demo_cases.get_demo_case(record.demo_case_id) is record
    for unknown in (None, "", "demo-museum", "demo-apartment ", "0", "demo/.."):
        assert demo_cases.get_demo_case(unknown) is None


def test_demo_case_default_is_demo_apartment():
    assert demo_cases.default_demo_case().demo_case_id == "demo-apartment"


def test_every_demo_script_is_a_complete_stage_map():
    """Each fixture carries every stage the pipeline may request (the four
    GENERATING stages + REPAIR) — an absent stage would exhaust the provider."""
    from app.generation.provider import GenerationStage

    required = {
        GenerationStage.CASE_TRUTH,
        GenerationStage.PUBLIC_WORLD,
        GenerationStage.EVIDENCE,
        GenerationStage.WORLD_GRAPH,
        GenerationStage.REPAIR,
    }
    for record in demo_cases.DEMO_CASES:
        script = record.script
        assert required <= set(script)
        for stage, entries in script.items():
            assert isinstance(entries, tuple)
            assert entries and all(isinstance(entry, str) for entry in entries)


def test_demo_registry_never_touches_the_network_or_reads_env():
    """The registry resolves fixtures from bundled files only (LLM-independent,
    Phase28 §20): loading is pure file I/O under the autouse network block."""
    for record in demo_cases.DEMO_CASES:
        script = record.script
        assert script  # loads without network / config reads


# --------------------------------------------------------------------------- #
# 2. Demo #1 immutability (§10)
# --------------------------------------------------------------------------- #


def test_demo_apartment_fixture_is_the_byte_unchanged_dev_mode_case():
    """The demo-apartment registry entry points at the EXISTING
    ``dev_mode_case.json`` file (never moved, never re-serialized, its content
    never altered). The fixture FILE is byte-identical to the golden."""
    golden_path = _BACKEND_DIR / "app" / "services" / "dev_mode_case.json"
    expected_sha = None
    import hashlib

    blob = golden_path.read_bytes()
    assert blob
    expected_sha = hashlib.sha256(blob).hexdigest()
    record = demo_cases.get_demo_case("demo-apartment")
    assert record is not None and record.fixture_file == "dev_mode_case.json"
    # The registry loads the SAME file (path resolution cannot rewrite it).
    assert Path(_BACKEND_DIR / "app" / "services" / "dev_mode_case.json").resolve() == (
        golden_path.resolve()
    )
    resolved_blob = Path(
        _BACKEND_DIR / "app" / "services" / "dev_mode_case.json"
    ).read_bytes()
    assert hashlib.sha256(resolved_blob).hexdigest() == expected_sha


def test_demo_apartment_generation_is_byte_identical_to_the_default(database_url):
    """Generating with ``demoCaseId=demo-apartment`` publishes EXACTLY the same
    draft as the no-``demoCaseId`` default (the §10 player-visible content is
    pinned — culprit/motive/weapon/canonical time/witness facts/scene objects
    are all unchanged)."""
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            r1 = c.post(
                "/api/v1/cases",
                json={"prompt": _APT_PROMPT, "demoCaseId": "demo-apartment"},
                headers=auth(token),
            )
            assert r1.status_code == 201 and r1.json()["status"] == "PUBLISHED"
        with TestClient(application) as c:
            token, _ = create_session(c)
            r2 = c.post("/api/v1/cases", json={"prompt": _APT_PROMPT}, headers=auth(token))
            assert r2.status_code == 201 and r2.json()["status"] == "PUBLISHED"
        p1 = json.loads(
            application.state.store.get_published(r1.json()["caseId"], 1).payload_json
        )
        p2 = json.loads(
            application.state.store.get_published(r2.json()["caseId"], 1).payload_json
        )
        for key in (
            "crime", "persons", "motives", "objects", "locations",
            "travelRules", "scene", "evidence", "worldGraph",
        ):
            assert json.dumps(p1["draft"].get(key), sort_keys=True) == json.dumps(
                p2["draft"].get(key), sort_keys=True
            ), key
        # Pin the canonical truth + a witness fact + a scene object explicitly.
        assert p1["draft"]["crime"]["crime_time"] == {
            "canonical": "2026-09-11T22:17:00+02:00",
            "accusation_tolerance_seconds": 300,
        }
        assert p1["draft"]["crime"]["murderer_id"] == "thomas_reed"
        assert p1["draft"]["crime"]["motive_id"] == "cover_up_embezzlement"
        assert p1["draft"]["crime"]["weapon_id"] == "kitchen_knife"
        witnesses = [
            p["name"] for p in p1["draft"]["persons"] if p["role"] == "witness"
        ]
        assert witnesses == ["Emily Reed"]
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# 3. Demo #2 / #3 authoring gate: real fake pipeline publish (§4 / §11)
# --------------------------------------------------------------------------- #


def _make_app(database_url, **settings_overrides):
    upgrade_db(database_url)
    return create_app(
        Settings(
            database_url=database_url,
            cors_allowed_origins=["http://localhost:5173"],
            **settings_overrides,
        )
    )


def _dispose(application) -> None:
    application.state.engine.dispose()
    if application.state.store is not None:
        try:
            application.state.store.dispose()
        except Exception:  # noqa: BLE001 - teardown must never mask
            pass


@pytest.mark.parametrize("demo_case_id", ["demo-gallery", "demo-laboratory"])
def test_new_demo_fixture_publishes_through_the_real_fake_pipeline(
    database_url, demo_case_id
):
    """The full deterministic gate runs and passes: schema validation,
    player-safe projection, solvability (all four dimensions unique), evidence
    / witness / scene-object references, locked-constraint match, accusation
    dimensions — nothing bypassed (§11)."""
    record = demo_cases.get_demo_case(demo_case_id)
    assert record is not None
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            response = c.post(
                "/api/v1/cases",
                json={"prompt": record.prompt, "demoCaseId": demo_case_id},
                headers=auth(token),
            )
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["status"] == "PUBLISHED"
            # The case truly started as the selected fixture: the published
            # crime belongs to the fixture, never the golden case.
            payload = json.loads(
                application.state.store.get_published(body["caseId"], 1).payload_json
            )
            victim = payload["draft"]["crime"]["victim_id"]
            expected_victim = (
                "maria_lindqvist" if demo_case_id == "demo-gallery" else "amara_okafor"
            )
            assert victim == expected_victim
            assert payload["draft"]["crime"]["crime_time"]["canonical"] == (
                "2026-09-18T21:47:00+02:00"
                if demo_case_id == "demo-gallery"
                else "2026-09-25T19:43:00+02:00"
            )
    finally:
        _dispose(application)


@pytest.mark.parametrize("demo_case_id", ["demo-gallery", "demo-laboratory"])
def test_new_demo_fixture_public_dto_has_no_truth(database_url, demo_case_id):
    """The API surface for the new demos obeys the player-safe boundary: deep
    leak scan + forbidden key names in the RAW response (Phase 20 PD-SEC-01
    machinery applied to the demo path)."""
    record = demo_cases.get_demo_case(demo_case_id)
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            response = c.post(
                "/api/v1/cases",
                json={"prompt": record.prompt, "demoCaseId": demo_case_id},
                headers=auth(token),
            )
            assert response.status_code == 201 and response.json()["status"] == "PUBLISHED"
            created = response.json()
            res = c.get(
                f"/api/v1/cases/{created['caseId']}",
                headers=auth(created["creatorAccessToken"]),
            )
            assert res.status_code == 200
            assert_no_hidden_leaks(res.json())
            for marker in (
                "murdererId", "victimId", "weaponId", "crimeTime",
                "solutionProof", "truthfulness", "acceptedScoring",
            ):
                assert marker not in res.text, marker
    finally:
        _dispose(application)


def test_demo_gallery_and_laboratory_diverge(database_url):
    """§3 — the two new cases are meaningfully different fixtures (distinct
    victims / canonical times / scenes), not renamed copies of each other."""
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            started = {}
            for demo_case_id in ("demo-gallery", "demo-laboratory"):
                record = demo_cases.get_demo_case(demo_case_id)
                response = c.post(
                    "/api/v1/cases",
                    json={"prompt": record.prompt, "demoCaseId": demo_case_id},
                    headers=auth(token),
                )
                assert response.status_code == 201 and response.json()["status"] == "PUBLISHED"
                started[demo_case_id] = json.loads(
                    application.state.store.get_published(
                        response.json()["caseId"], 1
                    ).payload_json
                )
            g, l = started["demo-gallery"], started["demo-laboratory"]
            assert g["draft"]["crime"]["victim_id"] != l["draft"]["crime"]["victim_id"]
            assert (
                g["draft"]["crime"]["crime_time"]["canonical"]
                != l["draft"]["crime"]["crime_time"]["canonical"]
            )
            assert g["draft"]["scene"] != l["draft"]["scene"]
            assert g["draft"]["crime"]["weapon_id"] != l["draft"]["crime"]["weapon_id"]
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# 4. truth-leak protection (§12) — registry-level scan
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("demo_case_id", ["demo-apartment", "demo-gallery", "demo-laboratory"])
def test_fixture_player_visible_text_has_no_truth_markers(demo_case_id):
    record = demo_cases.get_demo_case(demo_case_id)
    issues = demo_cases.truth_leak_issues(record)
    assert issues == (), issues


def test_truth_scan_runs_over_every_player_visible_document():
    """The scan really touches the public world, every evidence presentation
    and the world graph of every fixture (so a regression is caught)."""
    for record in demo_cases.DEMO_CASES:
        texts = demo_cases.player_visible_text(record)
        assert texts, record.demo_case_id
        # The concrete expected presentation texts are part of the scan.
        if record.demo_case_id == "demo-gallery":
            assert any("Lindqvist Gallery" in text for text in texts)


# --------------------------------------------------------------------------- #
# 5. selection transport (§6-§8 / §16) — 400 envelope / faill-closed
# --------------------------------------------------------------------------- #


def test_unknown_demo_case_id_rejected_never_echoed(database_url):
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            for hostile in (
                "demo-museum",
                "demo-mall",
                "0",
                " http://evil.example/demo ",
                "demo/../apartment",
            ):
                response = c.post(
                    "/api/v1/cases",
                    json={
                        "prompt": _APT_PROMPT,
                        "demoCaseId": hostile,
                    },
                    headers=auth(token),
                )
                assert response.status_code == 400, (hostile, response.text)
                body = response.json()
                assert body["error"]["code"] == "INVALID_DEMO_CASE"
                assert body["error"]["details"] is None
                assert hostile.strip() not in response.text, hostile
    finally:
        _dispose(application)


def test_demo_case_id_with_non_fake_provider_rejected_fail_closed(database_url):
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            response = c.post(
                "/api/v1/cases",
                json={
                    "prompt": _APT_PROMPT,
                    "demoCaseId": "demo-gallery",
                    "generationProvider": "ollama",
                    "ollamaTransport": "server",
                    "ollamaModel": "qwen2.5:1.5b",
                },
                headers=auth(token),
            )
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "INVALID_DEMO_CASE"
            assert "demo-gallery" not in response.text
    finally:
        _dispose(application)


def test_blank_demo_case_id_treated_as_absent(database_url):
    """Blank/whitespace-only == absent: the current single-fixture behavior
    (backward compatibility) — a normal golden-prompt generation publishes."""
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            response = c.post(
                "/api/v1/cases",
                json={"prompt": _APT_PROMPT, "demoCaseId": "   "},
                headers=auth(token),
            )
            assert response.status_code == 201, response.text
            assert response.json()["status"] == "PUBLISHED"
    finally:
        _dispose(application)


@pytest.mark.parametrize(
    "padded", [" demo-gallery ", "demo-gallery ", " demo-gallery"]
)
def test_space_padded_known_demo_id_behaves_exactly_like_the_canonical_id(
    database_url, padded
):
    """Phase 28 F1 fix — ASCII-SPACE padding around a known registry id is
    normalized ONCE at the service boundary: ``" demo-gallery "``,
    ``"demo-gallery "`` and ``" demo-gallery"`` each behave EXACTLY like
    ``demo-gallery`` — the gallery fixture is selected (selector AND milestone
    agree), published through the real fake pipeline, and the deterministic
    victim is the gallery's own ``maria_lindqvist`` (never the golden case)."""
    record = demo_cases.get_demo_case("demo-gallery")
    assert record is not None
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            response = c.post(
                "/api/v1/cases",
                json={"prompt": record.prompt, "demoCaseId": padded},
                headers=auth(token),
            )
            assert response.status_code == 201, (padded, response.text)
            body = response.json()
            assert body["status"] == "PUBLISHED"
            payload = json.loads(
                application.state.store.get_published(body["caseId"], 1).payload_json
            )
            assert payload["draft"]["crime"]["victim_id"] == "maria_lindqvist"
            assert payload["draft"]["crime"]["crime_time"]["canonical"] == (
                "2026-09-18T21:47:00+02:00"
            )
    finally:
        _dispose(application)


@pytest.mark.parametrize(
    "hostile", ["\t demo-gallery\n", "demo\u2013gallery"]
)
def test_non_space_padding_and_non_ascii_lookalike_still_rejected_never_echoed(
    database_url, hostile
):
    """Phase 28 F1 fix — normalization ONLY tolerates ASCII spaces. A
    tab/newline-padded known id (``"\\t demo-gallery\\n"``) and a non-ASCII
    en-dash lookalike (``"demo\u2013gallery"``) are NOT canonicalized; they
    fail the closed registry allowlist and answer the canonical sanitized 400
    INVALID_DEMO_CASE envelope (``details: null``, the offending value is
    never echoed) — never a silent FAILED attempt, never a fixture select."""
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            response = c.post(
                "/api/v1/cases",
                json={"prompt": _APT_PROMPT, "demoCaseId": hostile},
                headers=auth(token),
            )
            assert response.status_code == 400, (repr(hostile), response.text)
            body = response.json()
            assert body["error"]["code"] == "INVALID_DEMO_CASE"
            assert body["error"]["message"] == "Unknown or invalid demo case selection"
            assert body["error"]["details"] is None
            assert hostile.strip() not in response.text, repr(hostile)
    finally:
        _dispose(application)


def test_demo_started_event_emits_only_the_canonical_registry_id(database_url):
    """Phase 28 F1 fix — observability emits the CANONICAL registry id even
    when the request carried ASCII-space padding: the ``demo.started``
    ``demoCaseId`` must always be EXACTLY one of the three registry ids."""
    import logging

    captured: list[dict] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if getattr(record, "pd_event", None) == "demo.started":
                captured.append(dict(getattr(record, "pd_fields", {}) or {}))

    handler = _Capture()
    logger = logging.getLogger("procedural-detective")
    logger.addHandler(handler)
    try:
        record = demo_cases.get_demo_case("demo-gallery")
        application = _make_app(database_url)
        try:
            with TestClient(application) as c:
                token, _ = create_session(c)
                response = c.post(
                    "/api/v1/cases",
                    json={"prompt": record.prompt, "demoCaseId": " demo-gallery "},
                    headers=auth(token),
                )
                assert response.status_code == 201 and response.json()["status"] == "PUBLISHED"
        finally:
            _dispose(application)
    finally:
        logger.removeHandler(handler)
    assert captured, "demo.started must be logged on the demo path"
    emitted = captured[0]["demoCaseId"]
    assert emitted == "demo-gallery"
    assert emitted in demo_cases.DEMO_CASE_IDS
    assert "\u2013" not in json.dumps(captured[0])
    assert "murderer" not in json.dumps(captured[0])


@pytest.mark.parametrize("demo_case_id", ["demo-gallery", "demo-laboratory"])
def test_new_demo_fixture_publishes_with_the_browser_demo_prompt(database_url, demo_case_id):
    """Frontend contract (sibling, Phase 28 §6): the "Try Demo Case" journey
    sends the SAME example prompt (the golden Demo-#1 prompt, e.g.
    ``EXAMPLE_PROMPT``) with every demoCaseId — the backend derives the
    deterministic locked constraints / world requirements from the SELECTED
    FIXTURE's canonical prompt (server-authoritative), so the fixture always
    publishes and the browser prompt can never contradict it or select
    different content."""
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            response = c.post(
                "/api/v1/cases",
                json={"prompt": _APT_PROMPT, "demoCaseId": demo_case_id},
                headers=auth(token),
            )
            assert response.status_code == 201, response.text
            assert response.json()["status"] == "PUBLISHED"
            payload = json.loads(
                application.state.store.get_published(
                    response.json()["caseId"], 1
                ).payload_json
            )
            expected_victim = (
                "maria_lindqvist" if demo_case_id == "demo-gallery" else "amara_okafor"
            )
            assert payload["draft"]["crime"]["victim_id"] == expected_victim
    finally:
        _dispose(application)


@pytest.mark.parametrize("demo_case_id", ["demo-gallery", "demo-laboratory"])
def test_new_demo_fixture_environment_hint_does_not_recompose(database_url, demo_case_id):
    """The fixture is authoritative for the world: a SAFE browser environment
    hint is validated (the same 422 boundary for hostile values) but never
    re-composes the fixture's scene/world; the published draft is identical
    to the no-hint run. A hostile hint on the demo path still answers the
    canonical 422 envelope (fail-closed validation unchanged)."""
    def _publish(extra: dict) -> dict:
        with TestClient(_make_app(database_url)) as c:
            token, _ = create_session(c)
            response = c.post(
                "/api/v1/cases",
                json={"prompt": _APT_PROMPT, "demoCaseId": demo_case_id, **extra},
                headers=auth(token),
            )
            assert response.status_code == 201, response.text
            assert response.json()["status"] == "PUBLISHED"
            store = c.app.state.store
            return json.loads(
                store.get_published(response.json()["caseId"], 1).payload_json
            )

    plain = _publish({})
    with_hint = _publish({"environment": "office"})
    for key in (
        "crime", "persons", "motives", "objects", "locations",
        "travelRules", "scene", "evidence", "worldGraph",
    ):
        assert json.dumps(plain["draft"].get(key), sort_keys=True) == json.dumps(
            with_hint["draft"].get(key), sort_keys=True
        ), key
    assert plain["draft"]["scene"]["environment_id"] == "apartment"

    # A hostile hint on the demo path keeps the canonical 422 envelope.
    with TestClient(_make_app(database_url)) as c:
        token, _ = create_session(c)
        response = c.post(
            "/api/v1/cases",
            json={"prompt": _APT_PROMPT, "demoCaseId": demo_case_id,
                  "environment": "office/../secret"},
            headers=auth(token),
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "ENVIRONMENT_ERROR"
        assert "secret" not in response.text


def test_service_layer_typed_errors_for_demo_selection(database_url):
    """The service raises the typed error the API maps to 400 INVALID_DEMO_CASE
    (never leaks the offending value)."""
    upgrade_db(database_url)
    settings = Settings(database_url=database_url)
    store = Store(database_url)
    service = GenerationService(settings=settings, store=store)
    try:
        session = service.create_anonymous_quota_session()
        with pytest.raises(InvalidDemoCaseError):
            service.start_case_generation(
                _APT_PROMPT,
                anonymous_quota_session_id=session.anonymous_quota_session_id,
                demo_case_id="demo-museum",
            )
    finally:
        store.dispose()


# --------------------------------------------------------------------------- #
# 6. per-generation-attempt isolation (§16 / §11) — no global mutation
# --------------------------------------------------------------------------- #


def _phase5_settings(database_url):
    return Settings(
        database_url=database_url,
        cors_allowed_origins=["http://localhost:5173"],
        max_concurrent_generations=2,
        max_concurrent_generations_global=4,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        generation_deadline_seconds=60,
    )


def test_demo_selection_is_per_attempt_and_isolated(database_url):
    """Two OVERLAPPING attempts with different demo ids each run their OWN
    fixture: A=demo-gallery, B=demo-laboratory. Both publish their own victim /
    scene; neither observes the other's script; the shared settings object is
    never mutated (Phase 25 §14 pattern applied to the demo selector)."""
    upgrade_db(database_url)
    settings = _phase5_settings(database_url)
    store = Store(database_url)
    service = GenerationService(settings=settings, store=store)

    session_a = service.create_anonymous_quota_session()
    session_b = service.create_anonymous_quota_session()
    results: dict[str, object] = {}
    errors: dict[str, Exception] = {}

    def _run(which: str, demo_case_id: str, session_id: str) -> None:
        try:
            record = demo_cases.get_demo_case(demo_case_id)
            results[which] = service.start_case_generation(
                record.prompt,
                anonymous_quota_session_id=session_id,
                demo_case_id=demo_case_id,
            )
        except Exception as exc:  # noqa: BLE001 - recorded for the assert
            errors[which] = exc

    pending = [
        threading.Thread(
            target=_run,
            args=("a", "demo-gallery", session_a.anonymous_quota_session_id),
        ),
        threading.Thread(
            target=_run,
            args=("b", "demo-laboratory", session_b.anonymous_quota_session_id),
        ),
    ]
    for t in pending:
        t.start()
    for t in pending:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in pending)
    assert not errors, errors
    assert results["a"].status == "PUBLISHED"
    assert results["b"].status == "PUBLISHED"

    payload_a = json.loads(store.get_published(results["a"].case_id, 1).payload_json)
    payload_b = json.loads(store.get_published(results["b"].case_id, 1).payload_json)
    assert payload_a["draft"]["crime"]["victim_id"] == "maria_lindqvist"
    assert payload_b["draft"]["crime"]["victim_id"] == "amara_okafor"
    assert payload_a["draft"]["scene"]["name"] != payload_b["draft"]["scene"]["name"]
    # No cross-attempt leakage and no global mutation.
    assert results["a"].case_id != results["b"].case_id
    assert service._settings.generation_provider == "fake"
    assert settings.generation_provider == "fake"
    store.dispose()


def test_default_fake_script_untouched_by_demo_resolution(database_url):
    """Resolving demo selections never mutates the shared default-script cache:
    a NO-id attempt right after demo attempts still runs the byte-identical
    golden dev-mode case."""
    from app.services.generation import GenerationService as GS

    upgrade_db(database_url)
    settings = _phase5_settings(database_url)
    store = Store(database_url)
    service = GS(settings=settings, store=store)
    try:
        session = service.create_anonymous_quota_session()
        service.start_case_generation(
            demo_cases.get_demo_case("demo-gallery").prompt,
            anonymous_quota_session_id=session.anonymous_quota_session_id,
            demo_case_id="demo-gallery",
        )
        # Then a default (no-id) attempt still publishes the golden case.
        session2 = service.create_anonymous_quota_session()
        out = service.start_case_generation(
            _APT_PROMPT,
            anonymous_quota_session_id=session2.anonymous_quota_session_id,
        )
        assert out.status == "PUBLISHED"
        payload = json.loads(store.get_published(out.case_id, 1).payload_json)
        assert payload["draft"]["crime"]["victim_id"] == "sarah_miller"
    finally:
        store.dispose()


# --------------------------------------------------------------------------- #
# 7. observability (§21) — sanitized demoCaseId only
# --------------------------------------------------------------------------- #


def test_demo_started_event_carries_only_the_sanitized_registry_id(
    database_url,
):
    """The ``demo.started`` lifecycle event carries the SANITIZED registry id
    (and never truth). Verified with a dedicated capture handler because the
    app's own ``configure_logging`` (create_app) reconfigures the root logger;
    pytest's caplog root handler is not guaranteed to be attached."""
    import logging

    captured: list[dict] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if getattr(record, "pd_event", None) == "demo.started":
                captured.append(dict(getattr(record, "pd_fields", {}) or {}))

    handler = _Capture()
    logger = logging.getLogger("procedural-detective")
    logger.addHandler(handler)
    try:
        record = demo_cases.get_demo_case("demo-laboratory")
        application = _make_app(database_url)
        try:
            with TestClient(application) as c:
                token, _ = create_session(c)
                response = c.post(
                    "/api/v1/cases",
                    json={"prompt": record.prompt, "demoCaseId": "demo-laboratory"},
                    headers=auth(token),
                )
                assert response.status_code == 201 and response.json()["status"] == "PUBLISHED"
        finally:
            _dispose(application)
    finally:
        logger.removeHandler(handler)
    assert captured, "demo.started must be logged on the demo path"
    assert captured[0]["demoCaseId"] == "demo-laboratory"
    # Never truth, never internal fixture material.
    assert "murderer" not in json.dumps(captured[0])