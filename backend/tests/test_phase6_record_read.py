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
      exact kind-allowlisted ``content`` key set (email; default {} for
      kinds without an allowlist)
- project_read_content: every kind's allowlist is applied exactly, absent
  fields are omitted (never fabricated), and no raw proposition material can
  ever leak into ``content``
- openedAt is ISO-8601 UTC and stable across repeat reads
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
EMAIL_CONTENT_KEYS = {"fromPersonId", "toPersonIds", "subject", "body", "timestamp"}
GOLDEN_EMAIL_BODY = (
    "Sarah, I reviewed the accounts again. I think we need to talk "
    "tonight before the board meeting, in person. Please do not involve "
    "the auditors until then. -Thomas"
)


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
    # "forensic" has no content allowlist -> empty mapping.
    assert body["content"] == {}
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
    # The email content is EXACTLY the documented allowlist.
    assert set(body["content"].keys()) == EMAIL_CONTENT_KEYS
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


def _payload_with(presentation: dict, kind: str) -> dict:
    return {
        "draft": {
            "evidence": [
                {
                    "id": "ev_kind_probe",
                    "kind": kind,
                    "reliability": "high",
                    "presentation": presentation,
                    "propositions": [
                        {"type": "OTHER", "structured": {"SECRET_SOLVER_ONLY": 1}}
                    ],
                }
            ]
        }
    }


def test_15_content_allowlist_exact_keys_per_kind():
    """Every kind may emit ONLY its documented keys; absent fields are omitted
    — never fabricated from propositions or anything else."""
    cases = [
        (
            "object",
            {"subtype": "sharp_weapon", "locationId": "kitchen", "extra": "X"},
            {"subtype": "sharp_weapon", "locationId": "kitchen"},
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
                "events": [
                    {
                        "time": "2026-09-11T21:38:12+02:00",
                        "personId": "thomas_reed",
                        "action": "enter",
                    }
                ],
                "cameraId": "hall_cam_01",
            },
        ),
        (
            "witness_statement",
            {"speakerName": "Emily Reed", "statement": "I heard shouting.", "honesty": 0},
            {"speakerName": "Emily Reed", "statement": "I heard shouting."},
        ),
        (
            "forensic",  # no allowlist -> default {}
            {"title": "t", "description": "d"},
            {},
        ),
    ]
    for kind, presentation, expected in cases:
        payload = _payload_with(presentation, kind)
        content = project_read_content(payload, "ev_kind_probe")
        assert content == expected, kind
        # nothing solver-side ever leaks into content
        text = json.dumps(content)
        assert "SECRET_SOLVER_ONLY" not in text
        assert "internal_note" not in text
        assert "suspicious_flag_private" not in text
        assert "honesty" not in text


def test_15_absent_fields_are_omitted_not_fabricated():
    payload = _payload_with({"title": "Only title"}, "email")
    assert project_read_content(payload, "ev_kind_probe") == {}


def test_unknown_evidence_projects_empty_content():
    assert project_read_content({"draft": {"evidence": []}}, "missing") == {}