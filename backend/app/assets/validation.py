"""Security validation of RAW asset-request payloads BEFORE typed parse.

Phase 10 / Phase_POST_MVP_ROADMAP: the resolver must never load anything based
directly on untrusted raw prompt text. ``validate_asset_request`` is that
gate — a deterministic, never-raising scanner that rejects URL schemes, path
separators/traversal, absolute paths, executable/script/handler/shader tokens,
control characters and oversized strings/arrays before a payload is parsed
into the typed ``AssetRequest`` and handed to the resolver.

Rules (sorted issue strings; empty tuple when safe):

- every string is scanned RAW and then through an NFKC-normalized form (the
  canonicalization approach of ``app.generation.safety``, minus HTML-entity
  decoding), so mangled/mixed-width spellings cannot evade the controls
  (DEF-061). The token checks therefore fire for: ``http:``/``https:``/
  ``data:``/``file:``/``javascript:`` scheme tokens (conservative substring
  scan, as specified); whitespace-split schemes ("java script:" compacts to
  "javascript:"); HTML-entity colons ("javascript&colon;" decolons to
  "javascript:"); fullwidth separators/letters (NFKC maps "／" to "/",
  "ｓｃｒｉｐｔ" to "script", "ｈｔｔｐ：" to "http:"); ``%2e`` percent-
  encoded traversal (DEF-061); path separators (``/`` ``\\``); ``..``
  traversal; absolute-path prefixes (leading ``/`` ``\\`` or a drive letter);
  forbidden word tokens ``script``/``handler``/``shader``/``function``/``eval``
  (word-boundary match); control characters (``ord < 0x20``, NUL included) and
  oversized strings (> 120 characters);
- list fields are bounded: ``tags`` <= 16, ``requiredEvidenceCapabilities``
  <= 8, ``styleHints`` <= 16, and every entry must be a non-empty string;
- non-string types anywhere in the payload are reported explicitly.

Benign names ("kitchen knife", "blue vase", "letter opener", "notebook") are
not affected: no scan form contains a scheme token, a forbidden word at a word
boundary, a separator, traversal or an absolute prefix.

This module performs NO resolution and NO I/O; the resolver imports it to gate
raw payloads and ``resolve()`` raises ``AssetRequestValidationError`` carrying
these issues so an attack can never silently fall back to a fake asset.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping

from app.assets.glyphs import format_glyph_issues

MAX_ASSET_STRING_LENGTH = 120
MAX_TAGS = 16
MAX_CAPABILITIES = 8
MAX_STYLE_HINTS = 16

# Conservative forbidden-scheme tokens (scheme + colon, as specified). A
# substring scan intentionally errs toward rejection on the asset-name surface.
FORBIDDEN_URL_TOKENS: tuple[str, ...] = (
    "http:",
    "https:",
    "data:",
    "file:",
    "javascript:",
)

# Executable/handler/shader word tokens, matched at word boundaries (a bare
# prose word like "shader" in a request name is still rejected — asset names
# must never carry executable/language tokens).
_FORBIDDEN_TOKEN_RE = re.compile(
    r"\b(?:script|handler|shader|function|eval)\b", re.IGNORECASE
)

# Windows drive-letter absolute-path prefix (e.g. "C:\..." / "C:/...").
_DRIVE_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")

# Percent-encoded traversal: %2e (%2E) is an encoded dot; any occurrence is a
# traversal/encoding-evasion indicator (DEF-061).
_PERCENT_DOT_RE = re.compile(r"%2e", re.IGNORECASE)

# HTML-entity spellings of the colon (decoded for the scheme scan ONLY —
# general entity decoding is intentionally NOT performed).
_ENTITY_COLON_RE = re.compile(r"&(?:colon|#58|#x3a);", re.IGNORECASE)


class AssetRequestValidationError(ValueError):
    """A raw asset request failed security validation.

    Carries the deterministic, sorted issue strings; never a raw exception.
    """

    def __init__(self, issues: tuple[str, ...]) -> None:
        self.issues = tuple(sorted(set(issues)))
        super().__init__("; ".join(self.issues))


def _string_issues(value: str, where: str) -> list[str]:
    """Deterministic issue strings for one required string (empty when safe).

    Each control is evaluated against the RAW form AND the NFKC-normalized
    form (and, for the scheme scan only, a whitespace-compacted form and an
    entity-colon-decoded form) so mangled spellings cannot evade the gate.
    """
    issues: list[str] = []
    norm = unicodedata.normalize("NFKC", value)
    # Scan forms used by every categorical control.
    forms = (value, norm)
    # Scheme-specific extras: whitespace-compacted (catches "java script:"
    # -> "javascript:") and entity-colon-decoded (catches
    # "javascript&colon;" -> "javascript:").
    compact = "".join(ch for ch in norm if not ch.isspace())
    entity_colon = _ENTITY_COLON_RE.sub(":", value)
    scheme_forms = (value, norm, compact, unicodedata.normalize("NFKC", entity_colon))

    if _PERCENT_DOT_RE.search(value):
        issues.append(f"{where}: contains percent-encoded path traversal '%2e'")
    if max(len(value), len(norm)) > MAX_ASSET_STRING_LENGTH:
        issues.append(
            f"{where}: string exceeds {MAX_ASSET_STRING_LENGTH} characters"
        )
    if any(ord(ch) < 0x20 for ch in value):
        issues.append(f"{where}: contains a control character")
    issues.extend(format_glyph_issues(value, where))

    lowered_forms = [form.casefold() for form in scheme_forms]
    for scheme in FORBIDDEN_URL_TOKENS:
        if any(scheme in form for form in lowered_forms):
            issues.append(f"{where}: contains a forbidden URL scheme {scheme!r}")
            break

    token = _FORBIDDEN_TOKEN_RE.search(value) or _FORBIDDEN_TOKEN_RE.search(norm)
    if token:
        issues.append(
            f"{where}: contains a forbidden token {token.group(0).lower()!r}"
        )
    if any(("/" in form) or ("\\" in form) for form in forms):
        issues.append(f"{where}: contains a path separator")
    if any(".." in form for form in forms):
        issues.append(f"{where}: contains path traversal '..'")
    if any(
        form.startswith(("/", "\\")) or _DRIVE_ABSOLUTE_RE.match(form)
        for form in forms
    ):
        issues.append(f"{where}: is an absolute path")
    return issues


def _optional_string_issues(
    payload: Mapping[str, Any],
    camel_key: str,
    snake_key: str,
    where: str,
    issues: list[str],
) -> str | None:
    """Validate one optional scalar string; return its value or None.

    ``None`` means the key is absent OR invalid; every invalid case pushes a
    deterministic issue.
    """
    if camel_key in payload:
        raw = payload[camel_key]
    elif snake_key in payload:
        raw = payload[snake_key]
    else:
        return None
    if not isinstance(raw, str) or not raw.strip():
        issues.append(f"{where}: must be a non-empty string")
        return None
    issues.extend(_string_issues(raw, where))
    return raw


def _optional_list_issues(
    payload: Mapping[str, Any],
    camel_key: str,
    snake_key: str,
    where: str,
    max_len: int,
    issues: list[str],
) -> tuple[str, ...]:
    """Validate one optional list-of-strings field; return the value tuple."""
    if camel_key in payload:
        raw = payload[camel_key]
    elif snake_key in payload:
        raw = payload[snake_key]
    else:
        return ()
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        issues.append(f"{where}: must be an array of non-empty strings")
        return ()
    if len(raw) > max_len:
        issues.append(f"{where}: exceeds the maximum of {max_len} entries")
    out: list[str] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, str) or not entry.strip():
            issues.append(f"{where}[{index}]: must be a non-empty string")
        else:
            issues.extend(_string_issues(entry, f"{where}[{index}]"))
            out.append(entry)
    return tuple(out)


def validate_asset_request(payload: Any) -> tuple[str, ...]:
    """Reject unsafe RAW asset-request payloads; return sorted issues.

    Never raises and performs no I/O: malformed input is reported as issue
    strings. An empty tuple means the payload is safe to parse into a typed
    ``AssetRequest``.
    """
    issues: list[str] = []
    if not isinstance(payload, Mapping):
        return ("asset request must be a JSON object",)

    if "requestedName" not in payload and "requested_name" not in payload:
        issues.append("requestedName: required field is missing")
    else:
        raw = payload.get("requestedName", payload.get("requested_name"))
        if raw is None or not isinstance(raw, str) or not raw.strip():
            issues.append("requestedName: must be a non-empty string")
        else:
            issues.extend(_string_issues(raw, "requestedName"))

    _optional_string_issues(
        payload, "categoryHint", "category_hint", "categoryHint", issues
    )
    _optional_string_issues(
        payload, "subtypeHint", "subtype_hint", "subtypeHint", issues
    )
    _optional_string_issues(
        payload, "requiredInteraction", "required_interaction",
        "requiredInteraction", issues,
    )
    _optional_list_issues(
        payload, "tags", "tags", "tags", MAX_TAGS, issues
    )
    _optional_list_issues(
        payload,
        "requiredEvidenceCapabilities",
        "required_evidence_capabilities",
        "requiredEvidenceCapabilities",
        MAX_CAPABILITIES,
        issues,
    )
    _optional_list_issues(
        payload, "styleHints", "style_hints", "styleHints", MAX_STYLE_HINTS, issues
    )

    return tuple(sorted(set(issues)))


__all__ = [
    "AssetRequestValidationError",
    "FORBIDDEN_URL_TOKENS",
    "MAX_ASSET_STRING_LENGTH",
    "MAX_CAPABILITIES",
    "MAX_STYLE_HINTS",
    "MAX_TAGS",
    "validate_asset_request",
]