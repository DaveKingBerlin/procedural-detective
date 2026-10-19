r"""ADV-48.1 probe 3 - semantic composition of normalized aliases.

Checks whether alias normalization changes the placed anchor / semantics and
whether the result silently passes composition (wrong-but-passing).

Run from backend/:
    python ..\screenshots\evidence\phase48a\adv\probe3_semantics.py
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
from test_world_composer import CATALOG, GOLDEN_PLACEMENTS, KITS  # noqa: E402


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
        evidence_placements=GOLDEN_PLACEMENTS,
        catalog=CATALOG,
        kit=KITS[kit_id],
    )
    placed = [
        (p.object_id, getattr(p, "anchor", None), getattr(p, "location_id", None))
        for p in comp.placements
    ]
    return canon, comp.issues, placed


print("=== alias -> canonical -> composed placement (apartment) ===")
for raw in ("on_table", "on_kitchen_counter", "on_counter", "on_shelf",
            "on_cabinet", "in_drawer", "in_cupboard", "on_floor", "on_wall",
            "beside", "next_to", "on_desk", "in_desk", "inside_desk",
            "at_table", "on_surface", "on_hook", "wall-mounted",
            "in_wall", "on_ground", "on_carpet"):
    for kit in ("apartment", "office"):
        out = compose_alias(raw, kit)
        if out is None:
            print(f"{raw!r:20} {kit:9} -> (not normalized)")
            continue
        canon, issues, placed = out
        print(f"{raw!r:20} {kit:9} -> canon={canon!r:16} issues={issues!r:60} placed={placed}")

print("\n=== does on_shelf/on_cabinet/in_drawer collapse to the SAME anchor as inside_cabinet? ===")
for kit in ("apartment", "office"):
    for raw in ("on_shelf", "on_cabinet", "in_drawer", "inside_cabinet"):
        out = compose_alias(raw, kit)
        if out:
            print(f"{kit:9} {raw!r:18} canon={out[0]!r:16} placed={out[2]}")

print("\n=== 'beside' with a NON-victim target ===")
for kit in ("apartment", "office"):
    out = compose_alias("beside", kit)
    if out:
        print(f"{kit:9} beside -> canon={out[0]!r} issues={out[1]!r} placed={out[2]}")
