"""Phase41 test helpers — payload construction/mutation for resolution cases.

The API-level Phase-41 tests publish a deterministic golden (homicide) case
through the fake provider, then mutate the frozen published payload's hidden
``truth`` section into SUICIDE / witness-murderer variants and insert them as a
new published version directly (zero provider calls, hermetic — mirroring the
``publish_v2`` pattern of ``test_phase7_helpers``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def read_payload(app: Any, case_id: str, version: int = 1) -> dict[str, Any]:
    """The full serialized published payload of ``(case_id, version)``."""
    row = app.state.store.get_published(case_id, version)
    assert row is not None, f"published v{version} missing for {case_id}"
    return json.loads(row.payload_json)


def insert_variant(
    app: Any,
    case_id: str,
    version: int,
    payload: dict[str, Any],
    *,
    generation_id: str = "GEN-P41",
) -> dict[str, Any]:
    """Insert a mutated payload as a new published version (no provider call)."""
    store = app.state.store
    now = float(app.state.clock.now())
    payload["caseVersion"] = version
    payload["publishedAt"] = now
    store.create_case_version(
        case_id=case_id,
        version=version,
        state="PUBLISHED",
        generation_id=generation_id,
        created_at=now,
    )
    store.insert_published(
        case_id=case_id,
        case_version=version,
        payload_json=json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ),
        published_at=now,
    )
    return payload


def to_suicide(payload: dict[str, Any]) -> dict[str, Any]:
    """Mutate the hidden truth into a SUICIDE resolution (no murderer).

    The public draft (suspects/witnesses/victim) is intentionally kept so the
    suicide case still carries red-herring suspects and witnesses.
    """
    crime = payload["truth"]["crime"]
    crime.pop("murderer_id", None)
    crime["resolution_type"] = "SUICIDE"
    return payload


def to_witness_murderer(payload: dict[str, Any], witness_id: str) -> dict[str, Any]:
    """Mutate the hidden truth into a HOMICIDE whose killer is a public
    witness (the witness keeps role=witness and is NOT SUSPECT_ELIGIBLE)."""
    crime = payload["truth"]["crime"]
    crime["murderer_id"] = witness_id
    crime["resolution_type"] = "HOMICIDE"
    return payload


def suicide_body(payload: dict[str, Any]) -> dict[str, Any]:
    """The all-correct SUICIDE accusation body for a mutated suicide payload."""
    crime = payload["truth"]["crime"]
    return {
        "resolutionType": "SUICIDE",
        "murdererId": None,
        "motiveId": crime["motive_id"],
        "weaponId": crime["weapon_id"],
        "crimeTime": crime["crime_time"]["canonical"],
    }


def homicide_body(
    payload: dict[str, Any],
    murderer_id: str,
    *,
    victim_id: str | None = None,
    motive_id: str | None = None,
    weapon_id: str | None = None,
) -> dict[str, Any]:
    """A HOMICIDE accusation body (optionally poisoned per dimension)."""
    crime = payload["truth"]["crime"]
    return {
        "resolutionType": "HOMICIDE",
        "murdererId": murderer_id,
        "motiveId": motive_id or crime["motive_id"],
        "weaponId": weapon_id or crime["weapon_id"],
        "crimeTime": crime["crime_time"]["canonical"],
    }