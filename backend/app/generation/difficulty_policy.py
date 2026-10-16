"""Phase 44 (ADR-004 D1) — the ONE authoritative difficulty policy module.

This module is the *single source of truth* for the Phase 44 difficulty
contract enforced by Gate 3 (``app.generation.playability``). It exposes:

- a CLOSED ``easy|medium|hard`` vocabulary (aligned with
  ``app.services.savegame.SAVEGAME_DIFFICULTIES``) and ``normalize_difficulty``;
- one frozen :class:`DifficultyPolicy` value per difficulty;
- :func:`difficulty_requirements_block` — a provider-neutral prompt block
  rendered from the SAME policy (AC-13/AC-14: prompt guidance and the
  validator can never drift because they share this module).

Enforcement semantics (ADR-004 D1):

- **MINIMUMS are hard** — a published case MUST satisfy every minimum or Gate 3
  rejects it (``PLAYABILITY_*``).
- **MAXIMUMS (targets) are guidance only** — they shape the provider prompt and
  the observability metrics but NEVER block publication, so a legitimately
  richer case (more inspectables / meaningful clues / red herrings than the
  target range) is never rejected.

**Default difficulty.** An empty/unknown/out-of-vocabulary difficulty
normalizes to ``medium`` (the product default). This prevents an omitted or
malformed value from being used to *lower* the playability contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

# The CLOSED difficulty vocabulary (mirrors services.savegame.SAVEGAME_DIFFICULTIES).
DIFFICULTY_VOCABULARY: tuple[str, ...] = ("easy", "medium", "hard")

# Missing/unknown -> medium (the product-selected default). Never a silent
# downgrade to easy.
DEFAULT_DIFFICULTY = "medium"


@dataclass(frozen=True)
class DifficultyPolicy:
    """One difficulty's enforced minimums + guidance targets.

    ``min_*`` fields are HARD publish-blocking minimums. ``target_*`` tuples are
    ``(low, high)`` guidance ranges used only for prompt rendering and
    observability — they never block publication.
    """

    name: str
    min_inspectable_objects: int
    target_inspectable_objects: tuple[int, int]
    min_meaningful_inspectables: int
    target_meaningful_inspectables: tuple[int, int]
    min_red_herrings: int
    target_red_herrings: tuple[int, int]
    min_distinct_meaningful_sources: int

    def __post_init__(self) -> None:
        if self.name not in DIFFICULTY_VOCABULARY:
            raise ValueError(f"unknown difficulty {self.name!r}")
        for field_name in (
            "min_inspectable_objects",
            "min_meaningful_inspectables",
            "min_red_herrings",
            "min_distinct_meaningful_sources",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative int")
        for field_name in (
            "target_inspectable_objects",
            "target_meaningful_inspectables",
            "target_red_herrings",
        ):
            value = getattr(self, field_name)
            if (
                not isinstance(value, tuple)
                or len(value) != 2
                or value[0] > value[1]
            ):
                raise ValueError(f"{field_name} must be a (low, high) tuple")


# ADR-004 D1 — the enforced contract.
#   easy   : inspectables >= 3, meaningful >= 2, red herrings >= 0, sources >= 2
#   medium : inspectables >= 5, meaningful >= 3, red herrings >= 1, sources >= 3
#   hard   : inspectables >= 7, meaningful >= 5, red herrings >= 2, sources >= 4
DIFFICULTY_POLICIES: Mapping[str, DifficultyPolicy] = {
    "easy": DifficultyPolicy(
        name="easy",
        min_inspectable_objects=3,
        target_inspectable_objects=(3, 5),
        min_meaningful_inspectables=2,
        target_meaningful_inspectables=(2, 3),
        min_red_herrings=0,
        target_red_herrings=(0, 1),
        min_distinct_meaningful_sources=2,
    ),
    "medium": DifficultyPolicy(
        name="medium",
        min_inspectable_objects=5,
        target_inspectable_objects=(5, 7),
        min_meaningful_inspectables=3,
        target_meaningful_inspectables=(3, 5),
        min_red_herrings=1,
        target_red_herrings=(1, 2),
        min_distinct_meaningful_sources=3,
    ),
    "hard": DifficultyPolicy(
        name="hard",
        min_inspectable_objects=7,
        target_inspectable_objects=(7, 10),
        min_meaningful_inspectables=5,
        target_meaningful_inspectables=(5, 7),
        min_red_herrings=2,
        target_red_herrings=(2, 3),
        min_distinct_meaningful_sources=4,
    ),
}


def normalize_difficulty(raw: str | None) -> str:
    """Case-insensitive/strip normalization; unknown/None -> ``medium``.

    A ``None``/empty/whitespace/out-of-vocabulary value NEVER lowers the
    contract: it becomes the product default ``medium`` (ADR-004 D1).
    """
    if not isinstance(raw, str):
        return DEFAULT_DIFFICULTY
    candidate = raw.strip().lower()
    if candidate in DIFFICULTY_POLICIES:
        return candidate
    return DEFAULT_DIFFICULTY


def policy_for(difficulty: str | None) -> DifficultyPolicy:
    """The :class:`DifficultyPolicy` for a (possibly raw) difficulty value."""
    return DIFFICULTY_POLICIES[normalize_difficulty(difficulty)]


def difficulty_requirements_block(difficulty: str | None) -> str:
    """Provider-neutral difficulty-guidance block derived from the SAME policy.

    Rendered into the PUBLIC_WORLD / WORLD_GRAPH stage context so the provider
    is told the difficulty targets. Guidance only — the deterministic Gate 3
    validator stays authoritative.
    """
    policy = policy_for(difficulty)
    lo_insp, hi_insp = policy.target_inspectable_objects
    lo_mean, hi_mean = policy.target_meaningful_inspectables
    lo_red, hi_red = policy.target_red_herrings
    return (
        f"DIFFICULTY REQUIREMENTS \u2014 {policy.name.upper()}\n"
        f"- minimum inspectable objects: {policy.min_inspectable_objects}\n"
        f"- target inspectable objects: {lo_insp}-{hi_insp}\n"
        f"- minimum meaningful inspectables: {policy.min_meaningful_inspectables}\n"
        f"- target meaningful inspectables: {lo_mean}-{hi_mean}\n"
        f"- minimum red herrings: {policy.min_red_herrings}\n"
        f"- target red herrings: {lo_red}-{hi_red}\n"
        "- distribute meaningful clues across at least "
        f"{policy.min_distinct_meaningful_sources} distinct interactions\n"
        "- every solver-critical dimension (WHO/WHY/WEAPON/WHEN) must be "
        "reachable through a player-facing interaction\n"
        "- minimums are HARD (publication-blocking); targets are guidance only"
    )


__all__ = [
    "DEFAULT_DIFFICULTY",
    "DIFFICULTY_POLICIES",
    "DIFFICULTY_VOCABULARY",
    "DifficultyPolicy",
    "difficulty_requirements_block",
    "normalize_difficulty",
    "policy_for",
]
