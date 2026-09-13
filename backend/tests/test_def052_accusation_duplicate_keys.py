"""DEF-052 — the accusation body rejects duplicate/ambiguous JSON keys
(Phase7 B "Reject: duplicate/ambiguous fields").

Repro: a wire body containing TWO ``murdererId`` keys (or any frozen field)
was accepted with 200 and the persisted value depended on client key ORDER
(json.loads keeps the LAST value) — an order-flip silently changed the
winner. Phase7 B requires rejecting duplicate/ambiguous fields.

Fix (PRODUCTION): the accusation route reads the CACHED raw request bytes
and scans the whole JSON tree for repeated keys via an ``object_pairs_hook``
before the service is invoked; a duplicate answers the generic
``422 VALIDATION_ERROR`` envelope. The normal Pydantic model parse then
handles everything else (reusing the same cached body).

Locked here: every frozen field duplicated (both key orders) -> 422 with NO
state change (playthrough stays PLAYING, no accusation row) and the same
playthrough then accepts a CLEAN body with 200; the duplicate detector also
fires for nested duplicates anywhere in the tree.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.api.v1.playthroughs import reject_duplicate_json_keys

from phase5_helpers import assert_sanitized_error, auth
from phase6_helpers import client
from test_phase7_helpers import (
    create_published_case_and_playthrough,
    make_accusation,
    truth_bundle,
    winning_body,
)


def _raw_body_with_duplicate(fields, dupe_key, first_value, second_value) -> bytes:
    """Build a raw JSON body where ``dupe_key`` appears TWICE (first then
    second; also returned reversed) and every other field once."""
    parts = []
    for key, value in fields.items():
        if key == dupe_key:
            parts.append(f'"{key}": {json.dumps(first_value)}')
            parts.append(f'"{key}": {json.dumps(second_value)}')
        else:
            parts.append(f'"{key}": {json.dumps(value)}')
    return ("{" + ", ".join(parts) + "}").encode("utf-8")


@pytest.mark.parametrize(
    "dupe_key,value_a,value_b",
    [
        ("murdererId", "anna_karlsson", "thomas_reed"),
        ("motiveId", "revenge_for_affair", "cover_up_embezzlement"),
        ("weaponId", "letter_opener", "kitchen_knife"),
    ],
)
def test_duplicate_frozen_id_field_rejected_both_orders(
    phase5_app, dupe_key, value_a, value_b
):
    """A duplicated id field (both key orders) -> 422 VALIDATION_ERROR and NO
    state change; the same playthrough still accepts a clean accusation."""
    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id = bundle["playthroughId"]
    pt_token = bundle["playthroughToken"]
    truth = truth_bundle(phase5_app, bundle["caseId"], 1)
    fields = winning_body(truth)

    for first, second in ((value_a, value_b), (value_b, value_a)):
        raw = _raw_body_with_duplicate(fields, dupe_key, first, second)
        with client(phase5_app) as c:
            res = c.post(
                f"/api/v1/playthroughs/{pt_id}/accusation",
                content=raw,
                headers={"Content-Type": "application/json", **auth(pt_token)},
            )
        assert res.status_code == 422, res.text
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"
        assert_sanitized_error(res.text)
        # NO state change: the playthrough is still PLAYING and immutable
        # accusation row does NOT exist.
        assert phase5_app.state.store.get_playthrough_state(pt_id) == "PLAYING"
        assert phase5_app.state.store.get_accusation(pt_id) is None

    # A clean body on the SAME playthrough still works (200).
    with client(phase5_app) as c:
        res = make_accusation(c, pt_id, pt_token, winning_body(truth))
    assert res.status_code == 200, res.json()
    assert res.json()["status"] == "ACCUSED"


def test_duplicate_crimeTime_rejected_both_orders(phase5_app):
    """A duplicated crimeTime (both forms/orders) -> 422, no state change."""
    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id = bundle["playthroughId"]
    pt_token = bundle["playthroughToken"]
    truth = truth_bundle(phase5_app, bundle["caseId"], 1)
    fields = winning_body(truth)

    forms = ("22:17:00", truth["canonical"])
    for first, second in ((forms[0], forms[1]), (forms[1], forms[0])):
        raw = _raw_body_with_duplicate(fields, "crimeTime", first, second)
        with client(phase5_app) as c:
            res = c.post(
                f"/api/v1/playthroughs/{pt_id}/accusation",
                content=raw,
                headers={"Content-Type": "application/json", **auth(pt_token)},
            )
        assert res.status_code == 422, res.text
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"
        assert phase5_app.state.store.get_playthrough_state(pt_id) == "PLAYING"
        assert phase5_app.state.store.get_accusation(pt_id) is None


def test_duplicate_nested_key_rejected(phase5_app):
    """The duplicate scan covers the WHOLE tree (nested objects too)."""
    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id = bundle["playthroughId"]
    pt_token = bundle["playthroughToken"]
    truth = truth_bundle(phase5_app, bundle["caseId"], 1)
    fields = winning_body(truth)
    nested = (
        "{"
        + ", ".join(f'"{k}": {json.dumps(v)}' for k, v in fields.items())
        + ', "extra": {"z": 1, "z": 2}}'
    ).encode("utf-8")
    with client(phase5_app) as c:
        res = c.post(
            f"/api/v1/playthroughs/{pt_id}/accusation",
            content=nested,
            headers={"Content-Type": "application/json", **auth(pt_token)},
        )
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
    assert phase5_app.state.store.get_playthrough_state(pt_id) == "PLAYING"
    assert phase5_app.state.store.get_accusation(pt_id) is None


def test_reject_duplicate_json_keys_unit():
    """The wire scanner itself: clean -> True, duplicate -> False (any depth),
    malformed/empty JSON -> True (Pydantic answers 422 instead)."""
    assert reject_duplicate_json_keys(b"")
    assert reject_duplicate_json_keys(b'{"a": 1, "b": 2}')
    assert reject_duplicate_json_keys(b'{"a": {"b": 1}}')
    assert not reject_duplicate_json_keys(b'{"a": 1, "a": 2}')
    assert not reject_duplicate_json_keys(b'{"a": {"b": 1, "b": 2}}')
    assert not reject_duplicate_json_keys(
        b'{"murdererId": "anna_karlsson", "murdererId": "thomas_reed"}'
    )
    # Not valid JSON -> not our concern (the model parse answers the 422).
    assert reject_duplicate_json_keys(b"{not json")
    assert reject_duplicate_json_keys(b"[]")