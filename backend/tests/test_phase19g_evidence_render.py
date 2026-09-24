"""Phase 19G — RICH EVIDENCE RENDERING & PLAYER-READABLE TIME CLUES (backend).

Covers the backend contract of Phase19G for the FROZEN closed render model:

- closed render model: every kind maps to exactly one of the closed
  ``EvidenceRenderType`` values (never an arbitrary/LLM renderer name), and
  unknown kinds degrade to GENERIC_TEXT (Phase19G §3/§8);
- golden + driver worlds: the WHEN/activity evidence renders a concrete
  player-visible time payload (Phase19G §2/§6/§7) instead of only
  "around the locked time" — ACTIVITY_LOG ``entries`` (chronological, with the
  concrete time + player-safe text), TIMELINE for the time-bearing witness
  observations, MESSAGE for email, FORENSIC_COMPARISON with the comparison
  result, BODY_OBSERVATION for statements;
- WHEN GUARANTEE across EASY (golden apartment) / MEDIUM (driver hotel_suite) /
  HARD (driver office) — every WHEN-critical evidence id yields a payload with
  at least one concrete visible time (Phase19G §6);
- determinism — same published case + evidence => byte-identical renderType /
  entries / ordering / timestamps / text on every read and reload (§10);
- 0 provider calls — the projection path makes no LLM/provider calls (§9/§19);
- security — pre-discovery 403 boundary, no truth/solver/proc/survivor/render
  ids in any payload field, hostile render metadata from non-allowlisted keys
  dropped (§5/§11/§18).

The phase 6 record-read contract tests (test_phase6_record_read.py) were
updated to the SAME frozen envelope — this module is the Phase 19G-specific
regression suite.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.render import (  # noqa: E402
    EvidenceRenderType,
    KIND_TO_RENDER_TYPE,
    render_payload_of,
    render_type_for_kind,
)
from app.domain.time_interval import parse_iso8601_to_epoch  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from app.services.publication import (  # noqa: E402
    project_read_content,
    serialize_published_payload,
)
from phase5_helpers import assert_no_hidden_leaks  # noqa: E402
from phase6_helpers import (  # noqa: E402
    EMAIL_EVIDENCE,
    KNIFE_EVIDENCE,
    KNIFE_OBJECT,
    LAPTOP_OBJECT,
    case_for,
    interact,
    playthrough,
    read_record,
)
from test_ollama_driver import (  # noqa: E402
    _case_people,
    _evidence,
    _j,
    _run,
    _staged,
    _world,
)

CLOSED_RENDER_TYPES = frozenset(render_type.value for render_type in EvidenceRenderType)

# Golden (EASY, dev-mode published) evidence ids used by the assertions.
GOLDEN_EMAIL = "email_thomas_01"
GOLDEN_CCTV = "cctv_thomas_scene_01"  # "Kitchen CCTV shows Thomas at 22:16:40"
GOLDEN_FORENSIC = "forensic_knife_match_01"
GOLDEN_STATEMENT = "alibi_claim_thomas_01"  # suspect_statement (no time anchors)
GOLDEN_WITNESS_OBSERVATION = "last_seen_01"  # "Neighbour saw Sarah alive at 22:15"

GOLDEN_KIND_BY_ID = {
    GOLDEN_EMAIL: "email",
    GOLDEN_CCTV: "cctv_observation",
    GOLDEN_FORENSIC: "forensic",
    GOLDEN_STATEMENT: "suspect_statement",
    GOLDEN_WITNESS_OBSERVATION: "witness_observation",
}

# Strings that must NEVER appear in any projected content payload (they are
# hidden/solver/internal/proc/render material — Phase19G §5/§11/§18). The
# canonical crime time VALUE is verified separately per world.
FORBIDDEN_PAYLOAD_TOKENS = (
    "d_ev_",
    "proc.",
    "solverProof",
    "observedAt",
    "observed_at",
    "propositions",
    "sourceRef",
    "murdererId",
    "victimId",
    "weaponId",
    "crimeTime",
    "canonical",
    "generationAttemptId",
    "uncertaintySeconds",
    "reliability",
    "survivor",
    "truth",
)

EMAIL_ALLOWLIST = frozenset(
    {"fromPersonId", "toPersonIds", "subject", "body", "timestamp"}
)


# --------------------------------------------------------------------------- #
# payload + world helpers
# --------------------------------------------------------------------------- #


def _golden_payload(phase5_app, case_id: str, version: int = 1) -> dict:
    row = phase5_app.state.store.get_published(case_id, version)
    assert row is not None
    return json.loads(row.payload_json)


def _when_critical_ids(payload: dict) -> tuple[str, ...]:
    """The published WHEN-critical evidence ids (solver proof time section)."""
    proof = payload.get("solverProof") or {}
    when = proof.get("time") or {}
    return tuple(str(i) for i in (when.get("critical_evidence_ids") or ()))


def _canonical_crime_time(payload: dict) -> str:
    return str(payload["truth"]["crime"]["crime_time"]["canonical"])


def _times_of(content: dict) -> list[str]:
    """Every CONCRETE player-visible time string in a content payload
    (entries[].time / timestamp / events[].time)."""
    times: list[str] = []
    for entry in content.get("entries") or ():
        if isinstance(entry, dict) and isinstance(entry.get("time"), str) and entry["time"]:
            times.append(entry["time"])
    if isinstance(content.get("timestamp"), str) and content["timestamp"]:
        times.append(content["timestamp"])
    for event in content.get("events") or ():
        if isinstance(event, dict) and isinstance(event.get("time"), str) and event["time"]:
            times.append(event["time"])
    return times


def _has_concrete_time(content: dict) -> bool:
    """Phase19G §6: a concrete temporal value is PRESENT (parseable ISO time)."""
    for raw in _times_of(content):
        try:
            parse_iso8601_to_epoch(raw)
        except ValueError:
            continue
        return True
    return False


def _entries_chronological(content: dict) -> bool:
    """The ACTIVITY_LOG/TIMELINE entries are sorted by time (non-decreasing)."""
    entries = content.get("entries") or ()
    epochs: list[int] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("time"), str):
            return False
        try:
            epochs.append(parse_iso8601_to_epoch(entry["time"]))
        except ValueError:
            return False
    return epochs == sorted(epochs)


def _medium_payload() -> dict:
    """MEDIUM driver world (hotel_suite, kitchen knife, canonical 21:18).

    Mirrors test_phase19c_when_derivability._medium_hotel_payload (mocked
    transport — hermetic, zero network).
    """
    cp = _case_people(weapon="kitchen_knife")
    cp["crime"]["crimeTime"] = {
        "canonical": "2026-09-11T21:18:00+02:00",
        "accusationToleranceSeconds": 300,
    }
    posts = [
        _j(cp),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        _j(_world("kitchen knife")),
    ]
    prompt = (
        "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research data\n"
        "Weapon: kitchen knife\nTime: 21:18\nWitness: Lisa König\nLocation: hotel suite\n"
    )
    record, _transport = _run(posts, prompt=prompt)
    assert record.state is GenerationState.PUBLISHED
    return json.loads(
        serialize_published_payload(
            record.published, seed=1, prompt="medium", model="mock", title="Medium"
        )
    )


def _hard_payload() -> dict:
    """HARD driver world (office, locked ice pick, canonical 23:42)."""
    record, _transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    return json.loads(
        serialize_published_payload(
            record.published, seed=1, prompt="hard", model="mock", title="Hard"
        )
    )


# --------------------------------------------------------------------------- #
# 1 — closed render model
# --------------------------------------------------------------------------- #


def test_closed_render_model_values_are_the_frozen_enum():
    """The render types are exactly the closed Phase19G vocabulary §3."""
    assert CLOSED_RENDER_TYPES == frozenset(
        {
            "GENERIC_TEXT",
            "ACTIVITY_LOG",
            "FORENSIC_COMPARISON",
            "MESSAGE",
            "DOCUMENT",
            "BODY_OBSERVATION",
            "TIMELINE",
        }
    )
    assert set(KIND_TO_RENDER_TYPE.values()) <= set(EvidenceRenderType)


def test_render_type_is_always_derived_never_raw_kind():
    """Unknown/crafted kinds NEVER leak their raw kind string as a renderer
    name — everything falls back to the closed GENERIC_TEXT value."""
    for handled_kind in (
        "cctv",
        "cctv_observation",
        "view_record",
        "email",
        "document",
        "digital",
        "financial",
        "forensic",
        "physical",
        "testimonial",
        "witness_statement",
        "statement",
        "suspect_statement",
        "witness_observation",
        "object",
    ):
        assert render_type_for_kind(handled_kind) in CLOSED_RENDER_TYPES
    for hostile_kind in ("mystery", "", "<script>alert(1)</script>", "MESSAGE", "ACTIVITY_LOG"):
        assert render_type_for_kind(hostile_kind) == "GENERIC_TEXT"


# --------------------------------------------------------------------------- #
# 2 — golden world payloads (EASY)
# --------------------------------------------------------------------------- #


def test_golden_laptop_read_renders_message_payload(phase5_app):
    """The golden Laptop read (the reproduced flow) renders the MESSAGE payload
    with the allowlisted email content — the player sees the actual clue."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator, version=1)
    res = interact(phase5_app, pt_id, pt_token, LAPTOP_OBJECT, "read")
    assert res.status_code == 200, res.text
    res = read_record(phase5_app, pt_id, pt_token, EMAIL_EVIDENCE)
    assert res.status_code == 200, res.text
    body = res.json()
    content = body["content"]
    assert content["renderType"] == "MESSAGE"
    assert EMAIL_ALLOWLIST <= set(content.keys())
    assert content["fromPersonId"] == "thomas_reed"
    assert content["toPersonIds"] == ["sarah_miller"]
    assert content["subject"] == "We need to talk tonight"
    assert content["timestamp"] == "2026-09-11T21:04:00+02:00"
    assert _has_concrete_time(content)  # the readable timestamp is a time clue
    assert_no_hidden_leaks(body)


def test_golden_activity_evidence_renders_activity_log_with_concrete_times(phase5_app):
    """Golden activity evidence (Kitchen CCTV observation) -> ACTIVITY_LOG:
    >=1 concrete time, chronological entries, {time, text} shape only."""
    case_id, creator = case_for(phase5_app)
    payload = _golden_payload(phase5_app, case_id)
    content = project_read_content(payload, GOLDEN_CCTV)
    assert content["renderType"] == "ACTIVITY_LOG"
    entries = content["entries"]
    assert entries, "activity evidence must expose concrete log entries"
    assert _has_concrete_time(content)
    assert _entries_chronological(content)
    for entry in entries:
        assert set(entry) == {"time", "text"}


def test_golden_when_observation_renders_timeline(phase5_app):
    """Golden time-bearing witness observation (last seen alive) -> TIMELINE
    with a concrete player-visible time (the deduced WHEN clue)."""
    case_id, creator = case_for(phase5_app)
    payload = _golden_payload(phase5_app, case_id)
    content = project_read_content(payload, GOLDEN_WITNESS_OBSERVATION)
    assert content["renderType"] == "TIMELINE"
    assert content["entries"]
    assert _has_concrete_time(content)
    assert _entries_chronological(content)


def test_golden_statement_renders_body_observation(phase5_app):
    """Golden suspect statement -> BODY_OBSERVATION (safe structured panel)."""
    case_id, creator = case_for(phase5_app)
    payload = _golden_payload(phase5_app, case_id)
    content = project_read_content(payload, GOLDEN_STATEMENT)
    assert content["renderType"] == "BODY_OBSERVATION"
    assert content["summary"]  # safe title/description-level text present


def test_golden_forensic_renders_comparison_result(phase5_app):
    """Golden forensic -> FORENSIC_COMPARISON with the player-visible result."""
    case_id, creator = case_for(phase5_app)
    payload = _golden_payload(phase5_app, case_id)
    content = project_read_content(payload, GOLDEN_FORENSIC)
    assert content["renderType"] == "FORENSIC_COMPARISON"
    assert content["comparison"] == "Blood on the kitchen knife matches the victim"


# --------------------------------------------------------------------------- #
# 3 — WHEN GUARANTEE across EASY / MEDIUM / HARD
# --------------------------------------------------------------------------- #


def test_when_invariant_easy_medium_hard(phase5_app):
    """Phase19G §6: EVERY published world's WHEN evidence yields a payload with
    at least one concrete visible time — never only 'around the locked time'."""
    worlds: list[tuple[str, dict]] = []
    case_id, creator = case_for(phase5_app)
    worlds.append(("EASY (golden apartment)", _golden_payload(phase5_app, case_id)))
    worlds.append(("MEDIUM (driver hotel_suite)", _medium_payload()))
    worlds.append(("HARD (driver office)", _hard_payload()))

    for world_name, payload in worlds:
        critical = _when_critical_ids(payload)
        assert critical, f"{world_name}: payload carries no WHEN-critical ids"
        for eid in critical:
            content = project_read_content(payload, eid)
            assert _has_concrete_time(content), (
                f"{world_name}: WHEN evidence {eid!r} has no concrete visible time"
            )


def test_driver_when_facts_render_concrete_time_payloads():
    """The driver d_ev_when_* canonical facts: d_ev_when_obs (the laptop
    activity record) -> ACTIVITY_LOG with synthetic entries built from the
    allowlisted observed-at anchor + presentation text; the witness
    observations -> TIMELINE with concrete times."""
    for payload in (_medium_payload(), _hard_payload()):
        obs = project_read_content(payload, "d_ev_when_obs")
        assert obs["renderType"] == "ACTIVITY_LOG"
        assert obs["entries"], "d_ev_when_obs must expose concrete log entries"
        assert obs["entries"][0]["text"] == "Activity logged at the scene"
        assert _has_concrete_time(obs)
        assert _entries_chronological(obs)

        last_seen = project_read_content(payload, "d_ev_when_last_seen")
        assert last_seen["renderType"] == "TIMELINE"
        assert _has_concrete_time(last_seen)

        body_found = project_read_content(payload, "d_ev_when_body")
        assert body_found["renderType"] == "TIMELINE"
        assert _has_concrete_time(body_found)


# --------------------------------------------------------------------------- #
# 4 — event -> entry projection (ordering, dedupe, allowlist)
# --------------------------------------------------------------------------- #


def _fact_with(kind: str, presentation: dict, propositions: list | None = None):
    return {
        "id": "ev_kind_probe",
        "kind": kind,
        "reliability": "high",
        "presentation": presentation,
        "propositions": propositions if propositions is not None else [],
    }


def test_event_entries_are_chronological_and_deduped():
    """Events drive the entries when present: chronological ordering (the
    allowlisted event times) and exact (time, text) deduplication."""
    fact = _fact_with(
        "cctv",
        {
            "title": "Activity log",
            "events": [
                # deliberately out of order
                {"time": "2026-09-11T22:11:00+02:00", "personId": "thomas_reed", "action": "User login detected"},
                {"time": "2026-09-11T22:17:00+02:00", "personId": "thomas_reed", "action": "Activity recorded at the scene"},
                {"time": "2026-09-11T22:14:00+02:00", "personId": "thomas_reed", "action": "Research file opened"},
                {"time": "2026-09-11T22:17:00+02:00", "personId": "thomas_reed", "action": "Activity recorded at the scene"},  # duplicate
            ],
        },
    )
    content = project_read_content({"draft": {"evidence": [fact]}}, "ev_kind_probe")
    assert content["renderType"] == "ACTIVITY_LOG"
    entries = content["entries"]
    assert [e["text"] for e in entries] == [
        "User login detected",
        "Research file opened",
        "Activity recorded at the scene",
    ]
    assert _entries_chronological(content)
    # the raw allowlisted events stay available untouched.
    assert len(content["events"]) == 4


def test_observed_at_synthesis_guarantees_concrete_time_without_events():
    """A time-bearing record WITHOUT events (the 'around the locked time'
    audit repro) deterministically synthesizes a concrete entry from the
    allowlisted observed-at anchor + presentation text."""
    fact = _fact_with(
        "cctv",
        {
            "title": "Activity logged at the scene",
            "description": "A monitoring log records activity at the scene around the locked time.",
        },
        propositions=[
            {
                "type": "CRIME_SCENE_OBSERVATION_AT",
                "observed_at": "2026-09-11T22:16:50+02:00",
                "uncertainty_seconds": 90,
                "structured": {"SURVIVOR_MARKER": "never"},
            }
        ],
    )
    content = render_payload_of(fact)
    assert content["renderType"] == "ACTIVITY_LOG"
    assert content["entries"] == [
        {"time": "2026-09-11T22:16:50+02:00", "text": "Activity logged at the scene"}
    ]
    # the proposition object (and its structured junk) never leaks.
    blob = json.dumps(content)
    assert "SURVIVOR_MARKER" not in blob
    assert "uncertainty_seconds" not in blob
    assert "structured" not in blob


def test_generic_text_fallback_never_breaks_the_ui():
    """Unknown valid kind -> GENERIC_TEXT with the safe title/description-level
    text ONLY (a non-empty dict, never a crash)."""
    fact = _fact_with(
        "mystery_kind",
        {"title": "A strange note", "description": "The note is hard to read."},
    )
    content = render_payload_of(fact)
    assert content == {"renderType": "GENERIC_TEXT", "summary": "The note is hard to read."}


# --------------------------------------------------------------------------- #
# 5 — determinism (§10)
# --------------------------------------------------------------------------- #


def _all_content(payload: dict) -> dict[str, dict]:
    return {fact["id"]: project_read_content(payload, fact["id"]) for fact in payload["draft"]["evidence"]}


def test_projection_is_byte_identical_across_reads_and_reload(phase5_app):
    """Same published case + same evidence => byte-identical renderType /
    entries / ordering / timestamps / text on every read AND after a reload
    (a separately deserialized copy of the frozen payload)."""
    case_id, creator = case_for(phase5_app)
    payload = _golden_payload(phase5_app, case_id)
    first = _all_content(payload)
    second = _all_content(payload)
    reloaded = json.loads(json.dumps(payload))  # simulate a fresh DB read
    third = _all_content(reloaded)
    assert first == second == third
    for eid, content in first.items():
        assert json.dumps(content, sort_keys=True) == json.dumps(
            second[eid], sort_keys=True
        )


def test_read_record_twice_is_byte_identical(phase5_app):
    """HTTP read_record twice => byte-identical response bodies (the persisted
    openedAt + the deterministic projection)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator, version=1)
    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert res.status_code == 200, res.text
    first = read_record(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
    second = read_record(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
    assert first.status_code == 200 and second.status_code == 200
    assert first.text == second.text


# --------------------------------------------------------------------------- #
# 6 — zero provider calls (§9/§19)
# --------------------------------------------------------------------------- #


def test_projection_path_makes_zero_provider_calls():
    """The render projection is PURE: after a driver-world publication, reading
    EVERY evidence through the projection makes ZERO additional provider/LLM
    calls (transport call_count unchanged)."""
    record, transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    payload = json.loads(
        serialize_published_payload(
            record.published, seed=1, prompt="guard", model="mock", title="Guard"
        )
    )
    calls_after_generation = transport.call_count
    assert calls_after_generation > 0
    for fact in payload["draft"]["evidence"]:
        _ = project_read_content(payload, fact["id"])
        _ = render_payload_of(fact)
    assert transport.call_count == calls_after_generation


# --------------------------------------------------------------------------- #
# 7 — security boundary (§5/§18)
# --------------------------------------------------------------------------- #


def test_prediscovery_read_still_403_with_zero_content(phase5_app):
    """Before discovery the browser receives NOTHING: 403 EVIDENCE_NOT_DISCOVERED
    with a generic envelope — no id, no title, no description, no render
    metadata, no time clues."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator, version=1)
    res = read_record(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
    assert res.status_code == 403
    body = res.json()
    assert body["error"]["code"] == "EVIDENCE_NOT_DISCOVERED"
    assert set(body.keys()) == {"error"}
    for token in ("renderType", "summary", "entries", "comparison", "Blood", "Timestam"):
        assert token not in res.text


def test_no_hidden_material_in_any_golden_payload_field(phase5_app):
    """Every golden evidence payload carries NO internal ids, no truth/solver/
    proc/survivor/render material and never the canonical crime time value."""
    case_id, creator = case_for(phase5_app)
    payload = _golden_payload(phase5_app, case_id)
    canonical = _canonical_crime_time(payload)
    for fact in payload["draft"]["evidence"]:
        content = project_read_content(payload, fact["id"])
        blob = json.dumps(content, sort_keys=True)
        for token in FORBIDDEN_PAYLOAD_TOKENS:
            assert token not in blob, (fact["id"], token)
        assert canonical not in blob, fact["id"]
        for raw_time in _times_of(content):
            assert raw_time != canonical, fact["id"]
        assert_no_hidden_leaks({"content": content})


def test_no_hidden_material_in_driver_payloads():
    """The same scan holds for the driver Medium/Hard worlds (d_ev_* ids,
    proc.* asset tokens and survivor material never reach a payload field)."""
    for payload in (_medium_payload(), _hard_payload()):
        for fact in payload["draft"]["evidence"]:
            content = project_read_content(payload, fact["id"])
            blob = json.dumps(content, sort_keys=True)
            for token in FORBIDDEN_PAYLOAD_TOKENS:
                assert token not in blob, (fact["id"], token)
            assert _canonical_crime_time(payload) not in blob
            assert_no_hidden_leaks({"content": content})


def test_hostile_render_metadata_from_non_allowlisted_keys_dropped():
    """A hostile presentation carrying a script-shaped renderType / junk keys /
    secret proposition material is IGNORED: the render type is derived from the
    kind, only allowlisted keys survive, and nothing executable/junk leaks."""
    fact = _fact_with(
        "cctv",
        {
            "title": "Activity log",
            "description": "A log.",
            "renderType": "<script>alert(1)</script>",
            "component": "DynamicComponent<constructor>",
            "templateurl": "https://evil.example/t",
            "secrets": "s3cret-token",
            "observedAt": "2026-09-11T21:00:00+02:00",
            "events": [
                {"time": "2026-09-11T21:38:12+02:00", "personId": "thomas_reed", "action": "enter"}
            ],
        },
        propositions=[
            {
                "type": "OTHER",
                "observed_at": "2026-09-11T22:00:00+02:00",
                "structured": {"survivorSet": ["paul_becker"]},
            }
        ],
    )
    content = project_read_content({"draft": {"evidence": [fact]}}, "ev_kind_probe")
    assert content["renderType"] == "ACTIVITY_LOG"  # derived, never hostile
    assert set(content.keys()) == {"renderType", "summary", "events", "entries"}
    blob = json.dumps(content)
    for junk in (
        "<script",
        "DynamicComponent",
        "templateurl",
        "s3cret",
        "evil.example",
        "survivorSet",
        "paul_becker",
        "observedAt",
        "observed_at",
        "structured",
    ):
        assert junk not in blob


def test_entries_text_is_inert_plain_data():
    """Log entry texts are plain strings (never rendered as anything but text
    by the frontend); the backend emits them as inert JSON data."""
    fact = _fact_with(
        "cctv",
        {
            "title": "Log",
            "events": [
                {"time": "2026-09-11T22:00:00+02:00", "personId": "x", "action": "<img src=x onerror=alert(1)>"}
            ],
        },
    )
    content = render_payload_of(fact)
    text = content["entries"][0]["text"]
    assert "<img" in text  # data is NEITHER stripped NOR executed — just inert text
    assert isinstance(text, str)


__all__ = []  # pytest module: no accidental public names