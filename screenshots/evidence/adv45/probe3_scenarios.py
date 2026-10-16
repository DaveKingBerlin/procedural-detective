"""Phase45 §43 scenarios 6-20 + exception paths + late-validation check."""
from harness import *  # noqa: F401,F403

import contextlib
import dataclasses

from app.domain.resolution import ResolutionType
from app.world.requirements import WorldRequirements


@contextlib.contextmanager
def patch(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, value if False else old)


def _names(records):
    return [getattr(r, "pd_event", None) for r in records]


def _no_late_gen_validation(records):
    bad = [r for r in records
           if getattr(r, "pd_event", None) == "generation.validation.complete"
           and r.pd_fields.get("validationOutcome") != "VALID"]
    return bad


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


# ---- scenario 6/7: composition removes a required clue / collapses Gate 3 ---
def s6_7():
    ctrl, record = held(golden_script(), "medium")
    canonical = record.published
    svc = service()
    with patch(__import__("app.world.composer", fromlist=["compose_world"]),
               "compose_world", T._fake_composition(placements=())):
        with capture() as records:
            ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print(f"[s6/7 remove-all] ok={ok} events={_names(records)}")
    for r in records:
        if getattr(r, "pd_event", "") == "world_compose.fallback":
            print("   fallback:", r.pd_fields.get("reasonCode"))
    print("   published is canonical:", record.published is canonical)
    g = playability.validate_published_candidate(record.published, "medium")
    print("   gate3 valid:", g.valid, g.issue_codes)
    print("   late gen-validation:", _no_late_gen_validation(records))


# ---- scenario 8: composition duplicates placements --------------------------
def s8():
    ctrl, record = held(golden_script(), "medium")
    base = record.published.draft.world_graph.placements
    dup = base[0]
    svc = service()
    with patch(__import__("app.world.composer", fromlist=["compose_world"]),
               "compose_world", T._fake_composition(placements=(dup, dup, *base))):
        with capture() as records:
            ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print(f"[s8 dup] ok={ok} events={_names(records)}")
    objs = [p.object_id for p in record.published.draft.world_graph.placements]
    print("   duplicate object ids:", [o for o in set(objs) if objs.count(o) > 1])
    print("   foreign:", _foreign(record.published.draft))
    print("   late gen-validation:", _no_late_gen_validation(records))


# ---- scenario 9: asset fallback semantics -----------------------------------
def s9():
    from app.assets.catalog import load_catalog_from_repo
    from app.environments.manifests import load_environment
    from app.world.composer import KnownObjectSpecProvider, compose_world
    from app.world.requirements import ObjectRequest
    ctrl, record = held(golden_script(), "medium")
    comp = compose_world(
        WorldRequirements(objects=(ObjectRequest(requested_name="Bronze Ceremonial Ice Pick"),)),
        spec_provider=KnownObjectSpecProvider(),
        evidence_placements=record.published.draft.world_graph.placements,
        catalog=load_catalog_from_repo(),
        kit=load_environment("apartment"),
    )
    placed = [p for p in comp.placements if "ice" in p.object_id or "pick" in p.object_id]
    print("[s9] issues:", comp.issues)
    for p in placed:
        print("   semantic:", p.object_id, "asset:", p.asset_id)
    print("   semantics kept (asset != object):",
          all(p.asset_id != p.object_id for p in placed))


# ---- scenario 11/12: witness-murderer / suicide through composition ---------
def _mutate_crime(record, **kw):
    draft = record.published.draft
    new_crime = dataclasses.replace(draft.crime, **kw)
    new_draft = dataclasses.replace(draft, crime=new_crime)
    record.draft = new_draft
    record.published = dataclasses.replace(record.published, draft=new_draft)
    return record


def s11():
    ctrl, record = held(golden_script(), "medium")
    record = _mutate_crime(record, murderer_id="emily_reed",
                           resolution_type=ResolutionType.HOMICIDE)
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print(f"[s11 witness-murderer] ok={ok} events={_names(records)}")
    print("   foreign:", _foreign(record.published.draft))


def s12():
    ctrl, record = held(golden_script(), "medium")
    record = _mutate_crime(record, murderer_id=None,
                           resolution_type=ResolutionType.SUICIDE)
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print(f"[s12 suicide] ok={ok} events={_names(records)}")
    print("   foreign:", _foreign(record.published.draft))


# ---- scenario 13: composition failure -> canonical fallback ------------------
def s13():
    ctrl, record = held(golden_script(), "medium")
    canonical = record.published
    svc = service()
    with patch(__import__("app.world.composer", fromlist=["compose_world"]),
               "compose_world", T._fake_composition(issues=("world.invalid: x",))):
        with capture() as records:
            ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print(f"[s13] ok={ok} events={_names(records)}")
    print("   published canonical (objects equal):",
          record.published.draft.objects == canonical.draft.objects)
    print("   late gen-validation:", _no_late_gen_validation(records))


# ---- scenario 14: malformed kit anchor mapping ------------------------------
def s14():
    ctrl, record = held(golden_script(), "medium")
    canonical = record.published
    svc = service()
    import app.environments.compose as _compose
    with patch(_compose, "scene_for_kit", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad anchor map"))):
        with capture() as records:
            ok = svc._apply_kit_composition(record, "apartment", decorative(), None)
    print(f"[s14] ok={ok} events={_names(records)}")
    for r in records:
        if getattr(r, "pd_event", "") == "world_compose.failed":
            print("   failed reason:", r.pd_fields.get("reasonCode"))
    print("   published restored:", record.published is canonical)
    print("   late gen-validation:", _no_late_gen_validation(records))


# ---- scenario 15: unsupported environment kit -------------------------------
def s15():
    ctrl, record = held(golden_script(), "medium")
    canonical = record.published
    svc = service()
    with capture() as records:
        ok = svc._apply_kit_composition(record, "planet_mars", decorative(), None)
    print(f"[s15 unsupported kit] ok={ok} events={_names(records)}")
    for r in records:
        print("   ", getattr(r, "pd_event", ""), getattr(r, "pd_fields", {}))
    print("   published restored:", record.published is canonical)
    print("   attempt state:", record.state)


# ---- scenario 16: repeated composition (composer path) ----------------------
def s16():
    ctrl, record = held(golden_script(), "medium")
    svc = service()

    def sig():
        return [(p.object_id, p.asset_id, p.location_id, p.interaction, p.evidence_id)
                for p in record.published.draft.world_graph.placements]

    svc._apply_kit_composition(record, "apartment", decorative(), None)
    a = sig()
    with capture() as records:
        svc._apply_kit_composition(record, "apartment", decorative(), None)
    b = sig()
    print(f"[s16 composer idempotent] first==second: {a == b} n={len(a)} events={_names(records)}")


# ---- scenario 18: empty decoration set --------------------------------------
def s18():
    for env, reqs in (("apartment", WorldRequirements()), ("warehouse", WorldRequirements())):
        ctrl, record = held(golden_script(), "medium")
        svc = service()
        with capture() as records:
            ok = svc._apply_kit_composition(record, env, reqs, None)
        mode = [r.pd_fields.get("compositionMode") for r in records
                if getattr(r, "pd_event", "") in ("world_compose.started",)]
        reason = [r.pd_fields.get("reasonCode") for r in records
                  if getattr(r, "pd_event", "") == "world_compose.skipped"]
        print(f"[s18 env={env} empty] ok={ok} mode={mode} reason={reason} events={_names(records)}")


# ---- scenario 20: all critical dimensions reachable after composition -------
def s20():
    ctrl, record = held(golden_script(), "medium")
    svc = service()
    svc._apply_kit_composition(record, "apartment", decorative(), None)
    g = playability.validate_published_candidate(record.published, "medium")
    print(f"[s20] gate3 valid={g.valid} reach={getattr(g,'reachable_dimensions',None)} "
          f"unreach={getattr(g,'unreachable_dimensions',None)} issues={g.issue_codes}")


run("s6/7 remove clue", s6_7)
run("s8 duplicate placements", s8)
run("s9 asset fallback", s9)
run("s11 witness-murderer", s11)
run("s12 suicide", s12)
run("s13 failure fallback", s13)
run("s14 malformed anchor", s14)
run("s15 unsupported kit", s15)
run("s16 repeated compose", s16)
run("s18 empty decoration", s18)
run("s20 critical dims", s20)
