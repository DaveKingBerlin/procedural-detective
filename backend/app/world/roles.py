"""Phase 19E — the closed SEMANTIC ROLE model for world objects.

A semantic world object (``app.world.requirements.ObjectRequest``) is a stable
app-owned identity independent of its RENDER representation (catalog asset,
catalog variant, or validated declarative procedural AssetSpec). Every request
carries SAFE DECLARATIVE TRAITS derived deterministically from validated
semantics — never an arbitrary LLM-defined role string.

The closed role vocabulary (Phase 19E §"Object roles"):

    STRUCTURAL          — kit scaffolding (kit ``structuralAssets`` / catalog
                          category ``structural``); exists in every kit, never
                          a prompt-specific request.
    DECORATIVE          — an optional scene-enrichment object. May degrade: if
                          it cannot be placed safely it is DISCARDED (a
                          player-safe composition note), never failing the case.
    INTERACTIVE         — the object has a non-empty interaction (typically
                          ``inspect`` or ``read``) the player may trigger. Map
                          to the published ``INSPECTABLE`` affordance.
    EVIDENCE_RELEVANT   — the object is REQUIRED by the case (locked-constraint
                          weapon, evidence-linked, or a crime-critical prompt
                          object). Maps to ``ObjectRequest.criticality ==
                          "required"`` and/or a non-empty ``evidence_id``.
    WEAPON_CANDIDATE    — the only role that grants SOLVER PARTICIPATION: the
                          object is a member of the published POTENTIAL_WEAPON
                          candidate universe (derived deterministically, see
                          mapping table below).

Role derivation (the ONLY way a role is ever assigned):

- ``derive_request_roles(request)`` reads validated ``ObjectRequest`` traits
  (criticality, evidence_id, required_interaction). This is a CLOSED
  deterministic function: no LLM string can introduce a new role.
- ``derive_object_spec_roles(spec)`` reads a validated public ``ObjectSpec``
  affordances (``INSPECTABLE`` / ``POTENTIAL_WEAPON`` / ...) — the PUBLIC
  eligibility surface the solver actually consumes.

Mapping to the existing project vocabulary (REQUIREMENTS §31.1,
``app.domain.public.AFFORDANCES``, ``app.world.requirements``):

┌────────────────┬──────────────────────────────────────────────────────────┐
│ semantic role  │ existing vocabulary                                      │
├────────────────┼──────────────────────────────────────────────────────────┤
│ STRUCTURAL      │ kit ``structuralAssets`` / catalog category structural  │
│ DECORATIVE      │ ``ObjectRequest.criticality == "decorative"``; no        │
│                 │ evidence link; no POTENTIAL_WEAPON affordance           │
│ INTERACTIVE     │ ``ObjectRequest.required_interaction`` non-empty;       │
│                 │ public affordance ``INSPECTABLE``                       │
│ EVIDENCE_RELEVANT│ ``criticality == "required"`` OR ``evidence_id`` or a  │
│                 │ locked-constraint weapon request                        │
│ WEAPON_CANDIDATE│ public affordance ``POTENTIAL_WEAPON`` (granted ONLY by │
│                 │ deterministic code: the driver's locked-weapon match    │
│                 │ ``_enhance_weapon`` or the golden base sharp objects)    │
└────────────────┴──────────────────────────────────────────────────────────┘

SOLVER PARTICIPATION INVARIANT (Phase 19E §"Object roles"): an object enters
the weapon candidate universe / evidence graph / accusation / solver ONLY when
its derived roles include WEAPON_CANDIDATE (i.e. the published affordance set
contains ``POTENTIAL_WEAPON``), which is derived from VALIDATED semantics. A
DECORATIVE object NEVER carries ``POTENTIAL_WEAPON`` and therefore never enters
the logical case (tests pin this).
"""

from __future__ import annotations

from typing import Any, Mapping

from app.world.requirements import (
    CRITICALITY_DECORATIVE,
    CRITICALITY_REQUIRED,
)

# --------------------------------------------------------------------------- #
# the CLOSED role vocabulary (frozen; new roles require an ADR)
# --------------------------------------------------------------------------- #

ROLE_STRUCTURAL = "STRUCTURAL"
ROLE_DECORATIVE = "DECORATIVE"
ROLE_INTERACTIVE = "INTERACTIVE"
ROLE_EVIDENCE_RELEVANT = "EVIDENCE_RELEVANT"
ROLE_WEAPON_CANDIDATE = "WEAPON_CANDIDATE"

SEMANTIC_ROLES: tuple[str, ...] = (
    ROLE_STRUCTURAL,
    ROLE_DECORATIVE,
    ROLE_INTERACTIVE,
    ROLE_EVIDENCE_RELEVANT,
    ROLE_WEAPON_CANDIDATE,
)

# The only affordance that grants SOLVER participation in the weapon dimension.
POTENTIAL_WEAPON_AFFORDANCE = "POTENTIAL_WEAPON"

# Documented vocabulary mapping (the table above as data).
ROLE_TO_VOCABULARY: Mapping[str, tuple[str, ...]] = {
    ROLE_STRUCTURAL: ("structuralAssets", "category: structural"),
    ROLE_DECORATIVE: (CRITICALITY_DECORATIVE,),
    ROLE_INTERACTIVE: ("requiredInteraction", "affordance: INSPECTABLE"),
    ROLE_EVIDENCE_RELEVANT: (CRITICALITY_REQUIRED, "evidenceId"),
    ROLE_WEAPON_CANDIDATE: ("affordance: POTENTIAL_WEAPON",),
}


def derive_request_roles(request: Any) -> tuple[str, ...]:
    """The closed semantic-role set of ONE validated ``ObjectRequest``.

    Deterministic traits-only derivation (see module docstring). ``request``
    may be any object exposing the documented ``ObjectRequest`` fields (the
    dataclass or a duck-typed twin), so callers with bounded wrappers (e.g.
    the driver's injected locked-weapon request) receive identical roles.
    """
    roles: set[str] = set()
    criticality = str(getattr(request, "criticality", CRITICALITY_DECORATIVE) or "")
    evidence_id = getattr(request, "evidence_id", None)
    interaction = getattr(request, "required_interaction", None)
    subtype = getattr(request, "subtype_hint", None)

    if criticality == CRITICALITY_REQUIRED or evidence_id is not None:
        roles.add(ROLE_EVIDENCE_RELEVANT)
    if interaction:
        roles.add(ROLE_INTERACTIVE)
    if criticality == CRITICALITY_DECORATIVE and evidence_id is None:
        roles.add(ROLE_DECORATIVE)
    if str(subtype or "") == "structural":
        roles.add(ROLE_STRUCTURAL)
    return tuple(role for role in SEMANTIC_ROLES if role in roles)


def derive_object_spec_roles(spec: Any) -> tuple[str, ...]:
    """The closed semantic-role set of ONE validated public ``ObjectSpec``.

    Reads the published affordance vocabulary — the SAME surface the candidate
    universes are derived from (``app.domain.eligibility``). Solver
    participation is exactly ``POTENTIAL_WEAPON`` membership.
    """
    affordances = frozenset(str(a) for a in (getattr(spec, "affordances", ()) or ()))
    interaction = getattr(spec, "interaction", None)
    evidence_id = getattr(spec, "evidence_id", None)
    role_set: set[str] = set()
    if POTENTIAL_WEAPON_AFFORDANCE in affordances:
        role_set.add(ROLE_WEAPON_CANDIDATE)
        # a weapon candidate is evidence-relevant by definition (the locked
        # weapon is REQUIRED; base sharp objects are evidence-linked)
        role_set.add(ROLE_EVIDENCE_RELEVANT)
    if evidence_id is not None and evidence_id != "":
        role_set.add(ROLE_EVIDENCE_RELEVANT)
    if "INSPECTABLE" in affordances or interaction:
        role_set.add(ROLE_INTERACTIVE)
    if not (role_set & {ROLE_WEAPON_CANDIDATE, ROLE_EVIDENCE_RELEVANT}):
        # no affordance for the weapon universe and no evidence link ->
        # decorative (or structural, when the subtype says so)
        if str(getattr(spec, "subtype", "") or "") == "structural":
            role_set.add(ROLE_STRUCTURAL)
        else:
            role_set.add(ROLE_DECORATIVE)
    return tuple(role for role in SEMANTIC_ROLES if role in role_set)


def is_solver_participant(roles: Any) -> bool:
    """True when a role set grants SOLVER participation (WEAPON_CANDIDATE)."""
    return ROLE_WEAPON_CANDIDATE in tuple(roles or ())


__all__ = [
    "POTENTIAL_WEAPON_AFFORDANCE",
    "ROLE_DECORATIVE",
    "ROLE_EVIDENCE_RELEVANT",
    "ROLE_INTERACTIVE",
    "ROLE_STRUCTURAL",
    "ROLE_TO_VOCABULARY",
    "ROLE_WEAPON_CANDIDATE",
    "SEMANTIC_ROLES",
    "derive_object_spec_roles",
    "derive_request_roles",
    "is_solver_participant",
]