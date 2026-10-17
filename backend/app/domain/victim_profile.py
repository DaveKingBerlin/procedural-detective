"""Phase 47 — deterministic victim / public-person identity normalization.

The victim is NOT a separate identity object: the victim IS the ``draft.persons``
entry whose ``role == "victim"`` (``draft.crime.victim_id`` must resolve to it).
This module owns the SINGLE deterministic normalization of the structured
identity presentation keys that may live inside a person's ``presentedData``
mapping (serialized snake_case as ``presented_data``):

    age          integer 1..120
    gender       one of the CLOSED vocabulary {"male","female","other","unknown"}
    occupation   non-empty string, bounded to 120 chars
    background   optional string, bounded to 300 chars

GENDER NORMALIZATION (documented, case-insensitive, closed):
    m | man | male | masculine            -> "male"
    f | woman | female | feminine         -> "female"
    other                                 -> "other"
    anything else (including absent pieces) -> "unknown"

The helpers are pure and provider-free, so both the read-time projections
(``app.services.publication``) and the generation-time quality validator
(``app.generation.case_quality``) share ONE boundary contract. ``identity_of``
OMITS an absent/invalid field (never ``null``/``undefined``/``NaN``); the VICTIM
profile projection then applies the documented deterministic fallbacks above
(DEF-115) so a PUBLISHED case always renders name + age + gender + occupation.
"""

from __future__ import annotations

from typing import Any, Mapping

# The CLOSED gender vocabulary (the ONLY tokens any DTO may ever emit).
GENDER_VALUES: tuple[str, ...] = ("male", "female", "other", "unknown")
GENDER_UNKNOWN = "unknown"

MIN_AGE = 1
MAX_AGE = 120
MAX_OCCUPATION_CHARS = 120
MAX_BACKGROUND_CHARS = 300

# DEF-115 — documented deterministic fallbacks used ONLY to render a COMPLETE
# published victim profile when the generated/crafted case omits (or mis-types)
# an identity field. These never fabricate solution-critical facts (no motive /
# weapon / killer / crime time); they are neutral presentation defaults:
#   gender missing/invalid  -> "unknown"  (the closed vocabulary's safe token)
#   occupation missing      -> "Unknown"  (only when genuinely unavailable)
#   age missing/invalid     -> DEFAULT_VICTIM_AGE (a neutral in-range constant)
DEFAULT_VICTIM_AGE = 40
DEFAULT_VICTIM_OCCUPATION = "Unknown"

# Common accepted variants (documented in the module docstring). Everything
# outside this closed alias table normalizes to "unknown".
_GENDER_MALE: frozenset[str] = frozenset(
    {"m", "man", "male", "masculine", "boy", "gentleman"}
)
_GENDER_FEMALE: frozenset[str] = frozenset(
    {"f", "woman", "female", "feminine", "girl", "lady"}
)
_GENDER_OTHER: frozenset[str] = frozenset({"other"})


def normalize_gender(value: Any) -> str:
    """Normalize a presented gender to the CLOSED vocabulary (see docstring)."""
    if not isinstance(value, str):
        return GENDER_UNKNOWN
    token = value.strip().casefold()
    if token in _GENDER_MALE:
        return "male"
    if token in _GENDER_FEMALE:
        return "female"
    if token in _GENDER_OTHER:
        return "other"
    return GENDER_UNKNOWN


def normalize_age(value: Any) -> int | None:
    """Normalize a presented age to an int in [1, 120] or None (invalid).

    Accepts an ``int`` (never a ``bool``) or a decimal digit string. Anything
    else — including out-of-range numbers — is None so the caller omits it; a
    value is NEVER coerced into a bogus int.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if MIN_AGE <= value <= MAX_AGE else None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            number = int(text)
            return number if MIN_AGE <= number <= MAX_AGE else None
    return None


def normalize_occupation(value: Any) -> str | None:
    """Normalize a presented occupation to a non-empty bounded string or None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:MAX_OCCUPATION_CHARS]


def normalize_background(value: Any) -> str | None:
    """Normalize optional public background to a non-empty bounded string or None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:MAX_BACKGROUND_CHARS]


def identity_of(presented_data: Any) -> dict[str, Any]:
    """The normalized PRESENT public-identity fields of one ``presented_data``.

    Returns a fresh dict carrying ONLY the keys whose value is present and
    valid (``age`` / ``gender`` / ``occupation`` / ``background``). An absent or
    JSON-null field is OMITTED (never rendered as null). A PRESENT but
    unrecognized ``gender`` normalizes to ``"unknown"`` so a supplied identity
    never silently disappears. Never raises for a non-mapping input.
    """
    if not isinstance(presented_data, Mapping):
        return {}
    out: dict[str, Any] = {}
    if presented_data.get("age") is not None:
        age = normalize_age(presented_data.get("age"))
        if age is not None:
            out["age"] = age
    if presented_data.get("gender") is not None:
        out["gender"] = normalize_gender(presented_data.get("gender"))
    if presented_data.get("occupation") is not None:
        occupation = normalize_occupation(presented_data.get("occupation"))
        if occupation is not None:
            out["occupation"] = occupation
    if presented_data.get("background") is not None:
        background = normalize_background(presented_data.get("background"))
        if background is not None:
            out["background"] = background
    return out


__all__ = [
    "DEFAULT_VICTIM_AGE",
    "DEFAULT_VICTIM_OCCUPATION",
    "GENDER_UNKNOWN",
    "GENDER_VALUES",
    "MAX_AGE",
    "MAX_BACKGROUND_CHARS",
    "MAX_OCCUPATION_CHARS",
    "MIN_AGE",
    "identity_of",
    "normalize_age",
    "normalize_background",
    "normalize_gender",
    "normalize_occupation",
]
