"""Phase45 §43 — partial namespace declaration + scene/location divergence,
composed-candidate validity, last_validation restoration, and no-late-gen check."""
from harness import *  # noqa: F401,F403

import dataclasses

from app.domain.resolution import ResolutionType


def _names(records):
    return [getattr(r, "pd_event", None) for r in records]


def _foreign(draft):
    objs = {str(o.object_id) for o in draft.objects}
    evs = {str(e.id) for e in draft.evidence}
    locs = {str(l.location_id) for l in draft.world_graph.locations}
    out = []
    for p in draft.world_graph.placements:
        if str(p.object_id) not in objs:
            out.append(("obj", p.object_id))
        if p.evidence_id is not None and str(p.evidence_id) not in evs:
            out.append(("ev", p.evidence_id))
        if str(p.location_id) not in locs:
            out.append(("loc", p.location_id))
    return out


def _no_late(records):
    return [r.pd_fields for r in records
            if getattr(r, "pd_event", None) == "generation.validation.complete"
            and r.pd_fields.get("validationOutcome") != "VALID"]


def _remap_objects(docs, remap):
    """Consistently remap object ids across public objects, placements, the
    world graph, evidence propositions and the crime weapon."""
    for obj in docs[GenerationStage.PUBLIC_WORLD]["objects"]:
        if obj["objectId"] in remap:
            obj["objectId"] = remap[obj["objectId"]]
    wg = docs[GenerationStage.WORLD_GRAPH]["worldGraph"]
    for p in wg["placements"]:
        if p["objectId"] in remap:
            p["objectId"] = remap[p["objectId"]]
    for fact in docs[GenerationStage.EVIDENCE]["evidence"]:
        for prop in fact.get("propositions") or ():
            if prop.get("objectId") in remap:
                prop["objectId"] = remap[prop["objectId"]]
    crime = docs[GenerationStage.CASE_TRUTH].get("crime") or {}
    if crime.get("weaponId") in remap:
        crime["weaponId"] = remap[crime["weaponId"]]


def partial_declaration():
    docs = renamed_docs("prov_obj_", "prov_ev_")
    base = KIT_BASE_OBJECT_IDS["apartment"]
    # un-prefix 8/9 base object ids (leave kitchen_knife prefixed)
    remap = {f"prov_obj_{b}": b for b in base if b != "kitchen_knife"}
    _remap_objects(docs, remap)
    ctrl, record = held(script_from_docs(docs), "medium", prompt=T.PROVIDER_PROMPT)
    declared = {str(o.object_id) for o in record.draft.objects}
    present = set(base) & declared
    print("[partial] declared base:", sorted(present))
    print("[partial] kit_base subset:", set(base) <= declared)
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    skip = [r for r in records if getattr(r, "pd_event", "") == "world_compose.skipped"]
    print(f"[partial] ok={ok} events={_names(records)} reason=",
          skip[0].pd_fields.get("reasonCode") if skip else None)
    print("   foreign:", _foreign(record.published.draft), "late:", _no_late(records))


def scene_locations_differ():
    docs = renamed_docs("prov_obj_", "prov_ev_")
    base = KIT_BASE_OBJECT_IDS["apartment"]
    remap = {f"prov_obj_{b}": b for b in base}
    # also un-prefix every non-base object so the draft is consistent
    for obj in docs[GenerationStage.PUBLIC_WORLD]["objects"]:
        if obj["objectId"].startswith("prov_obj_"):
            remap.setdefault(obj["objectId"], obj["objectId"][len("prov_obj_"):])
    _remap_objects(docs, remap)
    wg = docs[GenerationStage.WORLD_GRAPH]["worldGraph"]
    wg["locations"] = [
        {**loc, "locationId": "provzone_" + loc["locationId"]}
        for loc in wg["locations"]
    ]
    for p in wg["placements"]:
        p["locationId"] = "provzone_" + p["locationId"]
    ctrl, record = held(script_from_docs(docs), "medium", prompt=T.PROVIDER_PROMPT)
    declared = {str(o.object_id) for o in record.draft.objects}
    print("[scene/loc] kit_base subset:", set(base) <= declared)
    print("   provider locations:", [l.location_id for l in record.draft.world_graph.locations])
    print("   provider scene env:", record.draft.scene.environment_id)
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print(f"[scene/loc] ok={ok} events={_names(records)}")
    print("   foreign after:", _foreign(record.published.draft))
    print("   after locations:", [l.location_id for l in record.published.draft.world_graph.locations])
    print("   after scene env:", record.published.draft.scene.environment_id)
    print("   late:", _no_late(records))


def last_validation_restored():
    """After a composed-candidate validation failure/fallback, the authoritative
    last_validation must be the canonical (VALID) one."""
    ctrl, record = held(golden_script(), "medium")
    canonical_outcome = record.last_validation.outcome
    # force a composed candidate that validates non-VALID (mutate crime so the
    # composed validation is RECOVERABLE_REPAIR as in s11) but keep it as the
    # canonical draft too.
    draft = record.published.draft
    new_draft = dataclasses.replace(
        draft, crime=dataclasses.replace(draft.crime, murderer_id="emily_reed")
    )
    record.draft = new_draft
    record.published = dataclasses.replace(record.published, draft=new_draft)
    # canonical report was computed for the thomas crime; recompute it
    pipeline.validate_draft(record)
    canonical_after = record.last_validation
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print(f"[last-validation] ok={ok} events={_names(records)}")
    print("   canonical outcome:", canonical_outcome, "->",
          canonical_after.outcome, "| restored is canonical:",
          record.last_validation is canonical_after)
    print("   late:", _no_late(records))


run("partial declaration", partial_declaration)
run("scene/locations differ", scene_locations_differ)
run("last validation restored", last_validation_restored)
