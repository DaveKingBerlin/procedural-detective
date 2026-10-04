"""Deterministic Asset Oracle resolver (Phase 10 + Phase 26C5 fallback).

Resolution order (strict, deterministic for equal inputs + catalog version,
independent of dict ordering):

1. **EXACT** — ``requestedName`` equals an ``assetId`` (case-sensitive logical
   id)            -> provenance ``CATALOG_EXACT``
2. **CANONICAL** — ``normalize(requestedName) == normalize(canonicalName)``
                   -> provenance ``CATALOG_EXACT`` (roadmap: canonical names and
                   exact ids both report CATALOG_EXACT; only aliases report
                   CATALOG_ALIAS)
3. **ALIAS**     — ``normalize(requestedName)`` matches a normalized alias
                   -> provenance ``CATALOG_ALIAS`` (``matchedAlias`` is the
                   verbatim alias that matched)
4. **SEMANTIC**  — explicit scoring over ``categoryHint`` / ``subtypeHint`` /
                   ``tags``:

                   * +3.0 for every normalized tag of the request present in
                     the candidate's normalized tags,
                   * +2.0 when the normalized categoryHint equals the
                     candidate's normalized category,
                   * +1.0 when the normalized subtypeHint equals the
                     candidate's normalized subtype,
                   * confidence = total score; the documented minimum
                     confidence threshold is ``SEMANTIC_MIN_CONFIDENCE = 3.0``.
                   Candidates below threshold are discarded. A provided
                   ``requiredInteraction`` or non-empty
                   ``requiredEvidenceCapabilities`` further FILTER the
                   candidates (an asset that cannot support the requested
                   interaction/capability is not a valid candidate).

A UNIQUE candidate at the maximum score (>= threshold)
                    -> SEMANTIC_MATCH with that confidence — UNLESS the
                    C5-02 category-consistency gate rejects it: when the
                    request phrase's inferred semantic category exists and has
                    a trusted fallback chain AND the unique winner is NOT a
                    member of that chain (it contradicts the inferred class),
                    the SEMANTIC_MATCH is not category-safe and the request
                    falls through to steps 5-6 (normalized exact -> trusted
                    category fallback -> fail closed). A TIE at the maximum
                    score -> an AMBIGUOUS result with ``ambiguous=True`` and
                    the deterministic tied candidate list (score desc,
                    assetId asc) — NEVER an arbitrary winner (the ambiguity is
                    returned AS-IS only when the phrase resolves through no
                    safer trusted path; see step 6).

5. **NORMALIZED-EXACT** (Phase 26C5 §6) — deterministic phrase canonicalization:
   lower-case + punctuation/whitespace normalization, possessive handling,
   stripping of known material/color/style modifiers ("bronze ceremonial ice
   pick" -> "ice pick", "silver kitchen knife" -> "kitchen knife"), careful
   LIGHT singularization. When the REDUCED phrase identity-matches a catalog
   canonical name or alias, the request resolves to that trusted asset with
   provenance ``NORMALIZED_EXACT`` (same asset class, never an arbitrary id).
6. **CATEGORY FALLBACK** (Phase 26C5 §7-§10) — a bounded semantic category
   taxonomy backed exclusively by real catalog assets:

   * the reduced phrase is looked up in the trusted phrase -> category table
     (``SEMANTIC_PHRASE_CATEGORIES``) and then in a bounded noun keyword map
     (``SEMANTIC_KEYWORD_CATEGORIES``);
   * a safe category selects its trusted fallback chain
     (``SEMANTIC_CATEGORY_FALLBACK_ASSETS``) -> provenance ``CATEGORY_FALLBACK``
     with ``resolution_category`` set;
   * when the category is known but no chain member is usable, a category-safe
     GENERIC representation (``SEMANTIC_CATEGORY_GENERIC``) may apply
     -> provenance ``GENERIC_FALLBACK``; explicit "generic prop"-class phrases
     resolve with ``GENERIC_FALLBACK`` too;
   * ``firearm`` / ``explosive`` have NO safe fallback -> the request FAILS
     CLOSED with provenance ``UNRESOLVED`` (resolved=False) — never a wrong
     substitute (a gun must never render as a knife or a vase).
7. **FALLBACK / UNRESOLVED (terminal)** — every OTHER safe request resolves to
   the catalog's NEUTRAL ``fallbackAsset`` with provenance ``FALLBACK``
   (explicit, never silent nonsense; the caller may escalate to the bounded
   procedural asset-spec provider exactly as before). Requests whose category
   is inferred but unrepresentable (firearm/explosive, or interaction/cap -
   ability filters that strip every chain member AND the generic) end
   UNRESOLVED (fail closed).

The semantic game object and the rendered asset are NOT required to be
textually identical (Phase 26C5 §4): the resolver never changes the request's
display label — it only selects a trusted VISUAL representation. Asset ids are
NEVER caller-supplied: every fallback value comes from the shipped catalog.

``normalize`` shares the constraints engine's normalization concept
(``normalize_motive_text``: casefold + keep ASCII letters/digits/currency
symbols, drop everything else) and adds an NFKD decomposition pre-step
(``app.assets.catalog.normalize_asset_text``) so accented lookalikes of ASCII
letters match their base letter. "KITCHEN KNIFE", "Kïtchen knife" and
"kitchen_knife" are the same token. The typed entry point is
``AssetResolver.resolve_request``; ``resolve`` additionally gates RAW payloads
through ``app.assets.validation`` before parsing them. Phase 12 adds the
bounded parametric-variant entry points (``apply_variant`` /
``resolve_with_variant``) which validate declarative variant params and emit
the ``PARAMETRIC_VARIANT`` provenance.
"""

from __future__ import annotations

import dataclasses
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from app.assets.catalog import (
    Catalog,
    CatalogError,
    load_catalog_from_repo,
    normalize_asset_text,
)
from app.assets.validation import (
    AssetRequestValidationError,
    validate_asset_request,
)

# Documented semantic scoring weights / threshold (see module docstring).
SEMANTIC_TAG_WEIGHT = 3.0
SEMANTIC_CATEGORY_WEIGHT = 2.0
SEMANTIC_SUBTYPE_WEIGHT = 1.0
SEMANTIC_MIN_CONFIDENCE = 3.0

# Reserved provenance values (PARAMETRIC_VARIANT, PROCEDURAL_GENERATED,
# STATIC_GENERATED) exist so callers can pattern-match stable enum values
# across the roadmap. Phase 10 never emits them; Phase 12 emits
# PARAMETRIC_VARIANT through ``resolve_with_variant``.


class Provenance(Enum):
    """How an asset resolution was reached (Phase 10 + Phase 26C5 values).

    Closed enum, never free text (Phase 26C5 §14). ``NORMALIZED_EXACT`` is a
    match found AFTER the deterministic §6 phrase canonicalization;
    ``CATEGORY_FALLBACK`` / ``GENERIC_FALLBACK`` are the trusted §7-§10
    taxonomy resolutions; ``UNRESOLVED`` is the fail-closed terminal for a
    request with NO safe representation (firearm/explosive classes, or a class
    whose every candidate is filtered out).
    """

    CATALOG_EXACT = "CATALOG_EXACT"
    CATALOG_ALIAS = "CATALOG_ALIAS"
    SEMANTIC_MATCH = "SEMANTIC_MATCH"
    PARAMETRIC_VARIANT = "PARAMETRIC_VARIANT"
    PROCEDURAL_GENERATED = "PROCEDURAL_GENERATED"
    STATIC_GENERATED = "STATIC_GENERATED"
    FALLBACK = "FALLBACK"
    NORMALIZED_EXACT = "NORMALIZED_EXACT"
    CATEGORY_FALLBACK = "CATEGORY_FALLBACK"
    GENERIC_FALLBACK = "GENERIC_FALLBACK"
    UNRESOLVED = "UNRESOLVED"


class AssetVariantError(ValueError):
    """A variant request/parameter failed the Phase 12 bounded-variant rules.

    Raised by ``apply_variant`` / ``resolve_with_variant`` for unknown variant
    parameters, values outside the declared allowlists, non-numeric or
    non-finite (NaN/±Inf) scale values, or variant attempts against assets/
    resolutions that declare no variants (fallback / ambiguous / non-composite
    assets).
    """


# Phase 12 — the frozen primary-tone preference used to merge a variant color
# override into an asset's colors map (mirrors the frontend REGISTRY's
# PRIMARY_COLOR_KEYS so the merged view is deterministic and contract-stable).
_PRIMARY_COLOR_KEYS: tuple[str, ...] = (
    "shade",
    "base",
    "blade",
    "blades",
    "top",
    "body",
)

# --------------------------------------------------------------------------- #
# Phase 26C5 — bounded semantic fallback taxonomy (trusted catalog only).
# --------------------------------------------------------------------------- #
#
# Every asset id below is drawn verbatim from the SHIPPED catalog manifest
# (``assets/catalog/catalog.json``). A chain member that is absent from a
# particular ``Catalog`` is skipped deterministically. The tables are the
# canonical data structures for §6 normalization, §7 trusted aliases, §8/§9
# category fallback and §10 generic fallback — there are NO scattered
# conditionals and NO caller-supplied asset ids anywhere in this path.
#
# The values are also the "resolution category" tokens exposed in the
# observability fields (safe, bounded, non-secret).

# §6 — deterministic phrase modifiers stripped by semantic canonicalization.
# These are MATERIAL / COLOR / STYLE adjectives only. Only tokens inside this
# bounded set are removed; every other word is preserved so the reducer NEVER
# over-normalizes into an unrelated object ("letter opener" survives whole).
SEMANTIC_MODIFIER_TOKENS: frozenset[str] = frozenset(
    {
        # materials
        "bronze", "brass", "copper", "iron", "steel", "stainless", "silver",
        "gold", "golden", "wooden", "wood", "leather", "glass", "ceramic",
        "plastic", "marble", "granite", "stone", "ivory", "bone", "oak",
        "mahogany", "metal", "paper", "cardboard", "wool", "silk", "velvet",
        "crystal", "porcelain", "china", "clay", "chrome", "nickel",
        "titanium", "ebony", "walnut", "rubber",
        # colors
        "black", "white", "red", "blue", "green", "yellow", "purple",
        "orange", "pink", "brown", "grey", "gray", "beige", "crimson",
        "scarlet", "navy", "teal", "maroon", "blonde", "blond",
        # decorative / style adjectives
        "ceremonial", "antique", "ancient", "old", "vintage", "ornate",
        "decorative", "elegant", "fancy", "engraved", "carved", "rusty",
        "rusted", "tarnished", "polished", "dirty", "dusty", "traditional",
        "modern", "custom", "distinctive", "unusual", "mysterious", "strange",
        "peculiar", "rare", "valuable", "precious", "ornamental", "plain",
        "simple", "heavy", "lightweight", "hollow",
    }
)

# §7 — bounded trusted phrase -> category table (keys are IDENTITY-normalized
# via ``normalize``: ``"ice pick"`` / ``"ice-pick"`` / ``"Ice Pick"`` all key
# to ``"icepick"``). Values are the bounded semantic category tokens of the
# taxonomy; they NEVER point at raw asset ids.
SEMANTIC_PHRASE_CATEGORIES: Mapping[str, str] = {
    # stabbing / sharp weapons (no ``PROP_ICE_PICK_01`` exists in the catalog —
    # the category chain §9 selects the approved sharp prop).
    "icepick": "stabbing_weapon",
    "letteropener": "stabbing_weapon",
    "ceremonialknife": "stabbing_weapon",
    "kitchenknife": "stabbing_weapon",
    "chefsknife": "stabbing_weapon",
    "chefknife": "stabbing_weapon",
    "butcherknife": "stabbing_weapon",
    "carvingknife": "stabbing_weapon",
    "breadknife": "stabbing_weapon",
    "metalspike": "stabbing_weapon",
    "steelspike": "stabbing_weapon",
    "dagger": "stabbing_weapon",
    "stiletto": "stabbing_weapon",
    "switchblade": "stabbing_weapon",
    # blunt weapons
    "tireiron": "blunt_weapon",
    "crowbar": "blunt_weapon",
    "nightstick": "blunt_weapon",
    "hatchet": "blunt_weapon",
    # documents
    "documentfolder": "document",
    "researchpapers": "document",
    "researchpaper": "document",
    "investigationfile": "document",
    "paperwork": "document",
    "dossier": "document",
    # containers
    "suitcase": "container",
    "briefcase": "container",
    "strongbox": "container",
    # explicit generic-prop class (§10 generic placeholder)
    "genericprop": "generic_prop",
    "genericitem": "generic_prop",
    "genericobject": "generic_prop",
    "miscellaneousprop": "generic_prop",
    "miscellaneousitem": "generic_prop",
    "miscellaneousobject": "generic_prop",
    "randomprop": "generic_prop",
}

# §8 — bounded noun keyword -> category map (secondary inference used when the
# exact phrase table misses). DANGEROUS classes are checked FIRST and always
# fail closed (a "gun"-word can never fall through to a knife).
SEMANTIC_KEYWORD_CATEGORIES: Mapping[str, str] = {
    # stabbing / pointed weapons
    "knife": "stabbing_weapon", "knives": "stabbing_weapon",
    "dagger": "stabbing_weapon", "daggers": "stabbing_weapon",
    "stiletto": "stabbing_weapon", "switchblade": "stabbing_weapon",
    "sword": "stabbing_weapon", "swords": "stabbing_weapon",
    "blade": "stabbing_weapon", "blades": "stabbing_weapon",
    "spike": "stabbing_weapon", "spikes": "stabbing_weapon",
    "scalpel": "stabbing_weapon", "cleaver": "stabbing_weapon",
    "machete": "stabbing_weapon", "bayonet": "stabbing_weapon",
    "rapier": "stabbing_weapon", "shiv": "stabbing_weapon",
    "skewer": "stabbing_weapon", "awl": "stabbing_weapon",
    # blunt / impact weapons
    "hammer": "blunt_weapon", "wrench": "blunt_weapon",
    "crowbar": "blunt_weapon", "pipe": "blunt_weapon",
    "tireiron": "blunt_weapon", "nightstick": "blunt_weapon",
    "truncheon": "blunt_weapon", "club": "blunt_weapon",
    "axe": "blunt_weapon", "hatchet": "blunt_weapon",
    # firearm — NO safe catalog representation
    "gun": "firearm", "guns": "firearm", "pistol": "firearm",
    "pistols": "firearm", "rifle": "firearm", "rifles": "firearm",
    "shotgun": "firearm", "shotguns": "firearm", "revolver": "firearm",
    "handgun": "firearm", "firearm": "firearm", "firearms": "firearm",
    "musket": "firearm", "carbine": "firearm",
    # explosive — NO safe catalog representation
    "bomb": "explosive", "bombs": "explosive", "grenade": "explosive",
    "grenades": "explosive", "dynamite": "explosive",
    "explosive": "explosive", "explosives": "explosive",
    # tools (the evidence/tool family has real assets)
    "chisel": "tool", "drill": "tool", "pliers": "tool",
    "shovel": "tool", "spade": "tool", "pickaxe": "tool",
    # documents
    "document": "document", "documents": "document", "papers": "document",
    "paperwork": "document", "report": "document", "reports": "document",
    "dossier": "document", "resume": "document", "contract": "document",
    "contracts": "document", "will": "document", "affidavit": "document",
    "certificate": "document", "deed": "document", "diary": "document",
    # containers
    "briefcase": "container", "suitcase": "container", "chest": "container",
    "trunk": "container", "box": "container", "boxes": "container",
    "carton": "container", "bucket": "container", "hamper": "container",
    "sack": "container", "pouch": "container",
    # electronic devices
    "charger": "electronic_device", "headphones": "electronic_device",
    "earbuds": "electronic_device", "speaker": "electronic_device",
    "microphone": "electronic_device", "drone": "electronic_device",
    "smartwatch": "electronic_device", "harddrive": "electronic_device",
    # furniture
    "dresser": "furniture", "wardrobe": "furniture", "armoire": "furniture",
    "hutch": "furniture", "ottoman": "furniture", "stool": "furniture",
    "bench": "furniture", "vanity": "furniture", "bureau": "furniture",
    # glass objects
    "goblet": "glass_object", "tumbler": "glass_object",
    "crystal": "glass_object", "mirror": "glass_object",
    # personal items
    "ring": "personal_item", "rings": "personal_item",
    "necklace": "personal_item", "bracelet": "personal_item",
    "earring": "personal_item", "earrings": "personal_item",
    "scarf": "personal_item", "hat": "personal_item", "umbrella": "personal_item",
    "lipstick": "personal_item", "comb": "personal_item", "brush": "personal_item",
    "perfume": "personal_item", "cologne": "personal_item", "locket": "personal_item",
    "spectacles": "personal_item",
    # generic props (safe last-resort class)
    "knickknack": "generic_prop", "knickknacks": "generic_prop",
    "curio": "generic_prop", "curios": "generic_prop",
    "trinket": "generic_prop", "trinkets": "generic_prop",
    "tchotchke": "generic_prop", "bricabrac": "generic_prop",
}

# The deterministic category priority (first match by this order wins when a
# phrase matches several SAFE categories; dangerous classes already short-
# circuit to UNRESOLVED before this ordering applies).
SEMANTIC_CATEGORY_PRIORITY: tuple[str, ...] = (
    "stabbing_weapon",
    "blunt_weapon",
    "tool",
    "document",
    "container",
    "electronic_device",
    "furniture",
    "glass_object",
    "personal_item",
    "generic_prop",
)

# Categories with NO safe representation in the shipped catalog. A request
# inferred into one of these FAILS CLOSED (UNRESOLVED) — never substituted.
SEMANTIC_UNRESOLVED_CATEGORIES: frozenset[str] = frozenset(
    {"firearm", "explosive"}
)

# §9 — category -> trusted fallback chain (preferred asset FIRST; every member
# is a real catalog assetId from ``assets/catalog/catalog.json``).
SEMANTIC_CATEGORY_FALLBACK_ASSETS: Mapping[str, tuple[str, ...]] = {
    "stabbing_weapon": (
        "PROP_KITCHEN_KNIFE_01",
        "PROP_BREAD_KNIFE_01",
        "PROP_LETTER_OPENER_01",
        "PROP_SCISSORS_01",
    ),
    "blunt_weapon": (
        "PROP_WRENCH_01",
        "PROP_HAMMER_01",
        "PROP_BASEBALL_BAT_01",
    ),
    "tool": (
        "PROP_SCREWDRIVER_01",
        "PROP_HAMMER_01",
        "PROP_WRENCH_01",
        "PROP_KEY_01",
    ),
    "document": (
        "PROP_FOLDER_01",
        "PROP_CONTRACT_01",
        "PROP_LETTER_01",
        "PROP_NOTEBOOK_01",
        "PROP_INVOICE_01",
        "PROP_RECEIPT_01",
        "PROP_BANK_STATEMENT_01",
        "PROP_TICKET_01",
        "PROP_ID_CARD_01",
    ),
    "container": (
        "PROP_STORAGE_BOX_01",
        "PROP_JEWELRY_BOX_01",
        "PROP_GLASS_BOTTLE_01",
        "PROP_MEDICATION_BOTTLE_01",
        "PROP_WATER_BOTTLE_01",
        "PROP_CUP_01",
        "PROP_VASE_01",
    ),
    "electronic_device": (
        "PROP_TABLET_01",
        "PROP_PHONE_01",
        "PROP_LAPTOP_01",
        "PROP_CAMERA_01",
        "PROP_DESKTOP_MONITOR_01",
        "PROP_USB_STICK_01",
        "PROP_PRINTER_01",
        "PROP_ROUTER_01",
        "PROP_TV_01",
    ),
    "furniture": (
        "PROP_TABLE_01",
        "PROP_CHAIR_01",
        "PROP_DESK_01",
        "PROP_COFFEE_TABLE_01",
        "PROP_BEDSIDE_TABLE_01",
        "PROP_BOOKSHELF_01",
        "PROP_CABINET_01",
        "PROP_SOFA_01",
    ),
    "glass_object": (
        "PROP_CUP_01",
        "PROP_PLATE_01",
        "PROP_WATER_BOTTLE_01",
        "PROP_GLASS_BOTTLE_01",
        "PROP_VASE_01",
    ),
    "personal_item": (
        "PROP_WALLET_01",
        "PROP_WATCH_01",
        "PROP_HANDBAG_01",
        "PROP_GLOVE_01",
        "PROP_COAT_01",
        "PROP_PEN_01",
    ),
    "generic_prop": (
        "PROP_BOOK_01",
        "PROP_PEN_01",
        "PROP_CUP_01",
    ),
}

# §10 — category-safe GENERIC representation (used ONLY when the category is
# inferred but every chain member is unavailable or filtered out by a
# requiredInteraction / requiredEvidenceCapability).
SEMANTIC_CATEGORY_GENERIC: Mapping[str, str] = {
    "stabbing_weapon": "PROP_LETTER_OPENER_01",
    "blunt_weapon": "PROP_BASEBALL_BAT_01",
    "tool": "PROP_KEY_01",
    "document": "PROP_FOLDER_01",
    "container": "PROP_VASE_01",
    "electronic_device": "PROP_USB_STICK_01",
    "furniture": "PROP_CHAIR_01",
    "glass_object": "PROP_VASE_01",
    "personal_item": "PROP_PEN_01",
    "generic_prop": "PROP_BOOK_01",
}

# Documented fallback depth (Phase 26C5 §13 observability): 0 = not a
# fallback (exact/canonical/alias), 1 = normalized exact, 2 = category
# fallback, 3 = generic fallback, 4 = neutral fallback / unresolved terminal.
FALLBACK_DEPTH_BASE = 0
FALLBACK_DEPTH_NORMALIZED_EXACT = 1
FALLBACK_DEPTH_CATEGORY = 2
FALLBACK_DEPTH_GENERIC = 3
FALLBACK_DEPTH_TERMINAL = 4


def semantic_phrase_tokens(text: str) -> tuple[str, ...]:
    """Casefolded, punctuation/whitespace-normalized word tokens of a phrase.

    Handles possessive apostrophes ("chef's knife" -> "chef", "knife") so the
    reduced phrase can match the catalog alias "chef knife". Deterministic and
    bounded — this is the §6 tokenizer (never any dictionary of arbitrary
    nouns, never any generated code).
    """
    cleaned = re.sub(r"(?i)'s\b", "", str(text))
    return tuple(
        token
        for token in re.findall(r"[a-z0-9]+", cleaned.casefold())
        if token
    )


def semantic_phrase_reduce(text: str) -> str:
    """§6 canonical phrase reduction: the phrase with every material/color/
    style modifier token stripped ("bronze ceremonial ice pick" -> "ice pick").

    ``""`` when nothing but modifiers remains (the caller then skips the
    semantic-canonicalization step; the neutral fallback still applies).
    """
    kept = [
        token
        for token in semantic_phrase_tokens(text)
        if token not in SEMANTIC_MODIFIER_TOKENS
    ]
    return " ".join(kept)


def semantic_candidate_phrases(text: str) -> tuple[str, ...]:
    """The ordered phrase canonicals tried by step 5/6 (most specific first):

    1. the fully cleaned, possessive-normalized phrase ("tire iron"),
    2. the §6 modifier-reduced phrase ("ice pick"),
    3. the modifier-reduced phrase with a LIGHT singularization attempt
       ("papers" -> "paper") used only as an additional lookup key (never as a
       display value).

    Deterministic and bounded; equal inputs always produce equal outputs.
    """
    tokens = semantic_phrase_tokens(text)
    cleaned = " ".join(tokens) if tokens else ""
    reduced = semantic_phrase_reduce(text)
    candidates = [cleaned, reduced] if reduced and reduced != cleaned else [cleaned or reduced]
    singular = _light_singularize(reduced) if reduced else ""
    if singular and singular not in candidates:
        candidates.append(singular)
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return tuple(ordered)


def _light_singularize(phrase: str) -> str:
    """Careful, bounded singularization used ONLY as an extra lookup key.

    Strips a trailing ``s`` from the LAST word when the word is a plain plural
    (length >= 4, not ending in ``ss``/``sh``/``ch``/``x``/``z`` and not the
    preserved invariants ``scissors``/``glasses``). "research papers" ->
    "research paper"; "ice picks" -> "ice pick". This is opt-in (the plural key
    is always checked FIRST) so it can never over-normalize a lookup.
    """
    tokens = phrase.split()
    if not tokens:
        return ""
    word = tokens[-1]
    if (
        len(word) >= 4
        and word.endswith("s")
        and not word.endswith(("ss", "sh", "ch", "x", "z"))
        and word not in ("scissors", "glasses")
    ):
        return " ".join([*tokens[:-1], word[:-1]])
    return ""


@dataclass(frozen=True)
class AssetVariantView:
    """The frozen, bounded view of one parametric variant (Phase 12 §Variant).

    Produced by ``apply_variant`` (and held by ``AssetVariantResolution``):
    ``colors`` is the asset's base colors MERGED with the applied color param
    (the primary tone key — the frontend's preferred key, else the first key —
    carries the applied hex, so the multi-tone object keeps its parts), and
    ``scale`` is the applied scale CLAMPED into the asset's declared [min,max]
    (it can never escape the declared bounds). ``material``/``state`` are the
    applied (or defaulted) frozen safe tokens; None when the asset declares no
    such variant parameter.
    """

    asset_id: str
    template_id: str | None
    colors: Mapping[str, str]
    scale: float
    material: str | None
    state: str | None


@dataclass(frozen=True)
class AssetVariantResolution:
    """A resolution that additionally applied a validated parametric variant.

    ``provenance`` is always ``PARAMETRIC_VARIANT`` (reserved Phase 12+
    provenance; emitted by ``resolve_with_variant``). The underlying asset was
    first resolved through the normal catalog order (exact/canonical/alias/
    semantic); the view carries the clamped, allowlist-validated variant.
    """

    asset_id: str
    catalog_version: int
    provenance: Provenance
    version: int
    view: AssetVariantView
    resolved: bool


@dataclass(frozen=True)
class AssetRequest:
    """A typed, trusted semantic asset request (Phase 10 AssetRequest)."""

    requested_name: str
    category_hint: str | None = None
    subtype_hint: str | None = None
    tags: tuple[str, ...] = ()
    required_interaction: str | None = None
    required_evidence_capabilities: tuple[str, ...] = ()
    style_hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssetResolution:
    """The deterministic outcome of one asset request.

    ``resolved`` is True whenever a concrete asset was chosen — including the
    explicit fallback (``provenance == FALLBACK``) and the trusted Phase 26C5
    category/generic fallbacks. The only False cases are an ambiguous semantic
    match (NO arbitrary winner is chosen, the qualified candidates are
    returned instead) and the fail-closed ``UNRESOLVED`` terminal.

    ``confidence`` is the semantic score (float) for SEMANTIC_MATCH, the top
    (tied) score for an ambiguous result, and None otherwise.

    Phase 26C5 observability fields (never player-exposed, never secret):
    ``normalized_object_id`` is the §6 reduced phrase ("ice pick"),
    ``resolution_category`` the bounded taxonomy token, ``resolution_step`` a
    stable step name and ``fallback_depth`` the documented fallback depth.
    """

    asset_id: str
    catalog_version: int
    provenance: Provenance
    version: int
    confidence: float | None
    ambiguous: bool
    candidates: tuple[str, ...]
    matched_alias: str | None
    resolved: bool
    normalized_object_id: str | None = None
    resolution_category: str | None = None
    resolution_step: str | None = None
    fallback_depth: int = 0


def normalize(text: str) -> str:
    """Deterministic canonical matching form for names/aliases/vocabularies.

    The single Asset Oracle identity normalization (see
    ``app.assets.catalog.normalize_asset_text``): NFKD decomposition, then the
    constraints engine's concept (casefold + keep ASCII letters/digits/currency
    symbols). "KITCHEN KNIFE" / "Kïtchen knife" / "kitchen_knife" all normalize
    to ``kitchenknife``. Non-strings normalize to the empty string.
    """
    return normalize_asset_text(text)


class AssetResolver:
    """Stateless-per-catalog deterministic resolver."""

    def __init__(self, catalog: Catalog) -> None:
        if not isinstance(catalog, Catalog):
            raise TypeError("AssetResolver requires a Catalog")
        self._catalog = catalog
        self._by_id: dict[str, Any] = {a.asset_id: a for a in catalog.assets}
        # Normalized canonical names -> descriptor. Name collisions are
        # rejected at catalog load, so each normalized key maps to one asset.
        self._canonicals: dict[str, Any] = {
            normalize(a.canonical_name): a for a in catalog.assets
        }
        self._aliases: dict[str, tuple[Any, str]] = {}
        for asset in catalog.assets:
            for alias in asset.aliases:
                self._aliases.setdefault(normalize(alias), (asset, alias))
        if catalog.fallback_asset not in self._by_id:
            raise CatalogError(
                f"catalog fallbackAsset {catalog.fallback_asset!r} is not a "
                "declared assetId"
            )

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #

    def resolve_request(self, request: AssetRequest) -> AssetResolution:
        """Resolve one TYPED request (caller already validated raw input)."""
        if not isinstance(request, AssetRequest):
            raise TypeError("resolve_request requires an AssetRequest")
        name = request.requested_name

        # 1. EXACT logical id (case-sensitive).
        descriptor = self._by_id.get(name)
        if descriptor is not None:
            return self._exact(descriptor)

        norm = normalize(name)

        # 2. canonical name (normalized equality) -> CATALOG_EXACT.
        descriptor = self._canonicals.get(norm)
        if descriptor is not None:
            return self._exact(descriptor)

        # 3. alias -> CATALOG_ALIAS with the verbatim matched alias.
        alias_hit = self._aliases.get(norm)
        if alias_hit is not None:
            descriptor, raw_alias = alias_hit
            return AssetResolution(
                asset_id=descriptor.asset_id,
                catalog_version=self._catalog.catalog_version,
                provenance=Provenance.CATALOG_ALIAS,
                version=descriptor.version,
                confidence=None,
                ambiguous=False,
                candidates=(),
                matched_alias=raw_alias,
                resolved=True,
                resolution_step="alias",
            )

        # 4. semantic scoring (explicit rule, min confidence threshold).
        #    Winner = the UNIQUE candidate with the MAXIMUM score at/above the
        #    threshold; candidates tied at the maximum yield AMBIGUOUS (never
        #    an arbitrary winner). Lower-scoring qualifiers do not create
        #    ambiguity against a strictly better asset.
        #    Phase 26C5: an ambiguous tie is NOT automatically a dead end — it
        #    is held here and resolved through the trusted normalization /
        #    category path (steps 5-6) whenever that path yields a SAFE
        #    representation; only a request with NO trusted fallback returns
        #    the honest ambiguous result.
        #    Phase 26C5 Fix-C (C5-02): a UNIQUE semantic winner is further
        #    subject to the category-consistency gate — a winner whose semantic
        #    class contradicts the request phrase's inferred category is NOT
        #    category-safe and falls through to steps 5-6 instead of being
        #    returned (e.g. ``bronze ceremonial ice pick`` + weapon/restraint
        #    tags uniquely wins on PROP_ROPE_01 solely because rope owns both
        #    tags — a materially wrong puzzle-critical asset). See
        #    ``_semantic_match_is_category_safe``.
        semantic_result: AssetResolution | None = None
        scored = self._semantic_candidates(request)
        if scored:
            top_score = scored[0][1]
            winners = [
                (descriptor, score)
                for descriptor, score in scored
                if score == top_score
            ]
            if len(winners) == 1:
                descriptor, score = winners[0]
                if self._semantic_match_is_category_safe(
                    request, name, descriptor.asset_id
                ):
                    return AssetResolution(
                        asset_id=descriptor.asset_id,
                        catalog_version=self._catalog.catalog_version,
                        provenance=Provenance.SEMANTIC_MATCH,
                        version=descriptor.version,
                        confidence=float(score),
                        ambiguous=False,
                        candidates=(),
                        matched_alias=None,
                        resolved=True,
                        resolution_step="semantic",
                    )
            semantic_result = AssetResolution(
                asset_id="",
                catalog_version=self._catalog.catalog_version,
                provenance=Provenance.SEMANTIC_MATCH,
                version=0,
                confidence=float(top_score),
                ambiguous=True,
                candidates=tuple(
                    descriptor.asset_id for descriptor, _score in winners
                ),
                matched_alias=None,
                resolved=False,
                resolution_step="semantic",
            )

        # 5. SEMANTIC-CANONICALIZATION (Phase 26C5 §6) — deterministic phrase
        #    reduction, then an EXACT canonical/alias match on the REDUCED
        #    phrase -> NORMALIZED_EXACT (the same trusted asset, found through
        #    the normalization layer; the display label is never changed).
        for candidate in semantic_candidate_phrases(name):
            candidate_norm = normalize(candidate)
            descriptor = self._canonicals.get(candidate_norm)
            if descriptor is not None:
                return AssetResolution(
                    asset_id=descriptor.asset_id,
                    catalog_version=self._catalog.catalog_version,
                    provenance=Provenance.NORMALIZED_EXACT,
                    version=descriptor.version,
                    confidence=None,
                    ambiguous=False,
                    candidates=(),
                    matched_alias=None,
                    resolved=True,
                    normalized_object_id=candidate,
                    resolution_step="normalized_exact",
                    fallback_depth=FALLBACK_DEPTH_NORMALIZED_EXACT,
                )
            alias_hit = self._aliases.get(candidate_norm)
            if alias_hit is not None:
                descriptor, raw_alias = alias_hit
                return AssetResolution(
                    asset_id=descriptor.asset_id,
                    catalog_version=self._catalog.catalog_version,
                    provenance=Provenance.NORMALIZED_EXACT,
                    version=descriptor.version,
                    confidence=None,
                    ambiguous=False,
                    candidates=(),
                    matched_alias=raw_alias,
                    resolved=True,
                    normalized_object_id=candidate,
                    resolution_step="normalized_exact",
                    fallback_depth=FALLBACK_DEPTH_NORMALIZED_EXACT,
                )

        # 6. CATEGORY / GENERIC FALLBACK (Phase 26C5 §7-§10) — bounded trusted
        #    taxonomy. Only a SAFE inferred category can convert the request;
        #    an inference into an unrepresentable class fails closed.
        reduced = semantic_phrase_reduce(name)
        inferred = self._infer_semantic_category(name, reduced)
        if inferred is not None:
            category, matched_phrase = inferred
            category_result = self._resolve_category_fallback(
                category, matched_phrase or reduced, request
            )
            if category_result is not None:
                return category_result
            # A known category with NO usable trusted representation: fail
            # closed (never a wrong substitute).
            return AssetResolution(
                asset_id="",
                catalog_version=self._catalog.catalog_version,
                provenance=Provenance.UNRESOLVED,
                version=0,
                confidence=None,
                ambiguous=False,
                candidates=(),
                matched_alias=None,
                resolved=False,
                normalized_object_id=matched_phrase or reduced or None,
                resolution_category=category,
                resolution_step="unresolved",
                fallback_depth=FALLBACK_DEPTH_TERMINAL,
            )

        # 7. terminal — a held semantic ambiguity with no trusted fallback is
        #    the honest answer (never an arbitrary winner); every other safe
        #    unknown is the explicit NEUTRAL fallback (the caller may escalate
        #    to the bounded procedural provider exactly as before).
        if semantic_result is not None:
            return semantic_result
        fallback = self._by_id[self._catalog.fallback_asset]
        return AssetResolution(
            asset_id=fallback.asset_id,
            catalog_version=self._catalog.catalog_version,
            provenance=Provenance.FALLBACK,
            version=fallback.version,
            confidence=None,
            ambiguous=False,
            candidates=(),
            matched_alias=None,
            resolved=True,
            fallback_depth=FALLBACK_DEPTH_TERMINAL,
        )

    def resolve_placements_provenance(
        self, placements: Iterable[Any]
    ) -> dict[str, str]:
        """Diagnostic: objectId -> provenance for a placements iterable.

        Accepts dataclass placements (``object_id`` / ``asset_id`` attributes,
        e.g. ``app.generation.schemas.PlacementSpec``) or serialized mappings
        (``object_id``/``assetId`` keys). Items without both ids are skipped.
        Deterministic: iterated in input order, each resolved via the catalog.
        """
        out: dict[str, str] = {}
        for placement in placements:
            asset_id = _placement_value(placement, "asset_id", "assetId")
            object_id = _placement_value(placement, "object_id", "objectId")
            if asset_id is None or object_id is None:
                continue
            resolution = self.resolve_request(
                AssetRequest(requested_name=str(asset_id))
            )
            out[str(object_id)] = resolution.provenance.value
        return out

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _exact(self, descriptor: Any) -> AssetResolution:
        return AssetResolution(
            asset_id=descriptor.asset_id,
            catalog_version=self._catalog.catalog_version,
            provenance=Provenance.CATALOG_EXACT,
            version=descriptor.version,
            confidence=None,
            ambiguous=False,
            candidates=(),
            matched_alias=None,
            resolved=True,
            resolution_step="catalog_exact",
        )

    def _semantic_candidates(
        self, request: AssetRequest
    ) -> list[tuple[Any, float]]:
        """All catalog assets at/above the semantic threshold, deterministic.

        Filtering: a provided requiredInteraction must be supported by the
        candidate; every requiredEvidenceCapability must be present. Scoring:
        +3.0 per normalized tag hit, +2.0 categoryHint hit, +1.0 subtypeHint
        hit; only candidates scoring >= SEMANTIC_MIN_CONFIDENCE qualify.
        Sorted by (score desc, assetId asc) for a stable, dict-order-free
        ambiguity list.
        """
        norm_category = (
            normalize(request.category_hint) if request.category_hint else ""
        )
        norm_subtype = (
            normalize(request.subtype_hint) if request.subtype_hint else ""
        )
        norm_tags = {normalize(tag) for tag in request.tags}
        norm_required_interaction = (
            normalize(request.required_interaction)
            if request.required_interaction
            else ""
        )
        norm_required_capabilities = {
            normalize(cap) for cap in request.required_evidence_capabilities
        }

        scored: list[tuple[Any, float]] = []
        for descriptor in self._catalog.assets:
            if norm_required_interaction and norm_required_interaction not in {
                normalize(x) for x in descriptor.supported_interactions
            }:
                continue
            if norm_required_capabilities:
                owned = {normalize(x) for x in descriptor.evidence_capabilities}
                if not norm_required_capabilities.issubset(owned):
                    continue

            score = 0.0
            owned_tags = {normalize(tag) for tag in descriptor.tags}
            score += SEMANTIC_TAG_WEIGHT * float(len(norm_tags & owned_tags))
            if norm_category and norm_category == normalize(descriptor.category):
                score += SEMANTIC_CATEGORY_WEIGHT
            if norm_subtype and norm_subtype == normalize(descriptor.subtype):
                score += SEMANTIC_SUBTYPE_WEIGHT
            if score >= SEMANTIC_MIN_CONFIDENCE:
                scored.append((descriptor, score))

        scored.sort(key=lambda pair: (-pair[1], pair[0].asset_id))
        return scored

    # ------------------------------------------------------------------ #
    # Phase 26C5 — trusted semantic-category inference + fallback
    # ------------------------------------------------------------------ #

    def _infer_semantic_category(
        self, name: str, reduced: str
    ) -> tuple[str, str] | None:
        """The (category, matched phrase) of a request phrase, or None when NO
        safe category can be inferred.

        Deterministic order (§8):

        1. the trusted phrase -> category table (identity-normalized keys,
           checked on the FULL cleaned phrase first, then on each reduced
           candidate from ``semantic_candidate_phrases``);
        2. the bounded noun keyword map over the reduced tokens;
        3. dangerous classes short-circuit to their token here (the caller
           fails closed).

        The matched phrase is the candidate that produced the inference (used
        for the ``normalized_object_id`` observability field — "tire iron",
        not the material-stripped "tire"). ``None`` means "no safe category" —
        the request then follows the unchanged neutral-fallback / honest-
        ambiguity terminal.
        """
        for candidate in semantic_candidate_phrases(name):
            direct = SEMANTIC_PHRASE_CATEGORIES.get(normalize(candidate))
            if direct is not None:
                return (direct, candidate)
        tokens = tuple(semantic_phrase_tokens(reduced or name))
        if not tokens:
            return None
        found: dict[str, str] = {}
        for token in tokens:
            category = SEMANTIC_KEYWORD_CATEGORIES.get(token)
            if category is not None:
                found.setdefault(category, token)
        if not found:
            return None
        if "firearm" in found or "explosive" in found:
            category = "firearm" if "firearm" in found else "explosive"
            return (category, reduced or " ".join(tokens))
        for category in SEMANTIC_CATEGORY_PRIORITY:
            if category in found:
                return (category, reduced or " ".join(tokens))
        return None

    def _resolve_category_fallback(
        self, category: str, reduced: str, request: AssetRequest
    ) -> AssetResolution | None:
        """Resolve one inferred category through its TRUSTED catalog chain.

        Returns None ONLY when the category itself carries no usable
        representation (the caller then returns the fail-closed UNRESOLVED
        terminal). Deterministic; the first chain member that survives the
        interaction/capability filters and exists in ``self._catalog`` wins.
        A request that contains the word "generic" (or is the explicit
        generic-prop class) resolves with the category-safe GENERIC asset and
        the GENERIC_FALLBACK provenance.
        """
        chain = SEMANTIC_CATEGORY_FALLBACK_ASSETS.get(category, ())
        generic_asset = SEMANTIC_CATEGORY_GENERIC.get(category)
        explicit_generic = category == "generic_prop" or (
            "generic" in tuple(semantic_phrase_tokens(reduced or ""))
        )

        available = [
            asset_id
            for asset_id in chain
            if asset_id in self._by_id
        ]
        for asset_id in available:
            if self._asset_supports_request(asset_id, request):
                if explicit_generic and asset_id == generic_asset:
                    return self._category_resolution(
                        asset_id,
                        category,
                        reduced,
                        Provenance.GENERIC_FALLBACK,
                        FALLBACK_DEPTH_GENERIC,
                    )
                return self._category_resolution(
                    asset_id,
                    category,
                    reduced,
                    Provenance.CATEGORY_FALLBACK,
                    FALLBACK_DEPTH_CATEGORY,
                )
        if generic_asset is not None and generic_asset in self._by_id:
            if self._asset_supports_request(generic_asset, request):
                return self._category_resolution(
                    generic_asset,
                    category,
                    reduced,
                    Provenance.GENERIC_FALLBACK,
                    FALLBACK_DEPTH_GENERIC,
                )
        return None

    def _category_resolution(
        self,
        asset_id: str,
        category: str,
        reduced: str,
        provenance: Provenance,
        depth: int,
    ) -> AssetResolution:
        descriptor = self._by_id[asset_id]
        return AssetResolution(
            asset_id=descriptor.asset_id,
            catalog_version=self._catalog.catalog_version,
            provenance=provenance,
            version=descriptor.version,
            confidence=None,
            ambiguous=False,
            candidates=(),
            matched_alias=None,
            resolved=True,
            normalized_object_id=reduced or None,
            resolution_category=category,
            resolution_step=(
                "generic_fallback"
                if provenance is Provenance.GENERIC_FALLBACK
                else "category_fallback"
            ),
            fallback_depth=depth,
        )

    def _asset_supports_request(
        self, asset_id: str, request: AssetRequest
    ) -> bool:
        """The interaction/capability filter shared with ``_semantic_candidates``:
        a category fallback asset must support the SAME requiredInteraction and
        every requiredEvidenceCapability as any semantic candidate would."""
        descriptor = self._by_id[asset_id]
        if request.required_interaction:
            if normalize(request.required_interaction) not in {
                normalize(x) for x in descriptor.supported_interactions
            }:
                return False
        if request.required_evidence_capabilities:
            owned = {normalize(x) for x in descriptor.evidence_capabilities}
            required = {
                normalize(cap) for cap in request.required_evidence_capabilities
            }
            if not required.issubset(owned):
                return False
        return True

    def _semantic_match_is_category_safe(
        self, request: AssetRequest, name: str, winner_asset_id: str
    ) -> bool:
        """C5-02 gate: keep a UNIQUE semantic winner only when its semantic
        class is category-consistent with the request phrase.

        The phrase's semantic category is inferred through the SAME bounded
        taxonomy the trusted fallback uses (``_infer_semantic_category`` +
        ``SEMANTIC_CATEGORY_FALLBACK_ASSETS``). A unique winner that is NOT a
        member of the inferred category's trusted chain CONTRADICTS the
        inferred class (the adversarial ``ice pick`` + ``weapon``/``restraint``
        probe uniquely wins on PROP_ROPE_01 solely because rope owns both
        tags, at the composer's full CRITICAL_MIN_SEMANTIC_CONFIDENCE of 6.0)
        — it is NOT category-safe, so ``resolve_request`` falls through to
        steps 5-6 (normalized exact -> trusted category fallback -> fail
        closed) instead of publishing a materiality-wrong substitute.

        When NO category can be inferred, or the inferred category has no
        trusted chain, the semantic winner is KEPT exactly as before (zero
        behavior change for the documented working paths). Dangerous classes
        (firearm/explosive) make the winner not-category-safe REGARDLESS —
        such a request still fails closed through step 6 and can never render
        a wrong substitute.
        """
        inferred = self._infer_semantic_category(
            name, semantic_phrase_reduce(name)
        )
        if inferred is None:
            return True
        category, _matched_phrase = inferred
        if category in SEMANTIC_UNRESOLVED_CATEGORIES:
            return False
        chain = SEMANTIC_CATEGORY_FALLBACK_ASSETS.get(category, ())
        if not chain:
            return True
        if winner_asset_id in chain:
            # Same semantic class as the inferred category AND a member of the
            # documented trusted chain -> the winning asset is category-safe.
            return True
        # The winner contradicts the inferred class: never a unique win.
        return False


# --------------------------------------------------------------------------- #
# module-level convenience API
# --------------------------------------------------------------------------- #


def _placement_value(placement: Any, attr: str, key: str) -> Any:
    """Read ``object_id``/``asset_id`` from dataclasses or raw mappings."""
    if isinstance(placement, Mapping):
        return placement.get(key, placement.get(attr))
    return getattr(placement, attr, None)


# --------------------------------------------------------------------------- #
# Phase 12 §Variant — bounded parametric variants
# --------------------------------------------------------------------------- #


def _variant_surface(asset: Any) -> dict[str, Any]:
    """The deterministic per-asset variant-parameter surface.

    Merges the (load-validated, allowlist-consistent) variant specs of an
    asset into ONE key -> VariantParamSpec map. For each param key the first
    declared (manifest-order) spec wins; catalog validation guarantees every
    other variant's spec for that key shares the same allowlist/scale bounds.
    """
    surface: dict[str, Any] = {}
    for variant in asset.variants:
        for key, spec in variant.params.items():
            surface.setdefault(key, spec)
    return surface


def _primary_tone_key(colors: Mapping[str, str]) -> str | None:
    """Deterministic primary color key (frontend-REGISTRY preference order)."""
    for key in _PRIMARY_COLOR_KEYS:
        if key in colors:
            return key
    return next(iter(colors), None)


def apply_variant(
    asset: Any,
    variant_params: Mapping[str, Any],
    *,
    catalog: Catalog | None = None,
) -> AssetVariantView:
    """Validate + apply a bounded parametric variant to ONE asset.

    ``asset`` may be an ``AssetDescriptor`` or an assetId string (then looked
    up in ``catalog``, defaulting to the repo manifest). ``variant_params`` is
    the mapping of variant parameter values to apply, e.g. ``{"material":
    "wood.dark", "state": "clean", "scale": 1.1}``.

    Rules (bounded, never raising on ``None``/spec errors — those are raised
    as ``AssetVariantError``):
    - an asset with NO declared variants (non-composite assets and the neutral
      fallback) cannot be varianted -> ``AssetVariantError``;
    - unknown param key (not in the asset's variant surface) -> error;
    - color/material/state values must be members of the key's declared
      allowlist (a failed value is rejected, never coerced);
    - a scale value must be a FINITE number (NaN/±Inf raise ``AssetVariantError``,
      DEF-065) and is CLAMPED into the asset's declared [min, max], so an
      extreme scale can never escape the declared bounds;
    - the returned view carries the base ``colors`` MERGED with an applied
      color override on the deterministic primary-tone key.
    """
    if isinstance(asset, str):
        if catalog is None:
            from app.assets.catalog import load_catalog_from_repo

            catalog = load_catalog_from_repo()
        asset = catalog.by_id.get(asset)
        if asset is None:
            raise AssetVariantError(f"unknown asset {asset!r}")
    if not getattr(asset, "variants", None):
        raise AssetVariantError(
            f"asset {asset.asset_id!r} declares no variants"
        )
    if not isinstance(variant_params, Mapping):
        raise AssetVariantError("variant params must be a mapping")

    surface = _variant_surface(asset)
    for key in variant_params:
        if key not in surface:
            raise AssetVariantError(
                f"unknown variant parameter {key!r} for asset "
                f"{asset.asset_id!r}"
            )

    merged_colors = dict(asset.colors)
    scale_value = 1.0
    material: str | None = None
    state: str | None = None

    for key, spec in surface.items():
        value = variant_params.get(key, spec.default)
        if key == "color":
            if value not in spec.allowlist:
                raise AssetVariantError(
                    f"variant color {value!r} is not in the declared allowlist "
                    f"for asset {asset.asset_id!r}"
                )
            primary = _primary_tone_key(merged_colors)
            if primary is not None:
                merged_colors[primary] = value
        elif key == "material":
            if value not in spec.allowlist:
                raise AssetVariantError(
                    f"variant material {value!r} is not in the declared "
                    f"allowlist for asset {asset.asset_id!r}"
                )
            material = value
        elif key == "state":
            if value not in spec.allowlist:
                raise AssetVariantError(
                    f"variant state {value!r} is not in the declared allowlist "
                    f"for asset {asset.asset_id!r}"
                )
            state = value
        elif key == "scale":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise AssetVariantError(
                    f"variant scale must be a number (got {value!r}) for asset "
                    f"{asset.asset_id!r}"
                )
            applied = float(value)
            # DEF-065: NaN/±Inf must NEVER reach the clamped view — Python's
            # min/max propagate NaN (``min(nan, max)`` -> nan), silently
            # violating "scale can never escape the declared bounds". Reject
            # any non-finite value BEFORE clamping; finite extremes clamp.
            if not math.isfinite(applied):
                raise AssetVariantError(
                    f"variant scale must be a finite number (got {value!r}) "
                    f"for asset {asset.asset_id!r}"
                )
            if (
                spec.min_value is not None
                and spec.max_value is not None
            ):
                scale_value = min(
                    max(applied, spec.min_value), spec.max_value
                )
            else:
                scale_value = applied

    from types import MappingProxyType

    return AssetVariantView(
        asset_id=asset.asset_id,
        template_id=asset.template_id,
        colors=MappingProxyType(merged_colors),
        scale=scale_value,
        material=material,
        state=state,
    )


def resolve_with_variant(
    request: AssetRequest | Mapping[str, Any],
    variant_params: Mapping[str, Any],
    *,
    catalog: Catalog | None = None,
) -> AssetVariantResolution:
    """Resolve an asset through the normal catalog order, then apply a variant.

    Accepts the same inputs as ``resolve`` (typed ``AssetRequest`` or a raw
    mapping; raw payloads pass through ``app.assets.validation`` first). The
    resolved asset's variant is validated + applied via ``apply_variant`` and
    the result is reported with provenance ``PARAMETRIC_VARIANT`` (the
    reserved Phase 12+ provenance — never emitted for non-variant resolves).

    Raises ``AssetVariantError`` when the request is unresolved (ambiguous) or
    falls back, when the resolved asset declares no variants, or when the
    variant params are invalid.
    """
    if catalog is None:
        catalog = load_catalog_from_repo()
    resolution = resolve(request, catalog=catalog)
    if not resolution.resolved:
        raise AssetVariantError(
            "cannot apply a variant to an unresolved (ambiguous) request"
        )
    if resolution.provenance is Provenance.FALLBACK:
        raise AssetVariantError(
            "cannot apply a variant to a FALLBACK resolution (the neutral "
            "fallback declares no variants)"
        )
    descriptor = catalog.by_id[resolution.asset_id]
    view = apply_variant(descriptor, variant_params)
    return AssetVariantResolution(
        asset_id=resolution.asset_id,
        catalog_version=resolution.catalog_version,
        provenance=Provenance.PARAMETRIC_VARIANT,
        version=resolution.version,
        view=view,
        resolved=True,
    )


def _asset_request_from_raw(payload: Any) -> AssetRequest:
    """Validate then parse a RAW (possibly untrusted) payload."""
    issues = validate_asset_request(payload)
    if issues:
        raise AssetRequestValidationError(issues)
    if not isinstance(payload, Mapping):
        raise AssetRequestValidationError(
            ("asset request must be a JSON object",)
        )

    def _pick(camel: str, snake: str, default: Any = None) -> Any:
        if camel in payload:
            return payload[camel]
        if snake in payload:
            return payload[snake]
        return default

    tags = _pick("tags", "tags", ()) or ()
    capabilities = _pick(
        "requiredEvidenceCapabilities", "required_evidence_capabilities", ()
    ) or ()
    style_hints = _pick("styleHints", "style_hints", ()) or ()
    return AssetRequest(
        requested_name=str(_pick("requestedName", "requested_name", "")),
        category_hint=_pick("categoryHint", "category_hint"),
        subtype_hint=_pick("subtypeHint", "subtype_hint"),
        tags=tuple(str(tag) for tag in tags),
        required_interaction=_pick(
            "requiredInteraction", "required_interaction"
        ),
        required_evidence_capabilities=tuple(
            str(cap) for cap in capabilities
        ),
        style_hints=tuple(str(hint) for hint in style_hints),
    )


def resolve(
    request: AssetRequest | Mapping[str, Any], *, catalog: Catalog | None = None
) -> AssetResolution:
    """Resolve a request against ``catalog`` (default: the repo manifest).

    Accepts a TYPED ``AssetRequest`` (trusted internal callers) or a RAW
    mapping. RAW payloads are first passed through security validation
    (``app.assets.validation``): an unsafe payload raises
    ``AssetRequestValidationError`` with the sorted issues BEFORE any
    resolution can load content from it.
    """
    if catalog is None:
        catalog = load_catalog_from_repo()
    resolver = AssetResolver(catalog)
    if isinstance(request, AssetRequest):
        return resolver.resolve_request(request)
    return resolver.resolve_request(_asset_request_from_raw(request))


def resolve_placements_provenance(
    placements: Iterable[Any], *, catalog: Catalog | None = None
) -> dict[str, str]:
    """INTERNAL diagnostic: ``{objectId: provenance}`` for a placements list.

    Runs every placement's assetId through the resolver (catalog default: the
    repo manifest). The result is diagnostic only — it is never part of any
    published payload or API DTO, and it never alters the placement ids.
    """
    if catalog is None:
        catalog = load_catalog_from_repo()
    return AssetResolver(catalog).resolve_placements_provenance(placements)


__all__ = [
    "SEMANTIC_CATEGORY_WEIGHT",
    "SEMANTIC_MIN_CONFIDENCE",
    "SEMANTIC_SUBTYPE_WEIGHT",
    "SEMANTIC_TAG_WEIGHT",
    "SEMANTIC_CATEGORY_FALLBACK_ASSETS",
    "SEMANTIC_CATEGORY_GENERIC",
    "SEMANTIC_CATEGORY_PRIORITY",
    "SEMANTIC_KEYWORD_CATEGORIES",
    "SEMANTIC_MODIFIER_TOKENS",
    "SEMANTIC_PHRASE_CATEGORIES",
    "SEMANTIC_UNRESOLVED_CATEGORIES",
    "FALLBACK_DEPTH_BASE",
    "FALLBACK_DEPTH_NORMALIZED_EXACT",
    "FALLBACK_DEPTH_CATEGORY",
    "FALLBACK_DEPTH_GENERIC",
    "FALLBACK_DEPTH_TERMINAL",
    "AssetRequest",
    "AssetResolution",
    "AssetResolver",
    "AssetVariantError",
    "AssetVariantResolution",
    "AssetVariantView",
    "Provenance",
    "apply_variant",
    "normalize",
    "resolve",
    "resolve_placements_provenance",
    "resolve_with_variant",
    "semantic_candidate_phrases",
    "semantic_phrase_reduce",
    "semantic_phrase_tokens",
]