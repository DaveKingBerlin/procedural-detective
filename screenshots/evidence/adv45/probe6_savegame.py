"""Phase45 §43 scenario 10 — save/reload projections after a composed/skipped
case; confirms the published payload environment identity."""
from harness import *  # noqa: F401,F403

import json

from app.services.publication import (
    public_case_dict_from_payload,
    serialize_published_payload,
)
from app.services.savegame import project_savegame_v1


def _payload(record):
    return json.loads(
        serialize_published_payload(record.published, seed=11, prompt="p", title="T")
    )


def composed_apartment():
    ctrl, record = held(golden_script(), "medium")
    svc = service()
    svc._apply_kit_composition(record, "apartment", decorative(), None)
    payload = _payload(record)
    pub = public_case_dict_from_payload(payload, include_hidden=False) if False else None
    scene = payload["draft"]["scene"]
    print("[scenario10 composed apartment] scene:",
          {k: scene.get(k) for k in ("location_id", "name", "environment_id",
                                     "environment_version")})
    sg = project_savegame_v1(payload, difficulty="medium", exported_at="2026-10-16T00:00:00Z")
    print("   savegame case.metadata.environmentId:", sg["case"]["metadata"].get("environmentId"))
    print("   savegame case.scene.environmentId:", sg["case"]["scene"].get("environmentId"))
    print("   savegame worldObjects:", len(sg["case"]["scene"].get("worldObjects", [])))
    ids = {wo["objectId"] for wo in sg["case"]["scene"]["worldObjects"]}
    print("   object ids (first 5):", sorted(ids)[:5])


def skipped_office():
    ctrl, record = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
    svc = service()
    with capture() as records:
        svc._apply_kit_composition(record, "office", decorative(), None)
    payload = _payload(record)
    scene = payload["draft"]["scene"]
    print("[scenario10 skipped office] events:", events(records))
    print("   scene:", {k: scene.get(k) for k in ("location_id", "name",
                                                  "environment_id", "environment_version")})
    sg = project_savegame_v1(payload, difficulty="medium", exported_at="2026-10-16T00:00:00Z")
    print("   savegame case.metadata.environmentId:", sg["case"]["metadata"].get("environmentId"))
    print("   savegame case.scene.environmentId:", sg["case"]["scene"].get("environmentId"))
    print("   -> frontend requireEnvironmentId defaults null to: 'apartment'")


run("scenario10 composed apartment", composed_apartment)
run("scenario10 skipped office", skipped_office)
