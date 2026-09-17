"""Phase 11 — deterministic environment resolver (semantic hints -> kit).

Resolution order (strict, deterministic for equal inputs, independent of
dict ordering):

1. **EXACT**      — ``name`` equals an ``environmentId`` (case-sensitive
                   logical id) or normalizes to the kit's canonical name AND
                   the normal form equals the id normal form of that kit only
                   -> provenance ``EXACT``
2. **CANONICAL**  — ``normalize(name) == normalize(canonicalName)`` of exactly
                   one kit -> provenance ``EXACT`` (canonical names and exact
                   ids both report EXACT; only aliases report ALIAS)
3. **ALIAS**      — ``normalize(name)`` matches a normalized alias of exactly
                   one kit -> provenance ``ALIAS`` (``matchedAlias`` carries
                   the verbatim alias)
4. **SEMANTIC**   — word-token overlap between ``name`` and each kit's
                   canonical-name/alias/tag vocabulary; the UNIQUE kit with
                   the maximum score (>= 1) wins -> provenance
                   ``SEMANTIC_TYPE``; a TIE at the maximum score ->
                   ``ambiguous=True`` with the deterministic tied candidate
                   list (never an arbitrary winner)
5. **FALLBACK**   — every other request resolves to the documented default kit
                   (``apartment``) with provenance ``FALLBACK`` (explicit,
                   never fabricated).

Examples (roadmap): ``apartment``/``flat``/``condo`` -> apartment,
``office``/``workplace`` -> office, ``hotel``/``room 312`` -> hotel_suite,
``depot``/``storage hall`` -> warehouse, ``villa``/``manor`` -> mansion.

``normalize`` shares the Asset Oracle identity normalization
(``app.assets.catalog.normalize_asset_text``), so "Hotel Suite" and
"hotel_suite" are the same token.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from app.assets.catalog import normalize_asset_text
from app.environments.manifests import EnvironmentKit, load_all_environments

# The documented fallback environment (roadmap: "unknown location must return
# explicit fallback choice with provenance").
FALLBACK_ENVIRONMENT_ID = "apartment"

_WORD_RE = re.compile(r"[A-Za-z0-9]+")


class EnvironmentProvenance(Enum):
    """How a kit was resolved (Phase 11 vocabulary)."""

    EXACT = "EXACT"
    ALIAS = "ALIAS"
    SEMANTIC_TYPE = "SEMANTIC_TYPE"
    FALLBACK = "FALLBACK"


@dataclass(frozen=True)
class EnvironmentResolution:
    """The deterministic outcome of one environment request.

    ``resolved`` is True whenever a concrete kit was chosen — including the
    explicit fallback (``provenance == FALLBACK``). The only False case is an
    ambiguous semantic match: NO arbitrary winner is chosen, the tied
    candidate ids are returned instead. ``matched_alias`` is the verbatim
    alias for ALIAS resolutions, else None.
    """

    environment_id: str
    version: int
    provenance: EnvironmentProvenance
    ambiguous: bool = False
    candidates: tuple[str, ...] = ()
    matched_alias: str | None = None
    resolved: bool = True


def normalize(text: str) -> str:
    """Deterministic canonical matching form (Asset Oracle normalization)."""
    return normalize_asset_text(text)


def _word_normal_forms(text: str) -> frozenset[str]:
    """Distinct normalized word tokens of ``text`` (empty when no letters)."""
    return frozenset(
        norm for word in _WORD_RE.findall(text) if (norm := normalize_asset_text(word))
    )


def _vocabulary(kit: EnvironmentKit) -> frozenset[str]:
    """The distinct normalized word tokens of a kit's identity vocabulary."""
    tokens: set[str] = set()
    for text in (kit.canonical_name, *kit.aliases, *kit.tags):
        tokens.update(_word_normal_forms(text))
    return frozenset(tokens)


def _fallback_kit(
    kits: Sequence[EnvironmentKit],
) -> EnvironmentKit | None:
    """The documented default kit (apartment) when declared, else the first
    kit in deterministic (environmentId-sorted) order."""
    by_id = {kit.environment_id: kit for kit in kits}
    if FALLBACK_ENVIRONMENT_ID in by_id:
        return by_id[FALLBACK_ENVIRONMENT_ID]
    return kits[0] if kits else None


def _resolve(kits: Sequence[EnvironmentKit], name: str) -> EnvironmentResolution:
    text = name if isinstance(name, str) else ""

    # 1. EXACT logical id (case-sensitive).
    for kit in sorted(kits, key=lambda k: k.environment_id):
        if kit.environment_id == text:
            return EnvironmentResolution(
                environment_id=kit.environment_id,
                version=kit.version,
                provenance=EnvironmentProvenance.EXACT,
            )

    norm = normalize(text)

    # 2. canonical names (normalized equality) -> EXACT.
    canonicals: dict[str, EnvironmentKit] = {}
    for kit in kits:
        norm_canonical = normalize(kit.canonical_name)
        if norm_canonical:
            canonicals.setdefault(norm_canonical, kit)
    if norm:
        kit = canonicals.get(norm)
        if kit is not None:
            return EnvironmentResolution(
                environment_id=kit.environment_id,
                version=kit.version,
                provenance=EnvironmentProvenance.EXACT,
            )

    # 3. aliases -> ALIAS with the verbatim matched alias.
    if norm:
        matched: list[tuple[EnvironmentKit, str]] = []
        for kit in kits:
            for alias in kit.aliases:
                if normalize(alias) == norm:
                    matched.append((kit, alias))
        if matched:
            kit, raw_alias = matched[0]
            return EnvironmentResolution(
                environment_id=kit.environment_id,
                version=kit.version,
                provenance=EnvironmentProvenance.ALIAS,
                matched_alias=raw_alias,
            )

    # 4. semantic word-token scoring (unique top score; ties -> AMBIGUOUS).
    requested_tokens = _word_normal_forms(text)
    if requested_tokens:
        scored: list[tuple[EnvironmentKit, int]] = []
        for kit in kits:
            score = len(requested_tokens & _vocabulary(kit))
            if score >= 1:
                scored.append((kit, score))
        if scored:
            scored.sort(key=lambda pair: (-pair[1], pair[0].environment_id))
            top_score = scored[0][1]
            winners = [pair for pair in scored if pair[1] == top_score]
            if len(winners) == 1:
                kit, _score = winners[0]
                return EnvironmentResolution(
                    environment_id=kit.environment_id,
                    version=kit.version,
                    provenance=EnvironmentProvenance.SEMANTIC_TYPE,
                )
            return EnvironmentResolution(
                environment_id="",
                version=0,
                provenance=EnvironmentProvenance.SEMANTIC_TYPE,
                ambiguous=True,
                candidates=tuple(kit.environment_id for kit, _score in winners),
                resolved=False,
            )

    # 5. explicit fallback (never fabricated).
    fallback = _fallback_kit(kits)
    if fallback is None:
        raise ValueError("cannot resolve an environment: no kits are declared")
    return EnvironmentResolution(
        environment_id=fallback.environment_id,
        version=fallback.version,
        provenance=EnvironmentProvenance.FALLBACK,
    )


def resolve_environment(
    name: str,
    *,
    kits: Sequence[EnvironmentKit] | None = None,
) -> EnvironmentResolution:
    """Resolve an environment hint against the repo kits (or a caller set).

    ``kits`` defaults to ``load_all_environments()``; when provided the kit
    sequence is used as-is (deterministic resolution never re-reads disks).
    Unknown inputs resolve to the documented default (``apartment``) with
    provenance FALLBACK; ambiguous semantic inputs report candidates.
    """
    kit_list: tuple[EnvironmentKit, ...]
    if kits is None:
        kit_list = load_all_environments()
    else:
        kit_list = tuple(kits)
    return _resolve(kit_list, str(name))


__all__ = [
    "EnvironmentProvenance",
    "EnvironmentResolution",
    "FALLBACK_ENVIRONMENT_ID",
    "normalize",
    "resolve_environment",
]