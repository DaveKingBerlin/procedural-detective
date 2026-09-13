"""Shared Phase 6 test helpers (bootstrap/fixture drivers).

Reuses the Phase 5 API babysitters (phase5_helpers) and adds the Phase 6
scene constants + deterministic v2-publish helper (no provider call — a
second immutable published row whose evidence set carries a v2-only id).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from phase5_helpers import (
    auth,
    create_case,
    create_playthrough,
    create_session,
)

SCENE_LOCATION = "miller_apartment_kitchen"
SCENE_NAME = "Miller Apartment - Kitchen"
KNIFE_OBJECT = "kitchen_knife"
KNIFE_EVIDENCE = "forensic_knife_match_01"
LAPTOP_OBJECT = "apartment_laptop"
EMAIL_EVIDENCE = "email_thomas_01"
BODY_OBJECT = "victim_body_placeholder"
# A discoverable but placement-less evidence fact (test 12 non-reachable).
UNLINKED_EVIDENCE = "cctv_michael_office_01"
# A discoverable fact from the OTHER case version tests.
V2_ONLY_EVIDENCE = "v2_only_record_01"


def client(phase5_app) -> TestClient:
    return TestClient(phase5_app)


def case_for(phase5_app):
    """API drive: one anonymous session + a PUBLISHED case + creator token."""
    with client(phase5_app) as c:
        session_token, _ = create_session(c)
        case = create_case(c, session_token)
        return case["caseId"], case["creatorAccessToken"]


def playthrough(phase5_app, case_id, creator, version=1):
    """API drive: one playthrough pinned to ``version``."""
    with client(phase5_app) as c:
        status, body = create_playthrough(c, creator, case_id, version)
        assert status == 201, body
        return body["playthroughId"], body["playthroughAccessToken"]


def bootstrap(phase5_app, pt_id, pt_token):
    """GET /api/v1/playthroughs/{pt_id}/investigation."""
    with client(phase5_app) as c:
        res = c.get(
            f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token)
        )
    return res


def discover(phase5_app, pt_id, pt_token, evidence_id):
    """POST .../evidence/{evidence_id}/discover."""
    with client(phase5_app) as c:
        res = c.post(
            f"/api/v1/playthroughs/{pt_id}/evidence/{evidence_id}/discover",
            headers=auth(pt_token),
        )
    return res


def interact(phase5_app, pt_id, pt_token, object_id, interaction):
    """POST .../objects/{object_id}/interact."""
    with client(phase5_app) as c:
        res = c.post(
            f"/api/v1/playthroughs/{pt_id}/objects/{object_id}/interact",
            json={"interaction": interaction},
            headers=auth(pt_token),
        )
    return res


def read_record(phase5_app, pt_id, pt_token, record_id):
    """GET .../records/{record_id}."""
    with client(phase5_app) as c:
        res = c.get(
            f"/api/v1/playthroughs/{pt_id}/records/{record_id}",
            headers=auth(pt_token),
        )
    return res


def publish_v2_with_extra_evidence(phase5_app, case_id, extra_evidence_id=V2_ONLY_EVIDENCE):
    """Insert a deterministic v2 published row whose evidence set contains
    ``extra_evidence_id`` — a version-2-only record (no provider call).

    The v2 payload also gains a NEW object + placement linking the record so a
    V2 playthrough can legitimately discover it (the v1 payload never learns
    about it — the v1/v2 separation is what the tests assert).
    """
    store = phase5_app.state.store
    now = float(phase5_app.state.clock.now())
    v1 = store.get_published(case_id, 1)
    payload = json.loads(v1.payload_json)
    payload["caseVersion"] = 2
    payload["publishedAt"] = now
    payload["draft"]["evidence"].append(
        {
            "id": extra_evidence_id,
            "kind": "digital",
            "discoverable": True,
            "reliability": "high",
            "source_ref": {
                "kind": "record",
                "sourceId": f"record_{extra_evidence_id}",
            },
            "propositions": [
                {
                    "type": "OTHER",
                    "personId": None,
                    "locationId": None,
                    "objectId": None,
                    "motiveId": None,
                    "observedAt": None,
                    "uncertaintySeconds": 0,
                    "structured": {},
                }
            ],
            "presentation": {
                "title": "v2-only record",
                "description": "belongs only to version 2",
            },
        }
    )
    payload["draft"]["objects"].append(
        {
            "object_id": "v2_object",
            "asset_id": "PROP_BOTTLE_01",
            "affordances": ["INSPECTABLE"],
            "subtype": "decor",
        }
    )
    payload["draft"]["world_graph"]["placements"].append(
        {
            "object_id": "v2_object",
            "asset_id": "PROP_BOTTLE_01",
            "location_id": SCENE_LOCATION,
            "anchor": "shelf_01",
            "interaction": "inspect",
            "evidence_id": extra_evidence_id,
        }
    )
    store.create_case_version(
        case_id=case_id,
        version=2,
        state="PUBLISHED",
        generation_id="GEN-2",
        created_at=now,
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