"""Phase45 §43 scenarios 10/11/12/17 + partial-declaration + scene/locations
differ + payload environment identity."""
from harness import *  # noqa: F401,F403

import dataclasses

from app.domain.resolution import ResolutionType

NO_MURDER_LOCK = (
    "Victim: sarah_miller\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)


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


def _mutate_crime(record, **kw):
    draft = record.published.draft
    new_draft = dataclasses.replace(draft, crime=dataclasses.replace(draft.crime, **kw))
    record.draft = new_draft
    record.published = dataclasses.replace(record.published, draft=new_draft)
    return record


def s11_witness():
    ctrl, record = held(golden_script(), "medium", prompt=NO_MURDER_LOCK)
    record = _mutate_crime(record, murderer_id="emily_reed",
                           resolution_type=ResolutionType.HOMICIDE)
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print(f"[s11 witness-murderer] ok={ok} events={_names(records)}")
    for r in records:
        if getattr(r, "pd_event", "") in ("world_compose.failed", "world_compose.fallback"):
            print("   ", r.pd_event, r.pd_fields.get("reasonCode"))
    print("   foreign:", _foreign(record.published.draft))


def s12_suicide():
    ctrl, record = held(golden_script(), "medium", prompt=NO_MURDER_LOCK)
    record = _mutate_crime(record, murderer_id=None,
                           resolution_type=ResolutionType.SUICIDE)
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print(f"[s12 suicide] ok={ok} events={_names(records)}")
    for r in records:
        if getattr(r, "pd_event", "") in ("world_compose.failed", "world_compose.fallback"):
            print("   ", r.pd_event, r.pd_fields.get("reasonCode"))
    print("   foreign:", _foreign(record.published.draft))


def partial_declaration():
    docs = renamed_docs("prov_obj_", "prov_ev_")
    # declare 8/9 kit base ids: un-prefix all EXCEPT kitchen_knife
    wg = docs[GenerationStage.WORLD_GRAPH]["worldGraph"]
    for obj in docs[GenerationStage.PUBLIC_WORLD]["objects"]:
        if obj["objectId"] != "prov_obj_kitchen_knife":
            obj["objectId"] = "prov_obj_" + obj["objectId"].replace("prov_obj_", "")
    # un-prefix for every base id except kitchen_knife
    base = set(KIT_BASE_OBJECT_IDS["apartment"]) - {"kitchen_knife"}
    mapping = {o: o for o in base}
    for obj in docs[GenerationStage.PUBLIC_WORLD]["objects"]:
        oid = obj["objectId"]
        if oid.startswith("prov_obj_") and oid[len("prov_obj_"):] in mapping:
            obj["objectId"] = oid[len("prov_obj_"):]
    for p in wg["placements"]:
        oid = p["objectId"]
        if oid.startswith("prov_obj_") and oid[len("prov_obj_"):] in mapping:
            p["objectId"] = oid[len("prov_obj_"):]
    ctrl, record = held(script_from_docs(docs), "medium", prompt=T.PROVIDER_PROMPT)
    declared = {str(o.object_id) for o in record.draft.objects}
    print("[partial] declared base count:",
          len(set(KIT_BASE_OBJECT_IDS["apartment"]) & declared), "/",
          len(KIT_BASE_OBJECT_IDS["apartment"]),
          "kit_base subset:", set(KIT_BASE_OBJECT_IDS["apartment"]) <= declared)
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    skip = [r for r in records if getattr(r, "pd_event", "") == "world_compose.skipped"]
    print(f"[partial] ok={ok} events={_names(records)} reason=",
          skip[0].pd_fields.get("reasonCode") if skip else None)


def scene_locations_differ():
    """Provider declares kit base object ids but its OWN (different) scene and
    location ids; force composer; verify the rebuilt graph is declared."""
    docs = renamed_docs("prov_obj_", "prov_ev_")
    # un-prefix object ids back to kit base ids (force composer)
    for obj in docs[GenerationStage.PUBLIC_WORLD]["objects"]:
        obj["objectId"] = obj["objectId"].replace("prov_obj_", "")
    wg = docs[GenerationStage.WORLD_GRAPH]["worldGraph"]
    for p in wg["placements"]:
        p["objectId"] = p["objectId"].replace("prov_obj_", "")
    # rename the world-graph location ids to a provider namespace
    wg["locations"] = [
        {**loc, "locationId": "provzone_" + loc["locationId"]}
        for loc in wg["locations"]
    ]
    for p in wg["placements"]:
        p["locationId"] = "provzone_" + p["locationId"]
    ctrl, record = held(script_from_docs(docs), "medium", prompt=T.PROVIDER_PROMPT)
    declared = {str(o.object_id) for o in record.draft.objects}
    print("[scene/loc] kit_base subset:", set(KIT_BASE_OBJECT_IDS["apartment"]) <= declared)
    print("   provider locations:", [l.location_id for l in record.draft.world_graph.locations])
    print("   provider scene env:", record.draft.scene.environment_id)
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print(f"[scene/loc] ok={ok} events={_names(records)}")
    print("   foreign after:", _foreign(record.published.draft))
    print("   after locations:", [l.location_id for l in record.published.draft.world_graph.locations])
    print("   after scene env:", record.published.draft.scene.environment_id)


def scene_env_skip_nonapartment():
    """provider-authored world skipped for a NON-apartment resolved kit: does the
    persisted scene keep the resolved environment identity?"""
    ctrl, record = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
    print("[env-skip] provider scene env:", record.draft.scene.environment_id)
    svc = service()
    for env in ("office", "warehouse", "hotel_suite", "mansion"):
        ctrl, rec = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
        with capture() as records:
            ok = svc._apply_kit_composition(rec, env, decorative(), None)
        print(f"[env-skip {env}] ok={ok} events={_names(records)} "
              f"scene.environment_id={rec.published.draft.scene.environment_id!r} "
              f"name={rec.published.draft.scene.name!r}")


run("s11 witness-murderer", s11_witness)
run("s12 suicide", s12_suicide)
run("partial declaration", partial_declaration)
run("scene/locations differ", scene_locations_differ)
run("scene env skip non-apartment", scene_env_skip_nonapartment)
