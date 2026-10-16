"""ADV-45-01 control — the COMPOSED case savegame must round-trip."""
from harness import *  # noqa: F401,F403

import json

from app.services.publication import serialize_published_payload
from app.services.savegame import project_savegame_v1


def main():
    ctrl, record = held(golden_script(), "medium")
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print("[composed] ok=", ok, "events=", events(records))
    payload = json.loads(
        serialize_published_payload(record.published, seed=11, prompt="p", title="T")
    )
    sg = project_savegame_v1(payload, difficulty="medium",
                             exported_at="2026-10-16T00:00:00Z")
    print("[composed] publicCase.scene:",
          json.dumps(sg["case"]["publicCase"]["scene"], sort_keys=True))
    out = (ROOT / "screenshots" / "evidence" / "adv45"
           / "savegame_composed_apartment.pdcase.json")
    out.write_text(json.dumps(sg, indent=2, sort_keys=True), encoding="utf-8")
    print("[composed] wrote:", out)


main()
