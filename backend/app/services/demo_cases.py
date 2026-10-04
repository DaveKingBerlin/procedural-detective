"""Phase 28 — the canonical built-in Demo Case registry (three fixtures).

One frozen, data-driven list of exactly three deterministic demo fixtures.
The registry is the SINGLE source of truth for the demo pool:

- ``demo-apartment``  — Demo Case #1: the EXISTING golden dev-mode case
  (``backend/app/services/dev_mode_case.json``, byte-unchanged, preserved
  verbatim — no content edits, §10).
- ``demo-gallery``    — Demo Case #2: a new polished deterministic case
  (``backend/app/services/demo_fixtures/demo_gallery.json``).
- ``demo-laboratory`` — Demo Case #3: a new polished deterministic case
  (``backend/app/services/demo_fixtures/demo_laboratory.json``).

Every fixture follows the EXACT provider-script contract of
``dev_mode_case.json`` (``GenerationStage`` -> list of one JSON document
string each), so a demo id resolves to a fully valid **fake-provider script**
that runs through the NORMAL generation/validation pipeline (validators,
solvability, world-graph references, accusation dimensions — nothing is
bypassed, §11).

Adding Demo Case #4 is a pure data change: drop a new ``*.json`` script into
``demo_fixtures/`` and append one ``DemoCaseRecord`` below — the service,
transport and selection code never branch on the position of an entry.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app.assets.depthguard import bounded_json_loads
from app.generation.provider import GenerationStage

# --------------------------------------------------------------------------- #
# the three fixed, stable demo case ids (the frontend selection allowlist)
# --------------------------------------------------------------------------- #

DEMO_APARTMENT = "demo-apartment"
DEMO_GALLERY = "demo-gallery"
DEMO_LABORATORY = "demo-laboratory"

# The CLOSED id allowlist (exactly these three values may ever be accepted).
# Kept as a frozenset so ``get_demo_case`` never accidentally accepts a
# positional alias; the tuple order below is the canonical registry order.
DEMO_CASE_IDS: frozenset[str] = frozenset(
    {DEMO_APARTMENT, DEMO_GALLERY, DEMO_LABORATORY}
)

_SERVICES_DIR = Path(__file__).resolve().parent
_FIXTURE_DIR = _SERVICES_DIR / "demo_fixtures"

# The existing golden dev-mode case remains the builtin Demo #1, byte-identical
# (``backend/app/services/dev_mode_case.json`` — never touched).
_DEFAULT_FIXTURE_FILE = "dev_mode_case.json"


@dataclass(frozen=True)
class DemoCaseRecord:
    """One built-in demo fixture (immutable registry metadata).

    ``script`` is lazily loaded and cached on first access (never at import
    time) and is a *copy* — a caller can never mutate the registry.
    """

    demo_case_id: str
    title: str
    summary: str
    prompt: str
    fixture_file: str

    @property
    def script(self) -> dict[GenerationStage, tuple[str, ...]]:
        """The stage-keyed provider script of this fixture (lazy, cached)."""
        return _load_script_cached(self.demo_case_id, self.fixture_file)


def _load_script_cached(
    demo_case_id: str, fixture_file: str
) -> dict[GenerationStage, tuple[str, ...]]:
    cached = _SCRIPT_CACHE.get(demo_case_id)
    if cached is not None:
        return cached
    with _SCRIPT_LOCK:
        cached = _SCRIPT_CACHE.get(demo_case_id)
        if cached is not None:
            return cached
        script = _load_provider_script(_fixture_path(fixture_file))
        _SCRIPT_CACHE[demo_case_id] = script
        return script


def _fixture_path(fixture_file: str) -> Path:
    """Resolve a bundled fixture file to its path.

    ``dev_mode_case.json`` lives alongside this module (the pre-Phase-28
    location, kept byte-identical); the new Phase 28 fixtures live in the
    ``demo_fixtures/`` subdirectory. An explicit path wins; a bare name is
    looked up in the services directory first, then the subdirectory.
    """
    candidate = Path(fixture_file)
    if candidate.is_absolute():
        return candidate
    in_services = _SERVICES_DIR / candidate
    if in_services.is_file():
        return in_services
    return _FIXTURE_DIR / candidate


_SCRIPT_CACHE: dict[str, dict[GenerationStage, tuple[str, ...]]] = {}
_SCRIPT_LOCK = threading.Lock()


def _load_provider_script(path: Path) -> dict[GenerationStage, tuple[str, ...]]:
    """Parse + validate one bundled fixture into the stage key map.

    Same bounded loader + stage allowlist rules as the service's default
    script loader (``GenerationService._load_fake_script``): each top-level
    key must be a valid ``GenerationStage`` value and map to a list of string
    JSON documents. Loading never touches the network and raises ``ValueError``
    for a broken/malformed bundled fixture (a packaging error, surfaced as a
    provider-config error by the service — never served to a player).
    """
    try:
        raw = bounded_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"demo fixture {path.name!r} is not valid JSON: {exc}"
        ) from None
    if not isinstance(raw, Mapping):
        raise ValueError(f"demo fixture {path.name!r} must be a JSON object")
    script: dict[GenerationStage, tuple[str, ...]] = {}
    for stage_name, entries in raw.items():
        try:
            stage = GenerationStage(str(stage_name))
        except ValueError:
            raise ValueError(
                f"unknown stage {stage_name!r} in demo fixture {path.name!r}"
            ) from None
        if not isinstance(entries, (list, tuple)):
            raise ValueError(
                f"demo fixture {path.name!r} stage {stage_name!r} must map to a list"
            )
        script[stage] = tuple(str(entry) for entry in entries)
    # A bundled demo fixture is only usable when every deterministic stage the
    # pipeline may request (the four GENERATING stages + REPAIR) is present —
    # an absent stage would exhaust the provider mid-attempt.
    required = (
        GenerationStage.CASE_TRUTH,
        GenerationStage.PUBLIC_WORLD,
        GenerationStage.EVIDENCE,
        GenerationStage.WORLD_GRAPH,
        GenerationStage.REPAIR,
    )
    missing = [stage.value for stage in required if stage not in script]
    if missing:
        raise ValueError(
            f"demo fixture {path.name!r} is missing required stages: "
            + ", ".join(missing)
        )
    return script


def _parse_document(script: Mapping[Any, Any], stage: GenerationStage) -> dict[str, Any]:
    """Parse one stage's single JSON document strictly (registry validation)."""
    entries = script.get(stage)
    if not entries:
        raise ValueError(f"demo script has no {stage.value} document")
    doc = bounded_json_loads(str(entries[0]))
    if not isinstance(doc, Mapping):
        raise ValueError(f"demo {stage.value} document must be a JSON object")
    return doc


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #

# The canonical three-fixture registry. Order reflects Demo #1 .. #3.
DEMO_CASES: tuple[DemoCaseRecord, ...] = (
    DemoCaseRecord(
        demo_case_id=DEMO_APARTMENT,
        title="The Miller Apartment Case",
        summary=(
            "A finance director's private troubles surface when the CEO of "
            "Miller Consulting is found in her apartment kitchen."
        ),
        prompt=(
            "Victim: Sarah Miller\n"
            "Murderer: Thomas Reed\n"
            "Motive: \u20ac240,000 embezzlement\n"
            "Weapon: Kitchen knife\n"
            "Time: 22:17\n"
            "Witness: Emily Reed\n"
        ),
        fixture_file=_DEFAULT_FIXTURE_FILE,
    ),
    DemoCaseRecord(
        demo_case_id=DEMO_GALLERY,
        title="The Lindqvist Gallery Case",
        summary=(
            "During an evening preview, the director of the Lindqvist Gallery "
            "is found in the main hall. A sales ledger, a revealing email and "
            "a marble candlestick tell a different story than anyone expected."
        ),
        prompt=(
            "Victim: Maria Lindqvist\n"
            "Murderer: Daniel Voss\n"
            "Motive: forged sale\n"
            "Time: 21:47\n"
            "Witness: Jana Petersen\n"
        ),
        fixture_file="demo_gallery.json",
    ),
    DemoCaseRecord(
        demo_case_id=DEMO_LABORATORY,
        title="The Alder Street Study Lab Case",
        summary=(
            "A research chemist is found in her study lab. Two neighbours give "
            "conflicting accounts, a safety file points one way, and the "
            "evidence on a heavy glass bottle settles the timeline."
        ),
        prompt=(
            "Victim: Amara Okafor\n"
            "Murderer: Elias Meyer\n"
            "Motive: safety whistleblower\n"
            "Time: 19:43\n"
            "Witness: Hugo Brandt\n"
        ),
        fixture_file="demo_laboratory.json",
    ),
)


def get_demo_case(demo_case_id: str | None) -> DemoCaseRecord | None:
    """The registry entry for a demo case id (None when unknown/invalid)."""
    if demo_case_id is None or demo_case_id not in DEMO_CASE_IDS:
        return None
    for record in DEMO_CASES:
        if record.demo_case_id == demo_case_id:
            return record
    return None  # pragma: no cover - guarded by DEMO_CASE_IDS


def default_demo_case() -> DemoCaseRecord:
    """Demo Case #1 (``demo-apartment``) — the backward-compatible default."""
    record = get_demo_case(DEMO_APARTMENT)
    assert record is not None  # bundled registry invariant
    return record


# --------------------------------------------------------------------------- #
# player-safe truth-leak scan helpers (used by the Phase 28 regression tests
# AND by the service load path so a bundled fixture can never become player
# visible with a hidden marker)
# --------------------------------------------------------------------------- #

# CaseTruth-adjacent markers that must NEVER appear in player-visible text
# without being naturally discovered evidence. The scan is deliberately
# case-insensitive and covers every player-facing document.
PLAYER_VISIBLE_TRUTH_MARKERS: tuple[str, ...] = (
    "murderer=",
    "correct suspect=",
    "solution=",
    "case_truth:",
    "casestruth",
)


def _iter_text(node: Any) -> tuple[str, ...]:
    if isinstance(node, str):
        return (node,)
    if isinstance(node, Mapping):
        out: list[str] = []
        for key, value in node.items():
            out.extend(_iter_text(value))
        return tuple(out)
    if isinstance(node, (list, tuple)):
        out = []
        for value in node:
            out.extend(_iter_text(value))
        return tuple(out)
    return ()


def player_visible_text(record: DemoCaseRecord) -> tuple[str, ...]:
    """Every player-visible text string of one fixture (for truth-leak tests).

    Only the public/projectable sections are scanned: ``public_world`` and the
    ``presentation`` blocks of ``evidence`` (the world graph carries public
    placement metadata only). The hidden ``case_truth`` stage is server-only
    and is never part of this projection.
    """
    script = record.script
    texts: list[str] = []
    texts.extend(_iter_text(_parse_document(script, GenerationStage.PUBLIC_WORLD)))
    evidence = _parse_document(script, GenerationStage.EVIDENCE).get("evidence")
    if isinstance(evidence, (list, tuple)):
        for item in evidence:
            if isinstance(item, Mapping) and isinstance(item.get("presentation"), Mapping):
                texts.extend(_iter_text(item["presentation"]))
    texts.extend(
        _iter_text(_parse_document(script, GenerationStage.WORLD_GRAPH))
    )
    return tuple(texts)


def truth_leak_issues(record: DemoCaseRecord) -> tuple[str, ...]:
    """Sorted list of every player-visible marker hit (empty when clean)."""
    found: list[str] = []
    lowered = "\n".join(player_visible_text(record)).lower()
    for marker in PLAYER_VISIBLE_TRUTH_MARKERS:
        if marker.lower() in lowered:
            found.append(marker)
    return tuple(sorted(set(found)))


__all__ = [
    "DEMO_APARTMENT",
    "DEMO_CASES",
    "DEMO_CASE_IDS",
    "DEMO_GALLERY",
    "DEMO_LABORATORY",
    "DemoCaseRecord",
    "default_demo_case",
    "get_demo_case",
    "truth_leak_issues",
]