"""Smoke: confirm harness + baseline skip/compose paths."""
from harness import *  # noqa: F401,F403

report("SMOKE")


def smoke_skip():
    ctrl, record = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
    declared = {str(o.object_id) for o in record.draft.objects}
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print("ok:", ok)
    print("events:", events(records))
    print("skip:", event(records, "world_compose.skipped").pd_fields)
    print("declared objects:", sorted(declared))
    print("published placements object ids:",
          [p.object_id for p in record.published.draft.world_graph.placements])


def smoke_compose():
    ctrl, record = held(golden_script(), "medium")
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print("ok:", ok)
    print("events:", events(records))
    print("completed:", event(records, "world_compose.completed").pd_fields)


run("provider-authored skip", smoke_skip)
run("golden compose", smoke_compose)
