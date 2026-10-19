r"""ADV-48.1 probe 1 - normalize_relation_kind bypass/overreach battery.

Run from backend/:
    python ..\screenshots\evidence\phase48a\adv\probe1_normalize.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="backslashreplace")

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))

from app.world.requirements import (  # noqa: E402
    RELATION_KINDS,
    RELATION_TO_ANCHOR_TYPES,
    PlacementRelation,
    normalize_relation_kind,
)

CANON = set(RELATION_KINDS)

CASES = [
    # observed + safe aliases
    "on_kitchen_counter", "on_counter", "on_table", "on_desk", "on_shelf",
    "on_cabinet", "in_drawer", "in_cupboard", "on_floor", "on_wall",
    "beside", "next_to", "near_victim", "inside_cabinet", "floor_area",
    # separator variants
    "on-kitchen-counter", "on.kitchen.counter", "On Kitchen Counter",
    "on_the_counter", "on the table", "ON_TABLE",
    # preposition / stopword variants
    "upon_counter", "rests_on_counter", "on_countertop", "atop_table",
    "on_tabletop", "table_top", "tabletop",
    # leading/trailing junk
    "  on_table  ", "\ton_table\n", "\n on_table \r\n",
    "on_table; DROP TABLE cases", "on_table\n; DROP",
    "on_table_extra_nefarious", "on_table_extra", "not_on_table",
    "on_table_on_table", "on_on_table", "in_in_cabinet",
    "inside_on_table", "inside_desk", "in_desk", "at_table", "at_cabinet",
    "on_floor_near_window", "on_bed", "on_chair", "on_television",
    "teleported_inside", "behind_the_sofa", "inside_black_hole",
    # weird control / zero-width / bidi / homoglyph
    "\x01on_table", "on_table\x01", "on\x00table", "on\x00_table",
    "on\u200b_table", "on\ufeff_table", "o\u200bn_table",
    "\u202e on_table \u202c",  # RTL override
    "\uff4f\uff4e\uff3f\uff54\uff41\uff42\uff4c\uff45",  # fullwidth on_table (NFKC)
    "\u212b",  # angstrom
    # oversized / non-string
    "on_table" * 40, "on_table" + "x" * 200,
    "", " ", "_", "___", "the", "the_the", "on", "in", "inside", "at",
    None, 123, 1.5, True, ["on_table"], {"kind": "on_table"},
    # canonical mangling checks
    "on_wall", "on-table", "on table",
]


def show(raw):
    try:
        out = normalize_relation_kind(raw)
    except Exception as exc:  # noqa: BLE001
        return f"RAISED {type(exc).__name__}: {exc}"
    return repr(out)


print("=== RELATION_KINDS ===", RELATION_KINDS)
print("=== RELATION_TO_ANCHOR_TYPES keys ===", tuple(RELATION_TO_ANCHOR_TYPES))
print("=== keys == canonical? ===",
      set(RELATION_TO_ANCHOR_TYPES) == CANON,
      "keys:", sorted(RELATION_TO_ANCHOR_TYPES))

print("\n=== normalizer battery ===")
for raw in CASES:
    out = show(raw)
    flag = ""
    if isinstance(raw, str) and out != "None":
        try:
            canon = normalize_relation_kind(raw)
        except Exception:
            canon = None
        if canon not in CANON:
            flag = " <<< NON-CANONICAL OUTPUT!"
    print(f"{raw!r:50} -> {out}{flag}")

print("\n=== dataclass strictness ===")
for k in ("on_kitchen_counter", "on_table", "on_shelf", "inside_desk", "ON_TABLE"):
    try:
        r = PlacementRelation(kind=k)
        print(f"PlacementRelation({k!r}) ACCEPTED kind={r.kind!r}")
    except ValueError as exc:
        print(f"PlacementRelation({k!r}) rejected: {exc}")

print("\n=== semantic spot checks (alias -> anchor type) ===")
for raw in ("on_kitchen_counter", "on_shelf", "on_cabinet", "in_drawer",
            "beside", "next_to", "on_countertop", "in_desk", "inside_desk",
            "at_table", "in_floor", "on_on_table", "inside_on_table"):
    canon = normalize_relation_kind(raw)
    print(f"{raw!r:22} -> {canon!r:18} anchor={RELATION_TO_ANCHOR_TYPES.get(canon)}")
