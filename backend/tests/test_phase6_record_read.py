"""Phase 6 — record read/inspect (REQUIREMENTS 40.9, Phase6 B/H; Phase6 O items
4/7/8/10/15 + allowlist + openedAt semantics).

- O4  undiscovered evidence cannot be read (403 EVIDENCE_NOT_DISCOVERED, no
      content in the response)
- O7  read after discover succeeds
- O8  duplicate read is idempotent (byte-identical DTO)
- O10 evidence from ANOTHER CaseVersion cannot be read (v1 pt + v2-only id
      -> 404)
- O11 v1 read unchanged after v2 publish
- O15 read DTO contains only the allowed fields: exact top-level key set AND
      exact render-envelope ``content`` key set (Phase 19G: ``renderType`` +
      ``summary`` + type-specific fields + the exact kind-allowlisted keys;
      a known kind WITHOUT readable fields still carries the closed
      ``renderType`` + safe ``summary`` — never breaks the UI)
- project_read_content: every kind's allowlist is applied exactly (the render
      envelope only ever carries allowlisted keys on top of the closed
      metadata), absent fields are omitted (never fabricated), and no raw
      proposition material can ever leak into ``content``
- openedAt is ISO-8601 UTC and stable across repeat reads

Phase 19G contract note: the content key sets below are the FROZEN Phase 19G
contract (Rich Evidence Rendering) — ``renderType`` is always the closed
derived enum value, ``summary`` is the safe title/description-level text, and
the Phase-6 allowlisted keys are unchanged on top of them.
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase5_helpers import (
    assert_no_hidden_leaks,
    assert_sanitized_error,
)
from phase6_helpers import (
    EMAIL_EVIDENCE,
    KNIFE_EVIDENCE,
    KNIFE_OBJECT,
    V2_ONLY_EVIDENCE,
    case_for,
    interact,
    playthrough,
    publish_v2_with_extra_evidence,
    read_record,
)
from app.services.publication import project_read_content

READ_KEYS = {
    "evidenceId",
    "kind",
    "title",
    "description",
    "openedAt",
    "readByPlayer",
    "content",
}
# Phase 19G frozen render-envelope for the golden email: closed renderType +
# summary + the exact Phase-6 allowlisted email keys.
EMAIL_CONTENT_KEYS = {
    "renderType",
    "summary",
    "fromPersonId",
    "toPersonIds",
    "subject",
    "body",
    "timestamp",
}
GOLDEN_EMAIL_BODY = (
    "Sarah, I reviewed the accounts again. I think we need to talk "
    "tonight before the board meeting, in person. Please do not involve "
    "the auditors until then. -Thomas"
)
GOLDEN_EMAIL_SUMMARY = "A short email Thomas sent the evening before the murder."
GOLDEN_KNIFE_CONTENT = {
    "renderType": "FORENSIC_COMPARISON",
    "summary": "Structured evidence fact.",
    "comparison": "Blood on the kitchen knife matches the victim",
}


def test_4_undiscovered_cannot_be_read(phase5_app):
    """O4: a record that exists but is undiscovered answers
    403 EVIDENCE_NOT_DISCOVERED with a generic envelope — zero content."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    res = read_record(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
    assert res.status_code == 403
    body = res.json()
    assert body["error"]["code"] == "EVIDENCE_NOT_DISCOVERED"
    assert_sanitized_error(res.text)
    # No evidence content whatsoever in the error envelope.
    text_lower = res.text.lower()
    assert "blood" not in text_lower
    assert "propositions" not in text_lower
    assert set(body.keys()) == {"error"}
    # Reading does NOT mark anything read.
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.read == ()
    assert snap.discovered == ()


def test_7_read_after_discover_succeeds(phase5_app):
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert res.status_code == 200
    res = read_record(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == READ_KEYS
    assert body["evidenceId"] == KNIFE_EVIDENCE
    assert body["kind"] == "forensic"
    assert body["readByPlayer"] is True
    # Phase 19G: forensic evidence renders the closed FORENSIC_COMPARISON
    # envelope (renderType + safe summary + the player-visible comparison).
    assert body["content"] == GOLDEN_KNIFE_CONTENT
    # Parsable ISO-8601 UTC.
    opened = datetime.datetime.fromisoformat(body["openedAt"])
    assert opened.tzinfo is not None and opened.utcoffset().total_seconds() == 0
    assert_no_hidden_leaks(body)
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.read == (KNIFE_EVIDENCE,)


def test_8_duplicate_read_is_idempotent(phase5_app):
    """O8: every repeat read returns the byte-identical DTO."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    first = read_record(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
    assert first.status_code == 200
    second = read_record(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
    assert second.status_code == 200
    assert second.json() == first.json()
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.read == (KNIFE_EVIDENCE,)


def test_10_read_from_another_caseversion_answers_404(phase5_app):
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    publish_v2_with_extra_evidence(phase5_app, case_id)
    # The v2-only id is unknown to the v1 playthrough -> 404.
    res = read_record(phase5_app, pt_id, pt_token, V2_ONLY_EVIDENCE)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
    # And it stays unread / undiscovered.
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.read == ()
    assert snap.discovered == ()


def test_11_v1_read_unchanged_after_v2_publish(phase5_app):
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    before = read_record(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE).json()
    publish_v2_with_extra_evidence(phase5_app, case_id)
    after = read_record(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
    assert after.status_code == 200
    assert after.json() == before


def test_15_read_dto_exact_keys_and_email_allowlist(phase5_app):
    """O15: read DTO exact key sets — top level AND per-kind content."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    # Discover the email via the laptop interaction, then read it.
    res = interact(phase5_app, pt_id, pt_token, "apartment_laptop", "read")
    assert res.status_code == 200
    assert res.json()["discovery"]["evidenceId"] == EMAIL_EVIDENCE
    res = read_record(phase5_app, pt_id, pt_token, EMAIL_EVIDENCE)
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == READ_KEYS
    assert body["evidenceId"] == EMAIL_EVIDENCE
    assert body["kind"] == "email"
    assert body["title"] == "Re: the missing funds"
    # The email content is EXACTLY the documented Phase 19G envelope: the
    # closed renderType + safe summary + the exact email allowlist.
    assert set(body["content"].keys()) == EMAIL_CONTENT_KEYS
    assert body["content"]["renderType"] == "MESSAGE"
    assert body["content"]["summary"] == GOLDEN_EMAIL_SUMMARY
    assert body["content"]["fromPersonId"] == "thomas_reed"
    assert body["content"]["toPersonIds"] == ["sarah_miller"]
    assert body["content"]["subject"] == "We need to talk tonight"
    assert body["content"]["body"] == GOLDEN_EMAIL_BODY
    assert body["content"]["timestamp"] == "2026-09-11T21:04:00+02:00"
    assert_no_hidden_leaks(body)


def test_read_unknown_record_answers_404(phase5_app):
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    res = read_record(phase5_app, pt_id, pt_token, "record_that_never_existed")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
    assert_sanitized_error(res.text)


# --------------------------------------------------------------------------- #
# project_read_content — exact allowlist per kind (pure projection unit tests)
# --------------------------------------------------------------------------- #


def _payload_with(presentation: dict, kind: str, *, observed_at: str | None = None) -> dict:
    proposition: dict = {"type": "OTHER", "structured": {"SECRET_SOLVER_ONLY": 1}}
    if observed_at is not None:
        proposition = {
            "type": "CRIME_SCENE_OBSERVATION_AT",
            "observed_at": observed_at,
            "structured": {"SECRET_SOLVER_ONLY": 1},
        }
    return {
        "draft": {
            "evidence": [
                {
                    "id": "ev_kind_probe",
                    "kind": kind,
                    "reliability": "high",
                    "presentation": presentation,
                    "propositions": [proposition],
                }
            ]
        }
    }


def test_15_content_allowlist_exact_keys_per_kind():
    """Every kind may emit ONLY its documented keys PLUS the closed Phase 19G
    envelope keys (renderType + summary + type-specific fields); absent fields
    are omitted — never fabricated from propositions or anything else."""
    cases = [
        (
            "object",
            {"subtype": "sharp_weapon", "locationId": "kitchen", "extra": "X"},
            {
                "renderType": "GENERIC_TEXT",
                "summary": "",
                "subtype": "sharp_weapon",
                "locationId": "kitchen",
            },
        ),
        (
            "email",
            {
                "fromPersonId": "a",
                "toPersonIds": ["b"],
                "subject": "s",
                "body": "b",
                "timestamp": "2026-09-11T21:00:00+02:00",
            },
            {
                "renderType": "MESSAGE",
                "summary": "",
                "fromPersonId": "a",
                "toPersonIds": ["b"],
                "subject": "s",
                "body": "b",
                "timestamp": "2026-09-11T21:00:00+02:00",
            },
        ),
        (
            "financial",
            {
                "rows": [
                    {
                        "date": "2026-04-03",
                        "from": "A",
                        "to": "B",
                        "amount": 80000,
                        "currency": "EUR",
                        "description": "consulting",
                        "suspicious_flag_private": True,
                    }
                ],
                "suspicious": False,
            },
            {
                "renderType": "DOCUMENT",
                "summary": "",
                "rows": [
                    {
                        "date": "2026-04-03",
                        "from": "A",
                        "to": "B",
                        "amount": 80000,
                        "currency": "EUR",
                        "description": "consulting",
                    }
                ],
                "suspicious": False,
            },
        ),
        (
            "cctv_observation",
            {
                "events": [
                    {
                        "time": "2026-09-11T21:38:12+02:00",
                        "personId": "thomas_reed",
                        "action": "enter",
                        "internal_note": "hidden",
                    }
                ],
                "cameraId": "hall_cam_01",
            },
            {
                "renderType": "ACTIVITY_LOG",
                "summary": "",
                "events": [
                    {
                        "time": "2026-09-11T21:38:12+02:00",
                        "personId": "thomas_reed",
                        "action": "enter",
                    }
                ],
                "cameraId": "hall_cam_01",
                # Phase 19G: the concrete, chronologically-ordered log entries
                # projected from the ALLOWLISTED events (time + action).
                "entries": [
                    {"time": "2026-09-11T21:38:12+02:00", "text": "enter"}
                ],
            },
        ),
        (
            "witness_statement",
            {"speakerName": "Emily Reed", "statement": "I heard shouting.", "honesty": 0},
            {
                "renderType": "BODY_OBSERVATION",
                "summary": "",
                "speakerName": "Emily Reed",
                "statement": "I heard shouting.",
            },
        ),
        (
            # Kind with NO allowlist: still renders the closed envelope with
            # the player-safe forensic comparison result (Phase 19G §8).
            "forensic",
            {
                "title": "Forensic comparison",
                "description": "The object matches the wound pattern.",
            },
            {
                "renderType": "FORENSIC_COMPARISON",
                "summary": "The object matches the wound pattern.",
                "comparison": "The object matches the wound pattern.",
            },
        ),
        (
            # Time-bearing evidence WITHOUT events: the deterministic
            # observed-at synthesis guarantees a concrete player-visible time.
            "cctv",
            {"title": "Activity logged at the scene", "description": "log"},
            {
                "renderType": "ACTIVITY_LOG",
                "summary": "log",
                "entries": [{"time": "2026-09-11T22:16:50+02:00", "text": "Activity logged at the scene"}],
            },
        ),
    ]
    for kind, presentation, expected in cases:
        payload = _payload_with(presentation, kind, observed_at="2026-09-11T22:16:50+02:00" if kind == "cctv" else None)
        content = project_read_content(payload, "ev_kind_probe")
        assert content == expected, kind
        # nothing solver-side ever leaks into content
        text = json.dumps(content)
        assert "SECRET_SOLVER_ONLY" not in text
        assert "internal_note" not in text
        assert "suspicious_flag_private" not in text
        assert "honesty" not in text


def test_15_absent_fields_are_omitted_not_fabricated():
    # No allowlisted readable fields -> NO fabricated keys; the closed Phase 19G
    # envelope still carries only renderType + the safe title text.
    payload = _payload_with({"title": "Only title"}, "email")
    assert project_read_content(payload, "ev_kind_probe") == {
        "renderType": "MESSAGE",
        "summary": "Only title",
    }


def test_unknown_evidence_projects_empty_content():
    assert project_read_content({"draft": {"evidence": []}}, "missing") == {}