"""Phase 14 — deterministic prompt -> ``WorldRequirements`` extractor.

PURE, DETERMINISTIC text extraction (NO LLM, NO network, NO randomness):

- **environment**: the prompt is scanned for the five documented alias
  families (apartment / office / hotel_suite / warehouse / mansion). ONLY
  those families count — an unsupported location word such as "beach" or
  "castle" is NEVER closest-fitted (no mansion for castle); when no family is
  present ``locationTokens`` is empty and ``environmentHint`` is ``None`` so
  the environment resolver falls back to the documented default kit. The
  FIRST family keyword in document order wins; ``locationTokens`` records the
  matched tokens of the winning family.
- **objects**: bounded keyword extraction from ``KNOWN_OBJECT_TABLE`` (canonical
  names from the 101-object catalog + the four Phase 13 procedural fixtures).
  Trigger phrases match the NFKC-casefolded prompt at word boundaries as a
  word span with at most ``TRIGGER_GAP_MAX`` intervening words (so "antique
  ceremonial letter opener" hits the trigger "antique letter opener").
  Overlapping/shorter triggers inside an already-matched phrase are skipped
  deterministically. UNKNOWN nouns are IGNORED — the extractor never invents
  an asset.
- **unsafe terms**: a KNOWN-UNSAFE request (``UNSAFE_OBJECT_TERMS``, e.g.
  "bomb"/"gun"/"explosive") is recorded as a sanitized ``unsafeUnsupported``
  note and is NOT composed (safe fail — never an arbitrary asset).
- **relations**: bounded phrase matches (exact contiguous phrases, e.g. "on
  the desk" / "near the body") bind their kind to EVERY matched object in the
  same sentence that precedes the phrase (so "a broken bottle and medication
  are near the body" binds both); a phrase with no preceding object in the
  sentence is recorded with an empty target (unbound, never fabricated).

Equal inputs ALWAYS produce equal outputs. The ``locked`` argument (a
``LockedConstraints`` or None) is used ONLY as an additional documented object
trigger surface: a locked ``weapon`` that normalizes to a known table alias
(e.g. "Kitchen knife") emits that known request even when the prompt spells
the weapon differently.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Sequence

from app.generation.constraints import LockedConstraints
from app.world.requirements import (
    ObjectRequest,
    PlacementRelation,
    WorldRequirements,
)

# --------------------------------------------------------------------------- #
# environment families (the ONLY supported location vocabulary)
# --------------------------------------------------------------------------- #

# family environmentId -> alias word list. Documented: "apartment/flat/condo ->
# apartment; office/company/workplace -> office; hotel/room -> hotel_suite;
# warehouse/depot/storage -> warehouse; mansion/villa/manor -> mansion".
ENVIRONMENT_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("apartment", ("apartment", "flat", "condo")),
    ("office", ("office", "company", "workplace")),
    ("hotel_suite", ("hotel", "room", "suite")),
    ("warehouse", ("warehouse", "depot", "storage")),
    ("mansion", ("mansion", "villa", "manor")),
)

# --------------------------------------------------------------------------- #
# known-object table (canonical names from the catalog + the proc fixtures)
# --------------------------------------------------------------------------- #

# The bounded gap (max words allowed between two consecutive trigger words).
TRIGGER_GAP_MAX = 2

# Character classes for the word-boundary span construction.
_WORD_RE = r"[a-z0-9]+"


@dataclass(frozen=True)
class KnownObject:
    """One known-object table entry.

    ``triggers`` are the phrase aliases matched against the prompt (longer
    phrases win deterministically). ``in_base`` marks entries whose resolved
    asset is part of a kit's default (golden) placed set — the composer
    dedupes these against the kit base.
    """

    triggers: tuple[str, ...]
    requested_name: str
    category_hint: str | None = None
    subtype_hint: str | None = None
    tags: tuple[str, ...] = ()
    required_interaction: str | None = None
    evidence_id: str | None = None
    in_base: bool = False

    @property
    def atoms(self) -> tuple[tuple[str, ...], ...]:
        """Casefolded word atoms of every trigger (deterministic)."""
        return tuple(tuple(_phrase_words(phrase)) for phrase in self.triggers)


def _phrase_words(phrase: str) -> list[str]:
    """Word atoms of one trigger phrase ("_" counts as whitespace)."""
    raw = unicodedata.normalize("NFKC", phrase).casefold().replace("_", " ")
    return [w for w in re.split(r"[^a-z0-9]+", raw) if w]


KNOWN_OBJECT_TABLE: tuple[KnownObject, ...] = (
    # -- golden base objects (in_base=True) ---------------------------------
    KnownObject(
        triggers=("kitchen knife", "kitchen_knife", "knife"),
        requested_name="kitchen knife",
        category_hint="evidence",
        subtype_hint="sharp",
        tags=("weapon", "kitchen"),
        required_interaction="inspect",
        evidence_id="forensic_knife_match_01",
        in_base=True,
    ),
    KnownObject(
        triggers=("letter opener", "letter_opener"),
        requested_name="letter opener",
        category_hint="evidence",
        subtype_hint="sharp",
        required_interaction="inspect",
        evidence_id="forensic_letter_opener_01",
        in_base=True,
    ),
    KnownObject(
        triggers=("scissors",),
        requested_name="scissors",
        category_hint="evidence",
        subtype_hint="sharp",
        required_interaction="inspect",
        evidence_id="forensic_scissors_01",
        in_base=True,
    ),
    KnownObject(
        triggers=("laptop",),
        requested_name="laptop",
        category_hint="electronics",
        subtype_hint="computer",
        required_interaction="read",
        evidence_id="email_thomas_01",
        in_base=True,
    ),
    # -- supporting catalog objects (new: additive decorative placements) ----
    KnownObject(
        triggers=("wrench",),
        requested_name="adjustable wrench",
        category_hint="evidence",
        subtype_hint="tool",
    ),
    KnownObject(
        triggers=("rope",),
        requested_name="rope",
        category_hint="evidence",
        subtype_hint="restraint",
    ),
    KnownObject(
        triggers=("bottle", "glass bottle", "broken glass bottle"),
        requested_name="glass bottle",
        category_hint="evidence",
        subtype_hint="container",
    ),
    KnownObject(
        triggers=("medication", "pills"),
        requested_name="medication bottle",
        category_hint="evidence",
        subtype_hint="container",
    ),
    KnownObject(
        triggers=("hammer",),
        requested_name="claw hammer",
        category_hint="evidence",
        subtype_hint="tool",
    ),
    KnownObject(
        triggers=("watch",),
        requested_name="wristwatch",
        category_hint="evidence",
        subtype_hint="personal",
    ),
    KnownObject(
        triggers=("jewelry", "jewellery"),
        requested_name="jewelry box",
        category_hint="evidence",
        subtype_hint="container",
    ),
    KnownObject(
        triggers=("computer", "desktop computer"),
        requested_name="desktop computer",
        category_hint="electronics",
        subtype_hint="computer",
    ),
    # -- procedural fixtures (unknown-but-valid objects) ---------------------
    KnownObject(
        triggers=("trophy", "award", "heavy award"),
        requested_name="Custom Trophy",
        category_hint="decor",
        subtype_hint="trophy",
    ),
    KnownObject(
        triggers=("antique letter opener", "ceremonial letter opener"),
        requested_name="Antique Ceremonial Letter Opener",
        category_hint="decor",
        subtype_hint="ceremonial_letter_opener",
    ),
    KnownObject(
        triggers=("sample rack", "laboratory sample rack"),
        requested_name="Unusual Laboratory Sample Rack",
        category_hint="utility",
        subtype_hint="sample_rack",
    ),
    KnownObject(
        triggers=("desk award",),
        requested_name="Distinctive Desk Award",
        category_hint="decor",
        subtype_hint="desk_award",
    ),
)


def is_base_object_request(requested_name: str) -> bool:
    """True when ``requested_name`` is the canonical name of a table entry
    whose resolved asset belongs to the per-kit base (golden) placed set."""
    for entry in KNOWN_OBJECT_TABLE:
        if entry.requested_name == requested_name:
            return entry.in_base
    return False


# --------------------------------------------------------------------------- #
# bounded relation phrases (documented vocabulary) -> relation kind
# --------------------------------------------------------------------------- #

# (phrase atoms, kind); the exact contiguous phrases from the Phase 14 spec.
_RELATION_PHRASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("on", "the", "desk"), "on_desk"),
    (("on", "the", "table"), "on_table"),
    (("next", "to", "the", "body"), "near_victim"),
    (("near", "the", "body"), "near_victim"),
    (("in", "the", "cabinet"), "inside_cabinet"),
    (("on", "the", "floor"), "floor_area"),
    (("on", "the", "wall"), "on_wall"),
)

# The documented known-unsafe object terms (exported + pinned by tests).
UNSAFE_OBJECT_TERMS: tuple[str, ...] = (
    "bomb",
    "gun",
    "explosive",
    "rifle",
    "pistol",
    "grenade",
    "dynamite",
    "shotgun",
    "revolver",
)

# Sentence delimiters used only to scope relation binding (bounded, plain).
_SENTENCE_SPLIT_RE = re.compile(r"[.;!?\n]+")


# --------------------------------------------------------------------------- #
# span helpers
# --------------------------------------------------------------------------- #


def _phrase_regex(atoms: Sequence[str], gap: int) -> re.Pattern[str]:
    """Anchored word-boundary regex for one trigger phrase (bounded gap).

    Consecutive trigger words may be separated by at most ``gap`` OTHER words:
    ``antique ceremonial letter opener`` matches (gap 1 for ``letter``).
    """
    inner = r"".join(
        r"[^a-z0-9]+(?:" + _WORD_RE + r"[^a-z0-9]+){0,%d}" % gap + words + r"\b"
        for words in atoms[1:]
    )
    return re.compile(r"\b" + atoms[0] + inner)


def _normalized(prompt: str) -> str:
    return unicodedata.normalize("NFKC", prompt).casefold().replace("_", " ")


def _first_span(
    pattern: re.Pattern[str], text: str, start: int = 0
) -> tuple[int, int] | None:
    match = pattern.search(text, start)
    if match is None:
        return None
    return (match.start(), match.end())


def _overlaps(span_a: tuple[int, int], span_b: tuple[int, int]) -> bool:
    return not (span_a[1] <= span_b[0] or span_b[1] <= span_a[0])


def _environment_match(normalized: str) -> tuple[str | None, tuple[str, ...]]:
    """(environment_id | None, matched tokens) of the FIRST family in order."""
    best: tuple[int, str, tuple[str, ...]] | None = None
    for environment_id, aliases in ENVIRONMENT_FAMILIES:
        matches: list[tuple[int, str]] = []
        for alias in aliases:
            for match in re.finditer(r"\b" + re.escape(alias) + r"\b", normalized):
                matches.append((match.start(), alias))
        if not matches:
            continue
        matches.sort()
        if best is None or matches[0][0] < best[0]:
            best = (matches[0][0], environment_id, tuple(alias for _p, alias in matches))
    if best is None:
        return (None, ())
    return (best[1], best[2])


def _locked_weapon_surface(locked: LockedConstraints | None) -> tuple[tuple[str, ...], ...]:
    """Atoms of the locked weapon when it normalizes to a known entry."""
    if locked is None or not isinstance(locked.weapon, str) or not locked.weapon:
        return ()
    atoms = tuple(_phrase_words(locked.weapon))
    if not atoms:
        return ()
    known_atom_sets = {a for entry in KNOWN_OBJECT_TABLE for a in entry.atoms}
    return (atoms,) if atoms in known_atom_sets else ()


def _sentence_of(normalized: str, position: int) -> tuple[int, int]:
    """The [start, end) char span of the sentence containing ``position``."""
    start = 0
    for sentence in _SENTENCE_SPLIT_RE.split(normalized):
        end = start + len(sentence)
        if start <= position < end:
            return (start, end)
        start = end + 1  # +1 accounts for the delimiter character
    return (0, len(normalized))


# --------------------------------------------------------------------------- #
# public extraction
# --------------------------------------------------------------------------- #


def extract_world_requirements(
    prompt: str, locked: LockedConstraints | None = None
) -> WorldRequirements:
    """Deterministically derive ``WorldRequirements`` from one prompt.

    Pure text extraction: environment from the five alias families, objects
    from ``KNOWN_OBJECT_TABLE`` word-span matches, relations from the bounded
    phrase family bound to every preceding matched object in the same
    sentence, and safe-fail notes for ``UNSAFE_OBJECT_TERMS``. Unknown nouns
    are ignored; equal inputs always produce equal outputs.
    """
    if not isinstance(prompt, str):
        prompt = ""
    normalized = _normalized(prompt)
    environment_hint, location_tokens = _environment_match(normalized)

    # 1. unsafe terms -> sanitized safe-fail notes, never composed.
    unsafe_notes: list[str] = []
    matched_unsafe: set[str] = set()
    for term in UNSAFE_OBJECT_TERMS:
        if re.search(r"\b" + re.escape(term) + r"\b", normalized):
            matched_unsafe.add(term)
    for term in sorted(matched_unsafe):
        unsafe_notes.append(
            f"unsafeUnsupported: known-unsafe object term {term!r} was not composed"
        )

    # 2. object table matches. Options are FLATTENED and processed longest-trigger
    #    first GLOBALLY (a longer phrase always beats a shorter one, even when the
    #    shorter belongs to an alphabetically-earlier entry); each entry emits at
    #    most one request and overlapping claims are skipped deterministically.
    objects: list[ObjectRequest] = []
    claims: list[tuple[tuple[int, int], str]] = []  # (char span, requested name)
    locked_surface = _locked_weapon_surface(locked)

    def _try_span(atoms: Sequence[str], start: int) -> tuple[int, int] | None:
        span = _first_span(_phrase_regex(atoms, TRIGGER_GAP_MAX), normalized, start)
        if span is None:
            return None
        if any(_overlaps(span, other) for other, _name in claims):
            return None
        return span

    emitted: dict[str, ObjectRequest] = {}
    options: list[tuple[int, Any, Sequence[str]]] = []
    for entry in sorted(
        KNOWN_OBJECT_TABLE, key=lambda k: k.requested_name
    ):
        for atoms in entry.atoms:
            options.append((len(atoms), entry, atoms))
    for atoms in locked_surface:
        # locked-surface trigger only refines its matching known entry
        for entry in KNOWN_OBJECT_TABLE:
            if atoms in entry.atoms:
                options.append((len(atoms), entry, atoms))
                break
    options.sort(key=lambda option: (-option[0], option[1].requested_name))

    for _atoms_len, entry, atoms in options:
        if entry.requested_name in emitted:
            continue
        span = _try_span(atoms, 0)
        if span is None:
            continue
        claims.append((span, entry.requested_name))
        emitted[entry.requested_name] = ObjectRequest(
            requested_name=entry.requested_name,
            category_hint=entry.category_hint,
            subtype_hint=entry.subtype_hint,
            tags=entry.tags,
            required_interaction=entry.required_interaction,
            evidence_id=entry.evidence_id,
        )

    # Locked-weapon guarantee: when the locked constraints pin a KNOWN object
    # (e.g. the kitchen knife), that request is ALWAYS part of the world even
    # if the prompt spells the weapon differently (deterministic, safe — the
    # entry must be table-known; never an invented asset).
    if locked_surface:
        for entry in KNOWN_OBJECT_TABLE:
            if any(atoms in entry.atoms for atoms in locked_surface):
                if entry.requested_name not in emitted:
                    emitted[entry.requested_name] = ObjectRequest(
                        requested_name=entry.requested_name,
                        category_hint=entry.category_hint,
                        subtype_hint=entry.subtype_hint,
                        tags=entry.tags,
                        required_interaction=entry.required_interaction,
                        evidence_id=entry.evidence_id,
                    )
                break
    claims.sort(key=lambda pair: pair[0])
    objects = list(emitted.values())

    # 3. relations: exact contiguous phrase matches bound to every matched
    #    object in the same sentence that precedes the phrase.
    relations: list[PlacementRelation] = []
    for atoms, kind in _RELATION_PHRASES:
        pattern = _phrase_regex(atoms, 0)
        for match in pattern.finditer(normalized):
            phrase_start, phrase_end = match.start(), match.end()
            if any(_overlaps((phrase_start, phrase_end), other) for other, _n in claims):
                continue
            sentence_start, sentence_end = _sentence_of(normalized, phrase_start)
            # Every matched object in the SAME sentence whose span begins
            # before the phrase is bound to the relation.
            bound = sorted(
                {
                    name
                    for (other_start, other_end), name in claims
                    if sentence_start <= other_start < phrase_start
                }
            )
            if not bound:
                relations.append(PlacementRelation(kind=kind, target=""))
            else:
                for name in bound:
                    relations.append(PlacementRelation(kind=kind, target=name.casefold()))

    return WorldRequirements(
        environment_hint=environment_hint,
        location_tokens=location_tokens,
        objects=tuple(objects),
        relations=tuple(sorted(set(relations), key=lambda r: (r.kind, r.target))),
        unsafe_unsupported=tuple(unsafe_notes),
    )


__all__ = [
    "ENVIRONMENT_FAMILIES",
    "KNOWN_OBJECT_TABLE",
    "TRIGGER_GAP_MAX",
    "UNSAFE_OBJECT_TERMS",
    "extract_world_requirements",
    "is_base_object_request",
]