r"""ADV-48.1 probe 6 - relation hint effect WITHOUT pre-materialized golden placements.

The driver's `_compose_world` passes `evidence_placements=()`; this probe mirrors
that so the relation hint actually drives placement. Shows whether alias
normalization changes the chosen anchor and whether the result silently passes.

Run from backend/:
    python ..\screenshots\evidence\phase48a\adv\probe6_anchor.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="backslashreplace")
_REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO / "backend"))
sys.path.insert(0, str(_REPO / "backend" / "tests"))

from app.environments.resolver import resolve_environment  # noqa: E402
from app.world.composer import KnownObjectSpecProvider, compose_world  # noqa: E402
from app.world.requirements import (  # noqa: E402
    CRITICALITY_REQUIRED,
    ObjectRequest,
    PlacementRelation,
    WorldRequirements,
    normalize_relation_kind,
)
from test_world_composer import CATALOG, KITS  # noqa: E402


def compose_alias(raw_kind, kit_id, name="kitchen knife", evidence_id="forensic_knife_match_01"):
    canon = normalize_relation_kind(raw_kind)
    if canon is None:
        return None
    reqs = WorldRequirements(
        environment_hint=kit_id,
        objects=(
            ObjectRequest(
                requested_name=name,
                evidence_id=evidence_id,
                required_interaction="inspect",
                criticality=CRITICALITY_REQUIRED,
            ),
        ),
        relations=(PlacementRelation(kind=canon, target=name),),
    )
    comp = compose_world(
        reqs,
        env_resolver=resolve_environment,
        spec_provider=KnownObjectSpecProvider(),
        evidence_placements=(),  # mirror the driver
        catalog=CATALOG,
        kit=KITS[kit_id],
    )
    placed = [
        (p.object_id, getattr(p, "anchor", None))
        for p in comp.placements
        if p.object_id == "kitchen_knife"
    ]
    return canon, comp.issues, placed


print("=== anchor chosen for kitchen knife (no golden placements) ===")
for raw in ("on_table", "on_kitchen_counter", "on_counter", "on_desk",
            "in_desk", "inside_desk", "on_shelf", "on_cabinet", "in_drawer",
            "on_floor", "on_wall", "beside", "on_surface"):
    for kit in ("apartment", "office"):
        out = compose_alias(raw, kit)
        if out is None:
            print(f"{raw!r:20} {kit:9} -> not normalized")
            continue
        canon, issues, placed = out
        print(f"{raw!r:20} {kit:9} canon={canon!r:16} issues={issues!r:40} knife_anchor={placed}")
