"""Phase 14 — the typed ``WorldRequirements`` contract + bounded validation.

``WorldRequirements`` is the deterministic internal representation derived from
a natural-language prompt (``app.world.extract``) and consumed by the world
composer (``app.world.composer``):

    prompt  ->  WorldRequirements  ->  Environment Resolver / Asset Oracle
              ->  World Composer  ->  WorldComposition  ->  published world

Contract rules:

- **bounded**: at most ``MAX_OBJECT_REQUESTS`` object requests and
  ``MAX_RELATIONS`` placement relations; every string is length-bounded and
  free of URL schemes, path separators / traversal, absolute-path prefixes,
  control characters and the shared Unicode format/glyph class
  (``app.assets.glyphs``) — the same defensive surface the Asset Oracle
  request gate applies;
- **typed**: ``ObjectRequest`` carries ``requestedName <= 120`` plus bounded
  optional semantic hints (category/subtype/tags/requiredInteraction/
  evidenceId/requiredEvidenceCapabilities) and the Phase 12 bounded variant
  parameters (``variant_params`` = an ordered tuple of ``(key, value)`` pairs,
  keys restricted to the Phase 12 ``VARIANT_PARAM_KINDS``, values safe strings
  or finite numbers);
- **bounded semantic relations only**: a placement relation's ``kind`` is one
  of the six documented vocabulary values and maps 1:1 to semantic anchor
  TYPES (``RELATION_TO_ANCHOR_TYPES``). The composer turns each relation into
  a placement anchor-type preference for the bound object; there is NO free
  text, NO transform and NO path in the relation model;
- ``unsafe_unsupported`` records sanitized notes for KNOWN-UNSAFE requests
  (e.g. "bomb"/"gun"/"explosive") — such requests are NEVER composed into an
  asset (safe fail); the note is diagnostic only and never serialized.

Construction runs the same bounded checks (``__post_init__``), so a hand-built
``WorldRequirements`` can never bypass validation (the catalog.py pattern).
``world_requirements_issues`` is the non-raising sibling used by tests.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Mapping

from app.assets.catalog import VARIANT_PARAM_KINDS
from app.assets.glyphs import format_glyph_issues

# --------------------------------------------------------------------------- #
# documented bounds (deterministic, mirror the Asset Oracle request surface)
# --------------------------------------------------------------------------- #

MAX_OBJECT_REQUESTS = 24
MAX_RELATIONS = 12
MAX_REQUESTED_NAME_LENGTH = 120
MAX_TAGS = 8
MAX_CAPABILITIES = 8
MAX_VARIANT_PARAMS = 16  # one per Phase 12 VARIANT_PARAM_KINDS is enough; headroom
MAX_STRING_LENGTH = 120  # every other free-form string (hints/tags/values)

# Phase 14_5 — the frozen criticality vocabulary of an ObjectRequest. A
# REQUESTED (crime-critical) object must never silently disappear or be
# substituted with a semantically-incorrect catalog asset: the world composer
# records ``world.unresolved-object`` and the generation lifecycle repairs or
# fails publication. A DECORATIVE object may degrade gracefully.
# Documented classification rule (applied by ``app.world.extract``; ADV-236
# the arc is NARROWED to genuinely weapon-adjacent strong signals): a prompt
# noun is REQUIRED when it appears in the locked-constraint weapon field or in
# strong weapon/case context that arcs the CASE story (the killer was
# "killed/stabbed with X" / it "is the weapon/tool" used — see
# ``UNSEEN_WEAPON_CONTEXT_WORDS``); ``with``/``used``-adjacent ordinary
# instrument prose ("with a tray", "used a spatula") is DECORATIVE, never
# REQUIRED, so ordinary prose nouns can never fail a case.
CRITICALITY_REQUIRED = "required"
CRITICALITY_DECORATIVE = "decorative"
CRITICALITY_ALLOWED: tuple[str, ...] = (CRITICALITY_REQUIRED, CRITICALITY_DECORATIVE)

# The six documented relation kinds (bounded semantic relations only).
RELATION_KINDS: tuple[str, ...] = (
    "on_desk",
    "on_table",
    "near_victim",
    "inside_cabinet",
    "floor_area",
    "on_wall",
)


def semantic_object_id(requested_name: Any) -> str:
    """Deterministic canonical SEMANTIC object id from a requested name.

    The authoritative public/evidence identity (Phase 19 §"semantic object id
    vs render asset id" / Phase 19E): the slug of the SEMANTIC display name,
    NEVER the render asset identity. ``"antique brass letter opener"`` ->
    ``"antique_brass_letter_opener"``; ``"fork"`` -> ``"fork"``. The id-sheet
    weapon id (``app.services.ollama_driver._identity_slug``) equals this slug
    for the lock weapons (short, honorific-free names), so CaseTruth / evidence
    references / solver / accusation all agree on the same token. Bounded to 40
    characters exactly like the composer's new-object id rule (a longer name is
    truncated deterministically — ``safe_string_issues`` already bounded the
    name; this is a stable collision-free-form reduction). Returns ``"prop"``
    when nothing safe remains (a hostile request never escapes validation
    before this point — the caller records a safe-fail note).
    """
    lowered = "".join(
        ch for ch in str(requested_name).casefold() if ch.isalnum() or ch in " _-"
    )
    words = [word for word in lowered.replace("-", " ").split() if word]
    slug = " ".join(words)[:40].replace(" ", "_").strip("_")
    return slug if slug else "prop"

# The documented mapping from a relation kind to the semantic anchor TYPE it
# addresses (the Phase 11 ANCHOR_TYPES vocabulary). ``near_victim`` is the
# PROXIMITY relation: the composer places the bound object on an
# evidence-capable anchor and verifies the anchor stays within
# ``NEAR_VICTIM_PROXIMITY`` units of a kit BODY anchor (see
# ``app.world.composer``).
RELATION_TO_ANCHOR_TYPES: Mapping[str, str] = {
    "on_desk": "DESK_EVIDENCE",
    "on_table": "TABLE_PROP",
    "near_victim": "BODY",  # proximity (never on the BODY anchor itself)
    "inside_cabinet": "STORAGE",
    "floor_area": "FLOOR_EVIDENCE",
    "on_wall": "WALL_EVIDENCE",
}

# Forbidden URL-scheme tokens (substring scan, as in the asset-request gate).
_FORBIDDEN_URL_TOKENS: tuple[str, ...] = (
    "http:",
    "https:",
    "data:",
    "file:",
    "javascript:",
)
# Windows drive-letter absolute-path prefix, e.g. "C:\" / "C:/".
_DRIVE_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
# Executable/handler word tokens at word boundaries (mirror the request gate).
_FORBIDDEN_WORD_RE = re.compile(
    r"\b(?:script|handler|shader|function|eval)\b", re.IGNORECASE
)


def safe_string_issues(value: Any, where: str) -> tuple[str, ...]:
    """Deterministic sorted issue strings for ONE free-form world string.

    Guards: non-empty string, ``MAX_STRING_LENGTH`` bound, no control
    characters, no Unicode format/glyph class members (``app.assets.glyphs``),
    no forbidden URL-scheme tokens (RAW + NFKC-normalized forms), no word-boundary
    executable tokens, no path separators, no ``..`` traversal and no absolute
    path prefix. Never raises; an empty tuple means the string is safe.
    """
    if not isinstance(value, str) or not value:
        return (f"{where}: must be a non-empty string",)
    issues: list[str] = []
    norm = unicodedata.normalize("NFKC", value)
    if len(value) > MAX_STRING_LENGTH or len(norm) > MAX_STRING_LENGTH:
        issues.append(f"{where}: string exceeds {MAX_STRING_LENGTH} characters")
    if any(ord(ch) < 0x20 for ch in value):
        issues.append(f"{where}: contains a control character")
    issues.extend(format_glyph_issues(value, where))
    forms = (value, norm)
    lowered = [form.casefold() for form in forms]
    for scheme in _FORBIDDEN_URL_TOKENS:
        if any(scheme in form for form in lowered):
            issues.append(f"{where}: contains a forbidden URL scheme {scheme!r}")
            break
    if _FORBIDDEN_WORD_RE.search(value) or _FORBIDDEN_WORD_RE.search(norm):
        issues.append(f"{where}: contains a forbidden executable token")
    if any(("/" in form) or ("\\" in form) for form in forms):
        issues.append(f"{where}: contains a path separator")
    if any(".." in form for form in forms):
        issues.append(f"{where}: contains path traversal '..'")
    if any(
        form.startswith(("/", "\\")) or _DRIVE_ABSOLUTE_RE.match(form)
        for form in forms
    ):
        issues.append(f"{where}: is an absolute path")
    return tuple(sorted(set(issues)))


def _string_list_issues(
    value: Any, where: str, max_len: int
) -> tuple[str, ...]:
    issues: list[str] = []
    if not isinstance(value, (list, tuple)):
        return (f"{where}: must be an array of non-empty strings",)
    if len(value) > max_len:
        issues.append(f"{where}: exceeds the maximum of {max_len} entries")
    for index, entry in enumerate(value):
        issues.extend(safe_string_issues(entry, f"{where}[{index}]"))
    return tuple(sorted(set(issues)))


@dataclass(frozen=True)
class ObjectRequest:
    """One typed, bounded physical-object request derived from a prompt.

    ``requested_name`` is the canonical normal-form name the Asset Oracle
    resolves (<= 120 chars). ``evidence_id`` links a REQUIRED evidence object
    to its golden evidence fact (e.g. the knife -> ``forensic_knife_match_01``);
    evidence-linked requests MUST be placed on an evidence-capable anchor of
    the resolved kit and keep a non-empty interaction (the Phase 10 contract).

    ``variant_params`` (Phase 12, bounded) is an ORDERED tuple of
    ``(key, value)`` pairs, e.g. ``(("material", "wood.dark"), ("scale", 1.1))``.
    Keys are restricted to the Phase 12 ``VARIANT_PARAM_KINDS``; values are
    safe strings (allowlist-validated later by the variant machinery) or finite
    numbers. The composer passes the dict form to
    ``app.assets.resolver.resolve_with_variant``.

    ``criticality`` (Phase 14_5) is the frozen REQUIRED/DECORATIVE label of a
    prompt-derived unseen object (see ``CRITICALITY_ALLOWED``). Hand-built
    requests default to DECORATIVE so legacy callers keep the degradable
    behavior; the extractor sets REQUIRED for weapon/locked-constraint-context
    objects and the generation lifecycle treats an unresolvable REQUIRED object
    as a recoverable/terminal world failure (never a silent drop).
    """

    requested_name: str
    category_hint: str | None = None
    subtype_hint: str | None = None
    tags: tuple[str, ...] = ()
    required_interaction: str | None = None
    evidence_id: str | None = None
    required_evidence_capabilities: tuple[str, ...] = ()
    variant_params: tuple[tuple[str, Any], ...] = ()
    criticality: str = CRITICALITY_DECORATIVE

    def __post_init__(self) -> None:
        issues = object_request_issues(self)
        if issues:
            raise ValueError("; ".join(issues))

    def variant_params_dict(self) -> dict[str, Any]:
        """The Phase 12 variant dict form (deterministic order preserved)."""
        return {key: value for key, value in self.variant_params}


def object_request_issues(request: ObjectRequest) -> tuple[str, ...]:
    """Deterministic sorted issues for ONE ``ObjectRequest`` (empty == safe)."""
    issues: list[str] = []
    if not isinstance(request.requested_name, str) or not request.requested_name:
        issues.append("requestedName: must be a non-empty string")
    else:
        name_issues = list(safe_string_issues(request.requested_name, "requestedName"))
        if len(request.requested_name) > MAX_REQUESTED_NAME_LENGTH:
            name_issues.append(
                f"requestedName: exceeds the maximum of "
                f"{MAX_REQUESTED_NAME_LENGTH} characters"
            )
        issues.extend(sorted(set(name_issues)))
    if request.category_hint is not None:
        issues.extend(
            safe_string_issues(request.category_hint, "categoryHint")
        )
    if request.subtype_hint is not None:
        issues.extend(
            safe_string_issues(request.subtype_hint, "subtypeHint")
        )
    if request.required_interaction is not None:
        issues.extend(
            safe_string_issues(request.required_interaction, "requiredInteraction")
        )
    if request.evidence_id is not None:
        issues.extend(safe_string_issues(request.evidence_id, "evidenceId"))
    issues.extend(
        _string_list_issues(request.tags, "tags", MAX_TAGS)
    )
    issues.extend(
        _string_list_issues(
            request.required_evidence_capabilities,
            "requiredEvidenceCapabilities",
            MAX_CAPABILITIES,
        )
    )
    if not isinstance(request.variant_params, (list, tuple)):
        issues.append("variantParams: must be an array of [key, value] pairs")
    else:
        if len(request.variant_params) > MAX_VARIANT_PARAMS:
            issues.append(
                f"variantParams: exceeds the maximum of {MAX_VARIANT_PARAMS} entries"
            )
        seen_keys: set[str] = set()
        for index, entry in enumerate(request.variant_params):
            where = f"variantParams[{index}]"
            if not isinstance(entry, (tuple, list)) or len(entry) != 2:
                issues.append(f"{where}: must be a [key, value] pair")
                continue
            key, value = entry
            if not isinstance(key, str) or not key:
                issues.append(f"{where}: key must be a non-empty string")
                continue
            if key not in VARIANT_PARAM_KINDS:
                issues.append(
                    f"{where}: unknown variant parameter {key!r} (allowed "
                    f"{list(VARIANT_PARAM_KINDS)!r})"
                )
            if key in seen_keys:
                issues.append(f"{where}: duplicate variant parameter {key!r}")
            seen_keys.add(key)
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                issues.append(
                    f"{where}: value must be a string or a number "
                    f"(got {type(value).__name__})"
                )
            elif isinstance(value, str):
                issues.extend(safe_string_issues(value, f"{where}.value"))
            elif isinstance(value, float) and not math.isfinite(value):
                issues.append(f"{where}.value: must be a finite number")
    if request.criticality not in CRITICALITY_ALLOWED:
        issues.append(
            f"criticality {request.criticality!r} is not in the allowed "
            f"vocabulary {list(CRITICALITY_ALLOWED)!r}"
        )
    return tuple(sorted(set(issues)))


@dataclass(frozen=True)
class PlacementRelation:
    """One bounded placement relation binding an object to a semantic anchor.

    ``kind`` comes from ``RELATION_KINDS``; ``target`` is the casefolded
    ``requestedName`` of the object request the relation binds (the composer
    matches it deterministically). The relation carries NO free text, NO
    transform and NO path — the mapping to anchor TYPES is the documented
    ``RELATION_TO_ANCHOR_TYPES`` vocabulary.
    """

    kind: str
    target: str = ""

    def __post_init__(self) -> None:
        issues = placement_relation_issues(self)
        if issues:
            raise ValueError("; ".join(issues))


def placement_relation_issues(relation: PlacementRelation) -> tuple[str, ...]:
    """Deterministic sorted issues for ONE relation (empty == safe)."""
    issues: list[str] = []
    if relation.kind not in RELATION_KINDS:
        issues.append(
            f"kind {relation.kind!r} is not in the RELATION_KINDS vocabulary "
            f"{list(RELATION_KINDS)!r}"
        )
    if relation.target:
        issues.extend(safe_string_issues(relation.target, "target"))
    return tuple(sorted(set(issues)))


@dataclass(frozen=True)
class WorldRequirements:
    """The typed prompt->world contract.

    Fields:
    - ``environment_hint`` — the prompt-derived location hint (apartment /
      office / hotel_suite / warehouse / mansion alias family), None when the
      prompt names no supported location (the resolver then falls back to the
      documented default kit);
    - ``location_tokens`` — the matched location keyword tokens of the prompt
      (empty when none were supported);
    - ``objects`` — the requested physical objects (bounded, non-empty names);
    - ``relations`` — bounded placement relations;
    - ``unsafe_unsupported`` — sanitized notes for KNOWN-UNSAFE requests that
      were recorded and NOT composed (diagnostic only, never serialized).

    Construction validates every bound; ``world_requirements_issues`` is the
    non-raising sibling.
    """

    environment_hint: str | None = None
    location_tokens: tuple[str, ...] = ()
    objects: tuple[ObjectRequest, ...] = ()
    relations: tuple[PlacementRelation, ...] = ()
    unsafe_unsupported: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        issues = world_requirements_issues(self)
        if issues:
            raise ValueError("; ".join(issues))


def world_requirements_issues(requirements: WorldRequirements) -> tuple[str, ...]:
    """Deterministic sorted issues for ONE ``WorldRequirements`` (empty == OK)."""
    issues: list[str] = []
    if requirements.environment_hint is not None:
        issues.extend(
            safe_string_issues(requirements.environment_hint, "environmentHint")
        )
    issues.extend(
        _string_list_issues(
            requirements.location_tokens, "locationTokens", 8
        )
    )
    if not isinstance(requirements.objects, (list, tuple)):
        issues.append("objects: must be an array of ObjectRequest")
    else:
        if len(requirements.objects) > MAX_OBJECT_REQUESTS:
            issues.append(
                f"objects: exceeds the maximum of {MAX_OBJECT_REQUESTS} requests"
            )
        for index, request in enumerate(requirements.objects):
            if not isinstance(request, ObjectRequest):
                issues.append(f"objects[{index}]: must be an ObjectRequest")
            else:
                issues.extend(object_request_issues(request))
    if not isinstance(requirements.relations, (list, tuple)):
        issues.append("relations: must be an array of PlacementRelation")
    else:
        if len(requirements.relations) > MAX_RELATIONS:
            issues.append(
                f"relations: exceeds the maximum of {MAX_RELATIONS} relations"
            )
        for index, relation in enumerate(requirements.relations):
            if not isinstance(relation, PlacementRelation):
                issues.append(f"relations[{index}]: must be a PlacementRelation")
            else:
                issues.extend(placement_relation_issues(relation))
    issues.extend(
        _string_list_issues(
            requirements.unsafe_unsupported, "unsafeUnsupported", 8
        )
    )
    return tuple(sorted(set(issues)))


__all__ = [
    "CRITICALITY_ALLOWED",
    "CRITICALITY_DECORATIVE",
    "CRITICALITY_REQUIRED",
    "MAX_CAPABILITIES",
    "MAX_OBJECT_REQUESTS",
    "MAX_RELATIONS",
    "MAX_REQUESTED_NAME_LENGTH",
    "MAX_STRING_LENGTH",
    "MAX_TAGS",
    "MAX_VARIANT_PARAMS",
    "ObjectRequest",
    "PlacementRelation",
    "RELATION_KINDS",
    "RELATION_TO_ANCHOR_TYPES",
    "WorldRequirements",
    "object_request_issues",
    "placement_relation_issues",
    "safe_string_issues",
    "world_requirements_issues",
]