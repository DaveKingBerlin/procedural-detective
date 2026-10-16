"""Phase45 ADV — confirm the backend savegame projection emits a null
publicCase.scene.environmentId for a provider-authored skipped case, and
persist the exact savegame JSON as evidence."""
from harness import *  # noqa: F401,F403

import json

from app.services.publication import serialize_published_payload
from app.services.savegame import project_savegame_v1


def build(env):
    ctrl, record = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
    svc = service()
    with capture() as records:
        svc._apply_kit_composition(record, env, decorative(), None)
    payload = json.loads(
        serialize_published_payload(record.published, seed=11, prompt="p", title="T")
    )
    sg = project_savegame_v1(payload, difficulty="medium",
                             exported_at="2026-10-16T00:00:00Z")
    return events(records), payload, sg


def main():
    evs, payload, sg = build("office")
    print("[adv] events:", evs)
    print("[adv] draft.scene:", payload["draft"]["scene"])
    print("[adv] savegame.case.publicCase.scene:",
          json.dumps(sg["case"]["publicCase"]["scene"], indent=2))
    print("[adv] savegame.case.scene.environmentId:",
          sg["case"]["scene"].get("environmentId"))
    print("[adv] savegame.case.metadata.environmentId:",
          sg["case"]["metadata"].get("environmentId"))
    out = (ROOT / "screenshots" / "evidence" / "adv45"
           / "savegame_skipped_office.pdcase.json")
    out.write_text(json.dumps(sg, indent=2, sort_keys=True), encoding="utf-8")
    print("[adv] wrote evidence:", out)
    # also the composed-apartment control
    _e, _p, sg2 = build("apartment")
    print("[adv] control(apartment composed? see events) publicCase.scene:",
          sg2["case"]["publicCase"]["scene"])


main()
