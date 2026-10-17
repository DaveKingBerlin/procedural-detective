"""ADV-47 probe 2: hostile / malformed presented_data + projection edges.

Run from backend/:
    python ..\\screenshots\\evidence\\adv47\\probe_hostile_data.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from app.services import publication as pub  # noqa: E402


def payload(crime_victim, persons, evidence=(), placements=(), locations=()):
    return {
        "draft": {
            "crime": {"victim_id": crime_victim} if crime_victim is not None else {},
            "persons": list(persons),
            "evidence": list(evidence),
            "world_graph": {"placements": list(placements)},
            "locations": list(locations),
        }
    }


def body_evidence(title="Body found at 22:18", loc="kitchen", obs="2026-09-11T22:18:31+02:00"):
    return {
        "id": "body_found_01",
        "presentation": {"title": title},
        "propositions": [
            {"type": "BODY_FIRST_FOUND_AT", "location_id": loc, "observed_at": obs}
        ],
    }


print("=== A: hostile presented_data values -> bootstrap victim block ===")
hostile = {
    "age": 1e9,
    "gender": "<img src=x onerror=alert(1)>",
    "occupation": 12345,
    "background": {"nested": "object"},
}
p = payload(
    "v",
    [{"person_id": "v", "name": "<script>V</script>", "role": "victim", "presented_data": hostile}],
)
print(json.dumps(pub.victim_profile_of(p), ensure_ascii=False))

print("\n=== A2: age 0 / -5 / 999 / bool / float ===")
for age in (0, -5, 999, True, 1.5, "0", "999", "39"):
    p = payload(
        "v",
        [{"person_id": "v", "name": "V", "role": "victim", "presented_data": {"age": age}}],
    )
    print(f"  age={age!r:>8} -> {json.dumps(pub.victim_profile_of(p))}")

print("\n=== A3: non-mapping presented_data ===")
for bad in ("stringy", ["list"], 42, None, {},):
    p = payload(
        "v",
        [{"person_id": "v", "name": "V", "role": "victim", "presented_data": bad}],
    )
    print(f"  presented_data={bad!r:>12} -> {json.dumps(pub.victim_profile_of(p))}")

print("\n=== B: public_case persons projection ===")
pc = pub.public_case_dict_from_payload(
    payload(
        "v",
        [
            {"person_id": "v", "name": "V", "role": "victim", "presented_data": hostile},
            {"person_id": "s", "name": "S", "role": "suspect", "presented_data": "not-a-map"},
        ],
    )
    | {"caseId": "C", "caseVersion": 1, "title": "T"}
)
print(json.dumps(pc["persons"], ensure_ascii=False))

print("\n=== C: duplicate victim persons ===")
p = payload(
    "v2",
    [
        {"person_id": "v1", "name": "First Victim", "role": "victim"},
        {"person_id": "v2", "name": "Second Victim", "role": "victim"},
    ],
)
print(json.dumps(pub.victim_profile_of(p), ensure_ascii=False))

print("\n=== D: BODY_FIRST_FOUND_AT with no location_id -> discovery shape ===")
ev = body_evidence(loc=None)
ev["propositions"][0].pop("location_id")
p = payload("v", [{"person_id": "v", "name": "V", "role": "victim"}], evidence=[ev])
print(json.dumps(pub.victim_profile_of(p), ensure_ascii=False))

print("\n=== D2: BODY_FIRST_FOUND_AT with observed_at missing ===")
ev = body_evidence()
ev["propositions"][0].pop("observed_at")
p = payload("v", [{"person_id": "v", "name": "V", "role": "victim"}], evidence=[ev])
print(json.dumps(pub.victim_profile_of(p), ensure_ascii=False))

print("\n=== E: victim-linked marker with hostile presented truth in record ===")
rec = {
    "id": "rec1",
    "kind": "witness_observation",
    "presentation": {"title": "T", "description": "D"},
    "propositions": [{"type": "X", "person_id": "v"}],
    "presentedData": {"murdererId": "HIDDEN"},
}
p = payload("v", [{"person_id": "v", "name": "V", "role": "victim"}], evidence=[rec])
print("victimRelated:", pub.project_read_content(p, "rec1"))

print("\n=== F: discovery title/observed_at leak test (undiscovered body record) ===")
ev = body_evidence(title="Killed by thomas_reed with candlestick", obs="2026-09-11T22:17:00+02:00")
p = payload("v", [{"person_id": "v", "name": "V", "role": "victim"}], evidence=[ev])
print(json.dumps(pub.victim_profile_of(p), ensure_ascii=False))
