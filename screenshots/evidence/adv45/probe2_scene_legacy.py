"""Phase45 §43 — scene/environment identity, legacy classification, and
composition idempotence / determinism / decoration edge cases."""
from harness import *  # noqa: F401,F403

import dataclasses

from app.world.requirements import WorldRequirements

report("SCENE / ENVIRONMENT / LEGACY / DECORATION / IDEMPOTENCE")


def scene_probe():
    ctrl, record = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
    print("[scene] provider draft scene:", record.draft.scene)
    print("[scene] provider published scene:", record.published.draft.scene)
    for env in ("apartment", "office", "warehouse"):
        rec = record
        # re-fetch a fresh record each time
        ctrl, rec = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
        before = rec.published.draft.scene
        svc = service()
        with capture() as records:
            ok = svc._apply_kit_composition(rec, env, decorative(), None)
        after = rec.published.draft.scene
        print(f"[scene env={env}] ok={ok} events={events(records)}")
        print(f"    before.environment_id={before.environment_id!r} "
              f"after.environment_id={after.environment_id!r}")
        print(f"    before.name={before.name!r} after.name={after.name!r}")


def legacy_probe():
    """Provider-authored world but EMPTY requirements -> legacy path."""
    ctrl, record = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
    declared = {str(o.object_id) for o in record.draft.objects}
    print("[legacy] kit_base subset:", set(KIT_BASE_OBJECT_IDS["apartment"]) <= declared)
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", WorldRequirements(), None)
    print(f"[legacy] ok={ok} events={events(records)}")
    for r in records:
        if getattr(r, "pd_event", "") == "world_compose.skipped":
            print("   skip reason:", r.pd_fields.get("reasonCode"),
                  "outcome:", r.pd_fields.get("compositionOutcome"))
    declared_ev = {str(e.id) for e in record.published.draft.evidence}
    declared_obj = {str(o.object_id) for o in record.published.draft.objects}
    declared_loc = {str(l.location_id) for l in record.published.draft.world_graph.locations}
    foreign = []
    for p in record.published.draft.world_graph.placements:
        if str(p.object_id) not in declared_obj:
            foreign.append(("obj", p.object_id))
        if p.evidence_id is not None and str(p.evidence_id) not in declared_ev:
            foreign.append(("ev", p.evidence_id))
        if str(p.location_id) not in declared_loc:
            foreign.append(("loc", p.location_id))
    print("[legacy] foreign refs after composition:", foreign)


def idempotence_skip():
    ctrl, record = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
    svc = service()
    sig = lambda: [(p.object_id, p.asset_id, p.location_id, p.interaction, p.evidence_id)
                   for p in record.published.draft.world_graph.placements]
    svc._apply_kit_composition(record, "apartment", decorative(), None)
    a = sig()
    svc._apply_kit_composition(record, "apartment", decorative(), None)
    b = sig()
    print("[idem-skip] first==second:", a == b, "n=", len(a))


def empty_decoration():
    """Empty WorldRequirements on the composer path (need a new object to avoid
    legacy). Empty decoration set = no prompt objects at all on a non-apartment
    kit -> is_legacy False, kit_base disjoint -> skip."""
    ctrl, record = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "office", WorldRequirements(), None)
    print(f"[empty-decor office] ok={ok} events={events(records)}")
    for r in records:
        if getattr(r, "pd_event", "").startswith("world_compose."):
            print("   ", r.pd_event, r.pd_fields)


def anchors_already_valid():
    """Provider-shaped world whose placements already carry kit anchors."""
    ctrl, record = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
    declared = {str(o.object_id) for o in record.draft.objects}
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print(f"[anchors] kit_base subset={set(KIT_BASE_OBJECT_IDS['apartment']) <= declared} "
          f"ok={ok} events={events(records)}")


def red_herrings():
    ctrl, record = held(golden_script(), "medium")
    before = [ (p.object_id, p.evidence_id, p.interaction)
               for p in record.published.draft.world_graph.placements ]
    svc = service()
    svc._apply_kit_composition(record, "apartment", decorative(), None)
    after = [ (p.object_id, p.evidence_id, p.interaction)
              for p in record.published.draft.world_graph.placements ]
    print("[red-herrings] before n=", len(before), "after n=", len(after))
    print("  before:", before)
    print("  after :", after)


run("scene probe", scene_probe)
run("legacy probe", legacy_probe)
run("idempotence skip", idempotence_skip)
run("empty decoration", empty_decoration)
run("anchors already valid", anchors_already_valid)
run("red herrings", red_herrings)
