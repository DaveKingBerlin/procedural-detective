"""Phase 24 — deterministic CI fake world generator (docker-smoke Activity Log).

The shipped golden world (``backend/app/services/dev_mode_case.json``) carries
the Phase 19J 20-row activity-log records as published evidence, but links NO
world placement to them — so a golden-case playthrough cannot discover/read a
15-20 row ACTIVITY_LOG record over the wire (the direct-discover route was
removed in Phase 20; discovery only happens through world interactions).

The gitlab ``docker-smoke`` job therefore boots the CI deterministic profile
with a repo-owned fake world that keeps the golden case byte-for-byte except
for ONE world-graph placement change: ``apartment_laptop`` is linked to the
golden activity-log CCTV record (``cctv_thomas_scene_01``) instead of the
email. The smoke then:

  interact laptop(read) -> discover cctv_thomas_scene_01
  GET .../records/cctv_thomas_scene_01  -> renderType ACTIVITY_LOG, 20 rows
  reload -> byte-identical persisted content
  accusation/reveal -> 4/4 (same persons/motives/weapons/truth as golden)

Everything else (case_truth, evidence propositions, public world persons/
motives/weapons, solver-critical facts) stays identical, so the accusation
candidates and the 4/4 reveal keep working exactly like the golden world.

This tool regenerates that committed fixture deterministically from the
shipped golden payload, so a drift between the two ships is a release-check
failure (``--check``). It never touches the golden file itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GOLDEN = _REPO_ROOT / "backend" / "app" / "services" / "dev_mode_case.json"
_OUT = _REPO_ROOT / "compose" / "fake-worlds" / "ci-activity-log-world.json"
# The golden for-scene record id that renders as ACTIVITY_LOG (20 rows).
_ACTIVITY_LOG_EVIDENCE_ID = "cctv_thomas_scene_01"
# The golden laptop placement: keep its object/asset/anchor/interaction, only
# re-link it to the activity-log record so the log is wire-discoverable.
_LAPTOP_OBJECT_ID = "apartment_laptop"
_LAPTOP_INTERACTION = "read"


class GeneratorError(RuntimeError):
    """Typed generator failure (exit 2)."""


def generate(source: Path = _GOLDEN) -> dict:
    """Build the CI fake world dict from the golden payload (no writes)."""
    try:
        golden = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GeneratorError(f"cannot read the golden world {source}: {exc}") from None
    if not isinstance(golden, dict):
        raise GeneratorError("golden world is not a JSON object")

    world = json.loads(json.dumps(golden))
    world_graph_entries = world.get("world_graph")
    if not isinstance(world_graph_entries, list) or not world_graph_entries:
        raise GeneratorError("golden world has no world_graph stage")
    raw = world_graph_entries[0]
    if not isinstance(raw, str):
        raise GeneratorError("golden world_graph stage is not a JSON string")
    try:
        wg = json.loads(raw)
    except ValueError as exc:
        raise GeneratorError(f"golden world_graph is not valid JSON: {exc}") from None

    placements = wg.get("worldGraph", {}).get("placements", [])
    found = False
    changed = 0
    for placement in placements:
        if placement.get("objectId") == _LAPTOP_OBJECT_ID:
            placement["evidenceId"] = _ACTIVITY_LOG_EVIDENCE_ID
            found = True
        if placement.get("image"):
            continue
        if isinstance(placement, dict) and placement.get("evidenceId"):
            changed += 1
    if not found:
        raise GeneratorError(
            f"golden world has no placement for {_LAPTOP_OBJECT_ID!r}"
        )
    world["world_graph"][0] = json.dumps(
        wg, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return world


def render(world: dict) -> bytes:
    """Deterministic compact bytes of a CI world (committed byte-for-byte)."""
    return (
        json.dumps(world, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def check() -> list[str]:
    """Return the drift issues between the committed file and regeneration."""
    if not _OUT.is_file():
        return [f"missing committed CI world: {_OUT}"]
    try:
        expected = render(generate())
    except GeneratorError as exc:
        return [str(exc)]
    actual = _OUT.read_bytes()
    if actual != expected:
        return [f"committed CI world drifted from the golden source: {_OUT}"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.generate_ci_fake_world",
        description=(
            "Regenerate/verify compose/fake-worlds/ci-activity-log-world.json "
            "from the shipped golden payload (Phase 24 docker-smoke Activity "
            "Log fixture)."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed fixture against the golden payload (exit 1 on drift).",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="regenerate the committed fixture in place.",
    )
    args = parser.parse_args(argv)

    if not args.check and not args.write:
        parser.error("pass --check or --write")

    if args.check:
        issues = check()
        if issues:
            for issue in issues:
                print(f"CI FAKE WORLD DRIFT: {issue}", file=sys.stderr)
            return 1
        print("OK: ci-activity-log-world.json is in sync with the golden payload")
        return 0

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = render(generate())
    except GeneratorError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _OUT.write_bytes(payload)
    print(f"wrote {_OUT.relative_to(_REPO_ROOT)} ({len(payload)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())