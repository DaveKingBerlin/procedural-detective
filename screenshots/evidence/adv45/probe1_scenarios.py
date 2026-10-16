"""Phase45 §43 scenarios 1-5 + Model-B skip bypass attempts (forced composer)."""
from harness import *  # noqa: F401,F403

import copy
import dataclasses

report("SCENARIOS 1-5 + FORCED-COMPOSER BYPASS")


def _declared(draft):
    return {str(o.object_id) for o in draft.objects}


def _ev_ids(draft):
    return {str(e.id) for e in draft.evidence}


def _names(dump_events):
    return [getattr(r, "pd_event", None) for r in dump_events]


# ---- scenario 1: provider ids completely disjoint -------------------------
def s1():
    for env in ("apartment", "office"):
        ctrl, record = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
        svc = service()
        with capture() as records:
            ok = svc._apply_kit_composition(record, env, decorative(), None)
        print(f"[s1 env={env}] ok={ok} events={_names(records)}")
        print("   reason:", event(records, "world_compose.skipped").pd_fields.get("reasonCode")
              if event(records, "world_compose.skipped") else None)


# ---- scenario 2: one overlapping object id, rest disjoint ------------------
def s2():
    docs = renamed_docs("prov_obj_", "prov_ev_")
    # make exactly one object id overlap a kit base object id
    for obj in docs[GenerationStage.PUBLIC_WORLD]["objects"]:
        if obj["objectId"] == "prov_obj_vase_01":
            obj["objectId"] = "vase_01"
    wg = docs[GenerationStage.WORLD_GRAPH]["worldGraph"]
    for p in wg["placements"]:
        if p["objectId"] == "prov_obj_vase_01":
            p["objectId"] = "vase_01"
    ctrl, record = held(script_from_docs(docs), "medium", prompt=T.PROVIDER_PROMPT)
    declared = _declared(record.draft)
    print("[s2] vase_01 declared:", "vase_01" in declared,
          "| kit_base subset:", set(KIT_BASE_OBJECT_IDS["apartment"]) <= declared)
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print(f"[s2] ok={ok} events={_names(records)} reason=",
          event(records, "world_compose.skipped").pd_fields.get("reasonCode")
          if event(records, "world_compose.skipped") else None)


# ---- scenario 3: overlapping id with conflicting semantics -----------------
def s3():
    docs = renamed_docs("prov_obj_", "prov_ev_")
    # declare object id "kitchen_knife" but with a vase-like asset (conflict)
    for obj in docs[GenerationStage.PUBLIC_WORLD]["objects"]:
        if obj["objectId"] == "prov_obj_kitchen_knife":
            obj["objectId"] = "kitchen_knife"
            obj["assetId"] = "PROP_VASE_01"
    wg = docs[GenerationStage.WORLD_GRAPH]["worldGraph"]
    for p in wg["placements"]:
        if p["objectId"] == "prov_obj_kitchen_knife":
            p["objectId"] = "kitchen_knife"
            p["assetId"] = "PROP_VASE_01"
    for fact in docs[GenerationStage.EVIDENCE]["evidence"]:
        for prop in fact.get("propositions") or ():
            if prop.get("objectId") == "prov_obj_kitchen_knife":
                prop["objectId"] = "kitchen_knife"
    ctrl, record = held(script_from_docs(docs), "medium", prompt=T.PROVIDER_PROMPT)
    declared = _declared(record.draft)
    print("[s3] kitchen_knife declared:", "kitchen_knife" in declared,
          "| kit_base subset:", set(KIT_BASE_OBJECT_IDS["apartment"]) <= declared)
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print(f"[s3] ok={ok} events={_names(records)}")


# ---- scenario 4: unknown evidence ids (forced composer path) ---------------
def _forced_composer_draft(*, rename_evidence=True, drop_object_id=None,
                           keep_evidence=True):
    """Golden valid record mutated to: declare ALL kit base objects, but rename
    every evidence id to prov_ev_* (disjoint) and optionally DROP one placement
    so the composer falls back to a golden evidence id."""
    ctrl, record = held(golden_script(), "medium")
    draft = record.published.draft
    old_ev = {str(e.id): "prov_ev_" + str(e.id) for e in draft.evidence}
    new_evidence = tuple(
        dataclasses.replace(e, id=old_ev[str(e.id)]) for e in draft.evidence
    )
    new_placements = []
    for p in draft.world_graph.placements:
        if drop_object_id is not None and p.object_id == drop_object_id:
            continue
        ev = p.evidence_id
        if ev is not None:
            ev = old_ev.get(str(ev), ev)
        new_placements.append(dataclasses.replace(p, evidence_id=ev))
    new_wg = dataclasses.replace(draft.world_graph, placements=tuple(new_placements))
    new_draft = dataclasses.replace(draft, evidence=new_evidence, world_graph=new_wg)
    record.draft = new_draft
    record.published = dataclasses.replace(record.published, draft=new_draft)
    return record, old_ev


def s4():
    # 4a: all placements kept, only evidence renamed -> composer reuses provider
    # placements (declared) -> clean.
    record, old_ev = _forced_composer_draft()
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print(f"[s4a evidence-renamed-only] ok={ok} events={_names(records)}")

    # 4b: DROP the letter_opener placement -> composer falls back to golden
    # forensic_letter_opener_01 (undeclared) -> guard must catch.
    record, old_ev = _forced_composer_draft(drop_object_id="letter_opener")
    declared_ev = _ev_ids(record.draft)
    print("[s4b] declared evidence:", sorted(declared_ev))
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print(f"[s4b drop-letter_opener] ok={ok} events={_names(records)}")
    for r in records:
        if getattr(r, "pd_event", "") == "world_compose.skipped":
            print("   skip fields:", r.pd_fields)
    # If the composer produced a placement referencing forensic_letter_opener_01
    # while it is undeclared, that is a guard bypass.
    for p in record.published.draft.world_graph.placements:
        if p.evidence_id is not None and str(p.evidence_id) not in declared_ev:
            print("   *** PUBLISHED FOREIGN evidenceId:", p.object_id, p.evidence_id)


# ---- scenario 5: duplicate semantic objects --------------------------------
def s5():
    ctrl, record = held(golden_script(), "medium")
    draft = record.published.draft
    dup = draft.world_graph.placements[0]
    # duplicate the placement (same object_id) twice
    wg = dataclasses.replace(
        draft.world_graph,
        placements=(*draft.world_graph.placements, dup, dup),
    )
    new_draft = dataclasses.replace(draft, world_graph=wg)
    record.draft = new_draft
    record.published = dataclasses.replace(record.published, draft=new_draft)
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print(f"[s5] ok={ok} events={_names(records)}")
    for r in records:
        print("   ", getattr(r, "pd_event", None), getattr(r, "pd_fields", {}))


# ---- forced-composer bypass: declare kit base ids, inject foreign ev -------
def bypass_declare_all():
    record, old_ev = _forced_composer_draft(
        drop_object_id="letter_opener",
        rename_evidence=True,
    )
    print("[bypass] kit_base subset declared:",
          set(KIT_BASE_OBJECT_IDS["apartment"]) <= _declared(record.draft))
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print(f"[bypass] ok={ok} events={_names(records)}")
    for r in records:
        print("   ", getattr(r, "pd_event", None), getattr(r, "pd_fields", {}))
    declared_ev = _ev_ids(record.draft)
    for p in record.published.draft.world_graph.placements:
        if p.evidence_id is not None and str(p.evidence_id) not in declared_ev:
            print("   *** FOREIGN PUBLISHED evidenceId:", p.object_id, p.evidence_id)


run("s1 disjoint", s1)
run("s2 one overlap", s2)
run("s3 conflicting semantics", s3)
run("s4 unknown evidence", s4)
run("s5 duplicate objects", s5)
run("bypass declare-all", bypass_declare_all)
