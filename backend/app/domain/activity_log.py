"""Phase 19J — Hermes-generated multi-entry computer activity logs (domain model).

Closed structured schema, deterministic validation and player-safe persistence
for the ``ACTIVITY_LOG`` representation of a time-bearing evidence fact.

Authority split (Phase19J §2): the SERVER owns the canonical fact — the
canonical evidence timestamp is a LOCKED constraint injected into the provider
prompt, and this validator GUARANTEES it appears exactly once in the returned
log. The LLM generates surrounding noise only; it never decides or redefines
the canonical time, and it never decides the crime/evidence semantics.

Rules implemented here (Phase19J §8/§11/§15/§16/§17/§21/§22/§23/§24/§25/§26):

- entry count 15..20;
- every ``timestamp`` parses as ISO-8601 WITH an offset (``+02:00`` /
  ``+01:00`` / ``Z`` are equivalent instants; malformed timestamps are
  rejected deterministically);
- timestamps are STRICTLY chronological AND unique (no repeated instants);
- the canonical evidence time appears EXACTLY once (instant equality);
- every timestamp lies inside the permitted application-level temporal window
  (``[canonical - before, canonical + after]`` minutes; hard maximum total
  span 120 minutes; deterministic, app-owned);
- ``activityType`` is one of the CLOSED enum tokens;
- ``activity`` is non-empty, at most ``MAX_ACTIVITY_TEXT_CHARS`` characters,
  and free of control characters / HTML-markup / URLs / path-like text;
- no duplicate entries (same timestamp + text);
- no direct truth-leak language (answer-like tokens);
- no entity/weapon/motive/location leakage (app-owned name set, strict-by-
  default; ADV-254/ADV-255/ADV-258/ADV-260: person-name matching folds
  case/ASCII/NFKC, strips honorifics and Unicode Cf-format chars, and compares
  first/last/full tokens, leet variants, concatenated-username forms and plural
  surnames; weapon/motive paraphrases are rejected via word-token and
  de-genericized content-word needles with the shared bounded neutral
  computer-log carve-out so canonical weapons sharing ordinary words never
  over-block harmless rows).

Purity: this module is pure — zero provider calls, zero network, never reads
CaseTruth or solver material, and never touches raw prompts. Every function is
deterministic and bounded.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from app.assets.depthguard import bounded_json_loads
from app.domain.time_interval import parse_iso8601_to_epoch

# --------------------------------------------------------------------------- #
# closed activity-type vocabulary (Phase19J §11)
# --------------------------------------------------------------------------- #

ACTIVITY_LOG_ACTIVITY_TYPES: frozenset[str] = frozenset(
    {
        "SYSTEM_RESUME",
        "SYSTEM_IDLE",
        "SESSION_UNLOCK",
        "SESSION_LOCK",
        "LOGIN",
        "LOGOUT",
        "FILE_OPEN",
        "FILE_WRITE",
        "FILE_COPY",
        "DOCUMENT_ACCESS",
        "DOCUMENT_AUTOSAVE",
        "BROWSER_ACTIVITY",
        "MAIL_SYNC",
        "CLOUD_SYNC",
        "BACKGROUND_SYNC",
        "USB_CONNECTED",
        "NETWORK_ACTIVITY",
        "BACKUP",
        "LOCAL_ACTIVITY",
        "APPLICATION_OPEN",
        "APPLICATION_CLOSE",
    }
)

# --------------------------------------------------------------------------- #
# entry bounds (Phase19J §8)
# --------------------------------------------------------------------------- #

MIN_ACTIVITY_LOG_ENTRIES = 15
MAX_ACTIVITY_LOG_ENTRIES = 20
MAX_ACTIVITY_TEXT_CHARS = 120
# Hard app-level bound on a SINGLE parsed provider entries list (defense in
# depth: 20 is the accepted maximum, so 64 admits every rejectable oversized
# list without buffering an unbounded array).
_MAX_PARSED_ENTRIES = 64

# --------------------------------------------------------------------------- #
# deterministic temporal window (Phase19J §15)
# --------------------------------------------------------------------------- #

WINDOW_DEFAULT_BEFORE_MINUTES = 60
WINDOW_DEFAULT_AFTER_MINUTES = 60
WINDOW_HARD_MAX_TOTAL_MINUTES = 120

# --------------------------------------------------------------------------- #
# persistence version marker (Phase19J §52 — clean versioning of the rich
# evidence shape). The log is persisted into the existing player-safe
# ``presentation.events`` shape (``{time, action}`` — the allowlisted cctv
# events contract WITHOUT ``personId``, because logs carry no named entities);
# the marker records that the events are a Phase 19J accepted generated log.
# --------------------------------------------------------------------------- #

ACTIVITY_LOG_VERSION_MARKER = "ACTIVITY_LOG_v1"

# --------------------------------------------------------------------------- #
# typed internal validator codes (Phase19J §38). The string VALUES exactly
# match the canonical ``GenerationFailureCode`` additions so production can map
# ``GenerationFailureCode(ActivityLogValidatorCode.X.value)`` without this pure
# module ever importing the generation package.
# --------------------------------------------------------------------------- #


class ActivityLogValidatorCode(str, Enum):
    ACTIVITY_LOG_SCHEMA_INVALID = "ACTIVITY_LOG_SCHEMA_INVALID"
    ACTIVITY_LOG_ENTRY_COUNT_INVALID = "ACTIVITY_LOG_ENTRY_COUNT_INVALID"
    ACTIVITY_LOG_TIME_ORDER_INVALID = "ACTIVITY_LOG_TIME_ORDER_INVALID"
    ACTIVITY_LOG_CANONICAL_TIME_MISSING = "ACTIVITY_LOG_CANONICAL_TIME_MISSING"
    ACTIVITY_LOG_CANONICAL_TIME_DUPLICATED = "ACTIVITY_LOG_CANONICAL_TIME_DUPLICATED"
    ACTIVITY_LOG_TIME_WINDOW_INVALID = "ACTIVITY_LOG_TIME_WINDOW_INVALID"
    ACTIVITY_LOG_DIRECT_TRUTH_LEAK = "ACTIVITY_LOG_DIRECT_TRUTH_LEAK"
    ACTIVITY_LOG_ENTITY_LEAK = "ACTIVITY_LOG_ENTITY_LEAK"
    ACTIVITY_LOG_PROVIDER_FAILED = "ACTIVITY_LOG_PROVIDER_FAILED"


# Deterministic reporting priority: the FIRST code in this order is the one
# surfaced on a terminal (post-repair) failure. Schema-level violations come
# before semantic ones; the canonical-time invariant comes before the window;
# leaks come last (they are only reachable once the shape is valid).
_VALIDATOR_PRIORITY: tuple[str, ...] = (
    ActivityLogValidatorCode.ACTIVITY_LOG_SCHEMA_INVALID.value,
    ActivityLogValidatorCode.ACTIVITY_LOG_ENTRY_COUNT_INVALID.value,
    ActivityLogValidatorCode.ACTIVITY_LOG_TIME_ORDER_INVALID.value,
    ActivityLogValidatorCode.ACTIVITY_LOG_CANONICAL_TIME_MISSING.value,
    ActivityLogValidatorCode.ACTIVITY_LOG_CANONICAL_TIME_DUPLICATED.value,
    ActivityLogValidatorCode.ACTIVITY_LOG_TIME_WINDOW_INVALID.value,
    ActivityLogValidatorCode.ACTIVITY_LOG_DIRECT_TRUTH_LEAK.value,
    ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK.value,
)


def primary_validator_code(
    codes: Iterable[ActivityLogValidatorCode],
) -> ActivityLogValidatorCode | None:
    """The FIRST reportable validator code in the documented priority order."""
    present = {getattr(code, "value", None) or str(code) for code in codes}
    for value in _VALIDATOR_PRIORITY:
        if value in present:
            return ActivityLogValidatorCode(value)
    return None


# --------------------------------------------------------------------------- #
# direct truth-leak vocabulary (Phase19J §22)
#
# Bounded deterministic phrase/word list. Phrases (containing a space) are
# matched as casefolded substrings of the normalized scan text; single-word
# tokens are matched with word boundaries so harmless lookalikes are not
# over-blocked. The FULL published draft additionally passes the project's
# content-safety scan at validation time (defense in depth).
# --------------------------------------------------------------------------- #

_TRUTH_LEAK_PHRASES: frozenset[str] = frozenset(
    {
        "victim killed",
        "crime occurred",
        "weapon used",
        "crime time",
        "time of death",
        "murder weapon",
        "murder time",
        "killing of the victim",
        "killer used",
    }
)

_TRUTH_LEAK_WORDS: frozenset[str] = frozenset(
    {
        "murder",
        "murdered",
        "murderer",
        "murdering",
        "killer",
        "killed",
        "killing",
        "attack",
        "attacked",
        "attacking",
        "culprit",
        "homicide",
        "stabbed",
        "stabbing",
        "strangled",
        "slain",
        "fatal",
        "gunshot",
        "shooting",
        "perpetrator",
        "assassin",
    }
)

# --------------------------------------------------------------------------- #
# unsafe-text scanner (Phase19J §21/§49)
# --------------------------------------------------------------------------- #

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_HTML_OPEN_RE = re.compile(r"<\s*[a-z/!]")
_HTML_CLOSE_RE = re.compile(r">")
_URL_SCHEME_RE = re.compile(r"\b(?:https?|ftp|file|data|javascript)\s*:")
_WWW_RE = re.compile(r"\bwww\s*\.\s*")
_PATH_LIKE_RE = re.compile(
    r"(?:^|\s)[A-Za-z]:[\\/]|(?:^|\s)[\\/]|[\\/]{2}|\.\.[\\/]|%2e%2e"
)
_BACKTICK_RE = re.compile(r"`")


def _scan_text(value: str) -> str:
    """Bounded deterministic normalized scan form of one activity string."""
    text = unicodedata.normalize("NFKC", value)
    text = html.unescape(text)
    text = text.casefold()
    # collapse whitespace runs so phrase tokens match across line wraps
    return re.sub(r"\s+", " ", text).strip()


def activity_text_unsafe_tokens(value: str) -> tuple[str, ...]:
    """Deterministic unsafe-text findings for ONE activity string.

    Returns the matching token labels (empty when the text is safe/bounded).
    """
    issues: list[str] = []
    if not isinstance(value, str) or not value.strip():
        return ("EMPTY",)
    if len(value) > MAX_ACTIVITY_TEXT_CHARS:
        issues.append("TOO_LONG")
    if _CONTROL_CHAR_RE.search(value):
        issues.append("CONTROL_CHARS")
    scan = _scan_text(value)
    if _HTML_OPEN_RE.search(scan) and _HTML_CLOSE_RE.search(scan):
        issues.append("HTML_MARKUP")
    if _URL_SCHEME_RE.search(scan) or _WWW_RE.search(scan):
        issues.append("URL")
    if _BACKTICK_RE.search(scan):
        issues.append("CODE_FENCE")
    if _PATH_LIKE_RE.search(scan):
        issues.append("PATH")
    return tuple(sorted(set(issues)))


def truth_leak_tokens(value: str) -> tuple[str, ...]:
    """Direct truth-leak findings for ONE activity string (empty = no leak)."""
    scan = _scan_text(value)
    hits: list[str] = []
    for phrase in sorted(_TRUTH_LEAK_PHRASES):
        if phrase in scan:
            hits.append(phrase)
    for word in sorted(_TRUTH_LEAK_WORDS):
        if re.search(rf"\b{re.escape(word)}\b", scan):
            hits.append(word)
    return tuple(hits)


# --------------------------------------------------------------------------- #
# ADV-254/ADV-255/ADV-258/ADV-260 — deterministic bounded normalization for the
# entity/weapon/motive leak filter. The FIRST-VERSION filter matched only the
# exact canonical token, so surname/bare-first-name/ASCII-folded/leet person
# variants and weapon/motive paraphrases passed. The layers below fold BOTH the
# canonical name set and the log text identically (case + ASCII + NFKC +
# title/punctuation stripping; Unicode Cf-format chars are removed so invisible
# ZWSP/ZWNJ splits resolve) and compare word tokens — bounded, deterministic,
# no stemmer. ADV-260: person needles additionally cover concatenated username
# forms ("pbecker" / "paulbecker" / "beckerpaul") and plural surnames
# ("Beckers" -> "becker"). ADV-258: the weapon layer shares the motive layer's
# bounded neutral computer-log carve-out, so a canonical weapon whose name
# contains ordinary words ("computer case", "letter opener", "kitchen knife")
# never over-blocks harmless log rows; only DISTINCTIVE weapon content words
# stay forbidden.
# --------------------------------------------------------------------------- #

_HONORIFIC_TOKENS: frozenset[str] = frozenset(
    {
        "dr", "mr", "mrs", "ms", "mx", "prof", "sir", "madam", "mister",
        "miss", "doktor", "frau", "herr", "her", "his",
    }
)

_ASCII_TRANSLIT = str.maketrans(
    {
        "ä": "a", "å": "a", "æ": "ae", "ç": "c", "é": "e", "è": "e",
        "ê": "e", "ë": "e", "í": "i", "ì": "i", "î": "i", "ï": "i",
        "ñ": "n", "ó": "o", "ò": "o", "ô": "o", "ö": "o", "ø": "o",
        "ú": "u", "ù": "u", "û": "u", "ü": "u", "ý": "y", "ÿ": "y",
        "ß": "ss",
    }
)

# Deterministic leet substitutions (ADV-254: "P4UL B3CK3R" == "Paul Becker").
_LEET_SUBSTITUTIONS = str.maketrans(
    {"a": "4", "e": "3", "i": "1", "o": "0", "s": "5", "t": "7"}
)

_NAME_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")


def _leet_key(token: str) -> str:
    """Deterministic leet-canonical key (a->4, e->3, i->1, o->0, s->5, t->7)."""
    return token.translate(_LEET_SUBSTITUTIONS)


def _fold_ascii(value: str) -> str:
    """Deterministic bounded ASCII fold: NFKC -> casefold -> transliteration.

    ``"Lisa König"`` -> ``"lisa konig"``; ``"P4UL B3CK3R"`` -> ``"p4ul b3ck3r"``.
    Unicode Cf-format characters (U+200B ZWSP, U+200C ZWNJ, U+200D ZWJ,
    U+200E/U+200F LRM/RLM, U+FEFF BOM, ...) are dropped (ADV-260) so invisible
    splits like "Pau\\u200bl" fold to "paul" and resolve to the canonical name.
    """
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.casefold()
    text = text.translate(_ASCII_TRANSLIT)
    text = unicodedata.normalize("NFKD", text)
    # Mn = combining marks (existing); Cf = Unicode format/zero-width class
    # (ADV-260 / ADV-254 residual, defense-in-depth).
    return "".join(
        ch for ch in text if unicodedata.category(ch) not in ("Mn", "Cf")
    )


def _fold_name_tokens(value: str) -> tuple[str, ...]:
    """Deterministic folded, honorific-stripped, punctuation-split tokens.

    ``"Dr. Anna Weiss"`` -> ``("anna", "weiss")``; ``"paul_becker"`` ->
    ``("paul", "becker")``; ``"Paul-Beckers"`` -> ``("paul", "beckers")``.
    """
    folded = _fold_ascii(value)
    parts = [part for part in _NAME_SEPARATOR_RE.split(folded) if part]
    return tuple(part for part in parts if part not in _HONORIFIC_TOKENS)


_MOTIVE_FUNCTION_WORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "as", "at", "be", "been", "being", "but", "by",
        "did", "do", "does", "for", "from", "had", "has", "have", "he",
        "her", "his", "i", "in", "into", "is", "it", "its", "of", "on",
        "or", "out", "over", "she", "than", "that", "the", "their", "them",
        "there", "they", "this", "to", "up", "was", "we", "were", "will",
        "with", "wanted", "would", "you", "your",
    }
)

# Neutral computer-log vocabulary (ADV-258): generic words that legitimately
# appear in ordinary activity-log rows and must NEVER trip the weapon/motive
# word-token filters ("Research document accessed", "opened a document", "data
# synced", "the case file was archived", "New letter received", "the kitchen
# was cleaned"). Bounded and documented; keeps the weapon/motive content-word
# needle sets distinctive. The ADV-258 additions cover the generic/shared nouns
# a realistic canonical weapon can contain ("computer case", "letter opener",
# "kitchen knife", "glass bottle", "paper shredder", ...) — a row is only
# blocked when it carries a weapon-DISTINCTIVE token outside this list.
_NEUTRAL_COMPUTER_WORDS: frozenset[str] = frozenset(
    {
        "access", "accessed", "activity", "application", "applications",
        "autosave", "autosaved", "backup", "background", "browser", "client",
        "cloud", "completed", "computer", "copied", "data", "detected",
        "document",
        "documents", "editor", "entered", "explorer", "file", "files",
        "folder", "folders", "foreground", "keyboard", "local", "login",
        "logged", "mail", "network", "office", "opened", "opening",
        "recorded", "research", "resumed", "saved", "scan", "schedule",
        "server", "service", "session", "sessions", "sleep", "started",
        "sync", "synced", "synchronization", "synchronized", "system",
        "task", "tasks", "update", "updated", "user", "users", "view",
        "viewed", "window", "workspace", "written",
        # ADV-258: generic shared words a canonical weapon name may contain.
        "case", "letter", "opener", "kitchen", "error", "retry", "new",
        "received", "cleaned", "archived", "cable", "glass", "bottle",
        "chain", "pipe", "belt", "wire", "paper", "pillow", "frame",
    }
)


def _person_leak_keys(names: Iterable[str]) -> frozenset[str]:
    """Person-name leak needles: first/last/full tokens, leet variants and the
    ADV-260 concatenated-username / family forms.

    Every canonical person name/id contributes:
    - each folded word token (len >= 2) + its leet form ("becker"/"b3ck3r");
    - the concatenated first+last and last+first needles ("paulbecker",
      "beckerpaul") + leet forms ("p4ulb3ck3r");
    - the first-initial+surname needle ("pbecker") + leet form.
    Bounded: at most ~6 keys per canonical name (T <= 3 tokens).
    """
    keys: set[str] = set()
    for raw in names:
        tokens = _fold_name_tokens(raw)
        for token in tokens:
            if len(token) >= 2:
                keys.add(token)
                keys.add(_leet_key(token))
        if len(tokens) >= 2:
            joined = "".join(tokens)
            keys.add(joined)
            keys.add(_leet_key(joined))
            last_first = "".join(reversed(tokens))
            keys.add(last_first)
            keys.add(_leet_key(last_first))
            initial_surname = tokens[0][0] + tokens[-1]
            keys.add(initial_surname)
            keys.add(_leet_key(initial_surname))
    return frozenset(keys)


def _weapon_leak_keys(names: Iterable[str]) -> frozenset[str]:
    """Weapon-name DISTINCTIVE content words (the near-paraphrase needle set).

    ADV-255 (a): "bronze ceremonial ice pick" -> {bronze, ceremonial, ice,
    pick} — a row naming ANY of the weapon's distinctive words ("a ceremonial
    pick was seized", "a sharp bronze instrument was wiped") is rejected.
    ADV-258: the SAME bounded neutral carve-out the motive layer uses —
    function words and ``_NEUTRAL_COMPUTER_WORDS`` are dropped, so a canonical
    weapon that shares ordinary words with harmless rows ("computer case" ->
    "the case file was archived", "letter opener" -> "New letter received",
    "kitchen knife" -> "the kitchen was cleaned") NEVER over-blocks them; only
    the weapon's DISTINCTIVE content words stay forbidden. "kitchen knife" ->
    {"knife"} (the distinctive weapon-family word remains; the static
    forbidden-content list independently enforces knife/blade/shaft/gun).
    """
    keys: set[str] = set()
    for raw in names:
        for token in _fold_name_tokens(raw):
            if len(token) < 3:
                continue
            if (
                token in _MOTIVE_FUNCTION_WORDS
                or token in _NEUTRAL_COMPUTER_WORDS
            ):
                continue
            keys.add(token)
    return frozenset(keys)


def _motive_leak_keys(names: Iterable[str]) -> frozenset[str]:
    """The motive's SIGNIFICANT content words (bounded, de-genericized).

    Each motive label is tokenized; function words and the neutral
    computer-log vocabulary are dropped so generic rows never trip. The
    remaining distinctive words are the forbidden motive needles (word
    boundaries). "Wanted to steal the research data" -> {"steal"};
    "Cover up the €240,000 embezzlement" -> {"cover", "embezzlement"}.
    """
    keys: set[str] = set()
    for raw in names:
        for token in _fold_name_tokens(raw):
            if len(token) < 4:
                continue
            if (
                token in _MOTIVE_FUNCTION_WORDS
                or token in _NEUTRAL_COMPUTER_WORDS
            ):
                continue
            keys.add(token)
    return frozenset(keys)


# ADV-255 (c) — the small deterministic forbidden-content vocabulary beyond the
# direct truth-leak set. These are the §25 example phrases' diagnostic words
# plus the weapon-family terms; matched with a PREFIX rule so plural/-ed/-ing/
# -ment forms ("payouts", "embezzlement", "weapons") are caught without a
# stemmer. Bounded and deterministic; never a semantic model.
_FORBIDDEN_CONTENT_WORDS: tuple[str, ...] = (
    "stolen", "blackmail", "payout", "affair", "embezzle",
    "knife", "blade", "weapon", "gun", "shaft",
)


def _forbidden_content_hits(tokens: Iterable[str]) -> tuple[str, ...]:
    """Deterministic prefix hits of ``_FORBIDDEN_CONTENT_WORDS``."""
    hits: list[str] = []
    for token in tokens:
        for word in _FORBIDDEN_CONTENT_WORDS:
            if token.startswith(word):
                hits.append(word)
                break
    return tuple(dict.fromkeys(hits))


def _exact_phrase_hits(
    tokens: Iterable[str], scan: str
) -> tuple[str, ...]:
    """Exact folded phrase/word hits (v1 semantics: substring for multi-word
    values, word-boundary for single tokens)."""
    found: list[str] = []
    for raw in tokens:
        token = str(raw or "").strip()
        if not token or len(token) < 3:
            continue
        needle = _scan_text(token)
        if not needle:
            continue
        if " " in needle:
            if needle in scan:
                found.append(token)
        elif re.search(rf"\b{re.escape(needle)}\b", scan):
            found.append(token)
    return tuple(found)


def entity_leak_tokens(
    value: str,
    *,
    person_names: Iterable[str] = (),
    weapon_names: Iterable[str] = (),
    motive_names: Iterable[str] = (),
    location_ids: Iterable[str] = (),
    location_names: Iterable[str] = (),
) -> tuple[str, ...]:
    """Entity/weapon/motive/location leakage findings for ONE activity string.

    Strict-by-default stance (Phase19J §23/§24/§25/§26): the case's own
    canonical names are forbidden in log text by default.

    - **person names / ids** (ADV-254/ADV-260): the canonical name set
      (first/last/full word tokens) is folded on BOTH sides (case+ASCII+NFKC,
      titles/punctuation + Unicode Cf-format stripped) and leet-canonicalized,
      so bare first names, surname-only, ASCII-transliterated, title-less and
      leet spellings are rejected with word-boundary token equality; ADV-260
      additionally rejects concatenated-username forms ("pbecker" /
      "paulbecker" / "beckerpaul" / leet "p4ulb3ck3r"), ZWSP/ZWNJ-split names
      ("Pau\\u200bl B\\u200becker") and plural/possessive surnames ("the
      Beckers' workstation"). Generic words ("user", "operator", "colleague",
      "case file") are untouched unless they coincide with a canonical name.
      Documented bounded residual (LOW, tracked as ADV-260): non-Latin
      homoglyphs and morphological forms beyond a single trailing plural marker
      (e.g. "paulus") stay outside this deterministic needle set.
    - **weapon names / ids** (ADV-255/ADV-258): the whole name is rejected on
      ANY occurrence (v1) AND any DISTINCTIVE word-token of the weapon name
      rejects ("ceremonial pick", "sharp bronze instrument"). ADV-258: weapon
      tokens inside the shared neutral computer-log vocabulary never trip, so
      a canonical weapon that shares ordinary words ("computer case", "letter
      opener", "kitchen knife") never over-blocks harmless rows ("the case
      file was archived", "New letter received", "the kitchen was cleaned").
    - **motive labels / ids** (ADV-255): the whole label is rejected on ANY
      occurrence (v1) AND the label's significant content words (after dropping
      function words + the neutral computer-log vocabulary) are forbidden.
    - **static forbidden-content vocabulary** (ADV-255): "stolen"/"blackmail"/
      "payout"/"affair"/"embezzle" and the weapon family ("knife"/"blade"/
      "weapon"/"gun"/"shaft") are rejected by word-boundary PREFIX match — the
      §25 example phrases can never pass.
    - location **id** tokens (app-owned slugs): always rejected on the exact
      folded slug.
    - location **display names**: rejected when distinctive — a multi-word
      name or a single word of at least ``_DISTINCTIVE_SINGLE_WORD_NAME_LEN``
      characters. Short ambiguous single-word names (e.g. ``office``) are NOT
      name-rejected to avoid over-blocking harmless prose ("Office
      applications updated"); their id token is still rejected.

    Returns the matched source labels (empty when no leak).
    """
    if not isinstance(value, str) or not value.strip():
        return ()

    scan = _scan_text(value)
    tokens = _fold_name_tokens(value)
    text_token_set = frozenset(tokens)
    text_keys = text_token_set | frozenset(_leet_key(t) for t in tokens)

    hits: list[str] = []
    hits.extend(_exact_phrase_hits(person_names, scan))
    hits.extend(_exact_phrase_hits(weapon_names, scan))
    hits.extend(_exact_phrase_hits(motive_names, scan))
    hits.extend(_exact_phrase_hits(location_ids, scan))

    # person-name first/last/full + leet + concatenated-username + Cf-stripped
    # token set (ADV-254 / ADV-260 a+b)
    person_keys = _person_leak_keys(person_names)
    if person_keys & text_keys:
        hits.append("<person-name-token>")
    elif any(
        # ADV-260 (c): plural-surname defense — "Beckers" or leet "b3ck3r5"
        # collapse to the canonical surname/leet form by dropping ONE trailing
        # plural marker; the singular base must itself be a canonical key, so
        # generic "users"/"operators" rows never trip.
        len(t) >= 3
        and (t.endswith("s") or t.endswith("5"))
        and t[:-1] in person_keys
        for t in text_token_set
    ):
        hits.append("<person-name-token>")
    # weapon word-token near-paraphrase (ADV-255 a)
    for token in sorted(_weapon_leak_keys(weapon_names) & text_token_set):
        hits.append(f"weapon-token:{token}")
    # motive significant content words (ADV-255 b)
    for token in sorted(_motive_leak_keys(motive_names) & text_token_set):
        hits.append(f"motive-token:{token}")
    # static forbidden-content words (ADV-255 c)
    hits.extend(_forbidden_content_hits(text_token_set))

    # location display names: distinctive names only (v1 semantics)
    for raw in location_names:
        token = str(raw or "").strip()
        if not token or len(token) < 3 or not _distinctive_name(token):
            continue
        needle = _scan_text(token)
        if not needle:
            continue
        if " " in needle:
            if needle in scan:
                hits.append(raw)
        elif re.search(rf"\b{re.escape(needle)}\b", scan):
            hits.append(raw)

    return tuple(sorted(set(hits)))


_DISTINCTIVE_SINGLE_WORD_NAME_LEN = 8


def _distinctive_name(value: str) -> bool:
    """A display name distinctive enough for whole-name leak rejection.

    Multi-word names (contains a space/hyphen) are distinctive; a single word
    must be long enough not to over-block ordinary vocabulary.
    """
    if " " in value.strip() or "-" in value.strip():
        return True
    return len(re.sub(r"[^A-Za-z0-9]", "", value)) >= _DISTINCTIVE_SINGLE_WORD_NAME_LEN


# --------------------------------------------------------------------------- #
# structured schema parse (Phase19J §7 — closed: extra keys/unknown enum,
# wrong types and nested junk all fail as SCHEMA_INVALID)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ActivityLogEntry:
    """One validated log entry (raw values are validated by ``validate``)."""

    timestamp: str  # ISO-8601 WITH offset (as emitted by the provider)
    activity_type: str  # closed enum token
    activity: str  # safe bounded text

    def to_event(self) -> dict[str, str]:
        """The persisted player-safe presentation event ({time, action})."""
        return {"time": self.timestamp, "action": self.activity}


def parse_activity_log(content: str) -> list[ActivityLogEntry]:
    """Strictly parse the provider's structured activity-log document.

    Raises ``ValueError`` carrying a sanitized structural summary for any
    schema violation (extra/missing keys, wrong types, unknown enum tokens,
    malformed timestamps, non-string activity). The caller maps the failure to
    ``ACTIVITY_LOG_SCHEMA_INVALID`` for repair.
    """
    if not isinstance(content, str) or not content.strip():
        raise ValueError("activity log provider output is empty")
    try:
        data = bounded_json_loads(content)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"activity log is not valid JSON: {exc}") from None
    if not isinstance(data, Mapping):
        raise ValueError("activity log root must be a JSON object")
    extra = set(data) - {"entries"}
    if extra:
        raise ValueError(
            f"activity log has unknown top-level keys {sorted(extra)!r}"
        )
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("activity log 'entries' must be an array")
    if len(raw_entries) > _MAX_PARSED_ENTRIES:
        raise ValueError(
            f"activity log 'entries' exceeds the parse bound "
            f"({_MAX_PARSED_ENTRIES})"
        )
    entries: list[ActivityLogEntry] = []
    for index, item in enumerate(raw_entries):
        where = f"entries[{index}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{where} must be a JSON object")
        extra_keys = set(item) - {"timestamp", "activityType", "activity"}
        if extra_keys:
            raise ValueError(f"{where} has unknown keys {sorted(extra_keys)!r}")
        timestamp = item.get("timestamp")
        activity_type = item.get("activityType")
        activity = item.get("activity")
        if not isinstance(timestamp, str) or not timestamp:
            raise ValueError(f"{where}: 'timestamp' must be a non-empty string")
        try:
            parse_iso8601_to_epoch(timestamp)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{where}: 'timestamp' is not valid ISO-8601 with offset: {exc}"
            ) from None
        if (
            not isinstance(activity_type, str)
            or not activity_type
            or activity_type not in ACTIVITY_LOG_ACTIVITY_TYPES
        ):
            raise ValueError(
                f"{where}: 'activityType' must be one of the closed tokens"
            )
        if not isinstance(activity, str):
            raise ValueError(f"{where}: 'activity' must be a string")
        entries.append(
            ActivityLogEntry(timestamp=timestamp, activity_type=activity_type, activity=activity)
        )
    return entries


# --------------------------------------------------------------------------- #
# deterministic validator (Phase19J §21)
# --------------------------------------------------------------------------- #


def activity_log_window_bounds(
    canonical_tick: int,
    *,
    before_minutes: int = WINDOW_DEFAULT_BEFORE_MINUTES,
    after_minutes: int = WINDOW_DEFAULT_AFTER_MINUTES,
) -> tuple[int, int]:
    """Deterministic app-level window bounds, hard-capped at 120 total minutes.

    The window NEVER exceeds ``WINDOW_HARD_MAX_TOTAL_MINUTES``: each side is
    clamped so ``before + after <= 120`` regardless of the configured values.
    """
    before = max(0, int(before_minutes or 0))
    after = max(0, int(after_minutes or 0))
    if before + after > WINDOW_HARD_MAX_TOTAL_MINUTES:
        ratio_before = before / max(1, before + after)
        budget = WINDOW_HARD_MAX_TOTAL_MINUTES
        before = int(budget * ratio_before)
        after = budget - before
    return (canonical_tick - before * 60, canonical_tick + after * 60)


def validate_activity_log(
    entries: list[ActivityLogEntry],
    *,
    canonical_time: str,
    person_names: Iterable[str] = (),
    weapon_names: Iterable[str] = (),
    motive_names: Iterable[str] = (),
    location_ids: Iterable[str] = (),
    location_names: Iterable[str] = (),
    before_minutes: int = WINDOW_DEFAULT_BEFORE_MINUTES,
    after_minutes: int = WINDOW_DEFAULT_AFTER_MINUTES,
) -> tuple[ActivityLogValidatorCode, ...]:
    """Validate one parsed activity log against every Phase19J §21..§26 rule.

    Returns a deterministic sorted tuple of typed validator codes (EMPTY means
    the log is accepted). The canonical time and the temporal window are
    app-owned; the LLM never decides them.
    """
    codes: set[ActivityLogValidatorCode] = set()
    if not (MIN_ACTIVITY_LOG_ENTRIES <= len(entries) <= MAX_ACTIVITY_LOG_ENTRIES):
        codes.add(ActivityLogValidatorCode.ACTIVITY_LOG_ENTRY_COUNT_INVALID)

    try:
        canonical_tick = parse_iso8601_to_epoch(canonical_time)
    except (TypeError, ValueError):
        # An unparseable canonical time is a server contract error: the caller
        # must never generate a log for it. Fail schema-invalid deterministically.
        codes.add(ActivityLogValidatorCode.ACTIVITY_LOG_SCHEMA_INVALID)
        return tuple(sorted(codes, key=lambda c: c.value))

    window_min, window_max = activity_log_window_bounds(
        canonical_tick, before_minutes=before_minutes, after_minutes=after_minutes
    )

    canonical_occurrences = 0
    seen_ticks: set[int] = set()
    seen_pair: set[tuple[int, str]] = set()
    previous_tick: int | None = None

    for entry in entries:
        try:
            tick = parse_iso8601_to_epoch(entry.timestamp)
        except (TypeError, ValueError):
            # already rejected at parse time; defensive depth
            codes.add(ActivityLogValidatorCode.ACTIVITY_LOG_SCHEMA_INVALID)
            continue

        if previous_tick is not None and tick <= previous_tick:
            codes.add(ActivityLogValidatorCode.ACTIVITY_LOG_TIME_ORDER_INVALID)
        previous_tick = tick

        if tick in seen_ticks:
            codes.add(ActivityLogValidatorCode.ACTIVITY_LOG_TIME_ORDER_INVALID)
        seen_ticks.add(tick)

        # duplicate ENTRY = same instant AND same text (Phase19J §21)
        pair = (tick, entry.activity)
        if pair in seen_pair:
            codes.add(ActivityLogValidatorCode.ACTIVITY_LOG_SCHEMA_INVALID)
        seen_pair.add(pair)

        if tick == canonical_tick:
            canonical_occurrences += 1
        if not (window_min <= tick <= window_max):
            codes.add(ActivityLogValidatorCode.ACTIVITY_LOG_TIME_WINDOW_INVALID)

        unsafe = activity_text_unsafe_tokens(entry.activity)
        if unsafe:
            codes.add(ActivityLogValidatorCode.ACTIVITY_LOG_SCHEMA_INVALID)
        if truth_leak_tokens(entry.activity):
            codes.add(ActivityLogValidatorCode.ACTIVITY_LOG_DIRECT_TRUTH_LEAK)
        if entity_leak_tokens(
            entry.activity,
            person_names=person_names,
            weapon_names=weapon_names,
            motive_names=motive_names,
            location_ids=location_ids,
            location_names=location_names,
        ):
            codes.add(ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK)

    if canonical_occurrences == 0:
        codes.add(ActivityLogValidatorCode.ACTIVITY_LOG_CANONICAL_TIME_MISSING)
    elif canonical_occurrences > 1:
        codes.add(ActivityLogValidatorCode.ACTIVITY_LOG_CANONICAL_TIME_DUPLICATED)

    return tuple(sorted(codes, key=lambda code: code.value))


# --------------------------------------------------------------------------- #
# repair feedback (Phase19J §20 — machine-readable findings ONLY, never the
# raw log text, never CaseTruth beyond the locked temporal constraint)
# --------------------------------------------------------------------------- #


def repair_findings(
    codes: Iterable[ActivityLogValidatorCode],
    *,
    entry_count: int,
    non_chronological: bool = False,
    duplicate_timestamp: bool = False,
) -> tuple[str, ...]:
    """Safe machine-readable repair findings for one failed validation.

    The returned tokens are the ONLY activity-log repair feedback a provider
    ever receives (plus the locked canonical time, re-pasted by the caller).
    They never carry the rejected log content or any truth material.
    """
    present = {getattr(code, "value", None) or str(code) for code in codes}
    findings: list[str] = []
    if ActivityLogValidatorCode.ACTIVITY_LOG_SCHEMA_INVALID.value in present:
        findings.append("SCHEMA_INVALID")
    if ActivityLogValidatorCode.ACTIVITY_LOG_ENTRY_COUNT_INVALID.value in present:
        if entry_count < MIN_ACTIVITY_LOG_ENTRIES:
            findings.append("ENTRY_COUNT_TOO_LOW")
        else:
            findings.append("ENTRY_COUNT_TOO_HIGH")
    if ActivityLogValidatorCode.ACTIVITY_LOG_TIME_ORDER_INVALID.value in present:
        if duplicate_timestamp:
            findings.append("DUPLICATE_TIMESTAMP")
        if non_chronological:
            findings.append("NON_CHRONOLOGICAL")
        if not duplicate_timestamp and not non_chronological:
            findings.append("DUPLICATE_TIMESTAMP")
    if ActivityLogValidatorCode.ACTIVITY_LOG_CANONICAL_TIME_MISSING.value in present:
        findings.append("CANONICAL_TIME_MISSING")
    if ActivityLogValidatorCode.ACTIVITY_LOG_CANONICAL_TIME_DUPLICATED.value in present:
        findings.append("CANONICAL_TIME_DUPLICATED")
    if ActivityLogValidatorCode.ACTIVITY_LOG_TIME_WINDOW_INVALID.value in present:
        findings.append("TIMESTAMP_OUTSIDE_WINDOW")
    if ActivityLogValidatorCode.ACTIVITY_LOG_DIRECT_TRUTH_LEAK.value in present:
        findings.append("TRUTH_LEAK_DETECTED")
    if ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK.value in present:
        findings.append("ENTITY_LEAK_DETECTED")
    return tuple(dict.fromkeys(findings))


def log_validation_detail(
    codes: Iterable[ActivityLogValidatorCode],
    *,
    entry_count: int,
    non_chronological: bool = False,
    duplicate_timestamp: bool = False,
) -> dict[str, Any]:
    """Sanitized validation detail for observability / tests.

    Codes and counts only — NEVER the log text, never a provider response.
    """
    return {
        "validatorCodes": [
            getattr(code, "value", None) or str(code)
            for code in sorted(codes, key=lambda code: code.value)
        ],
        "repairFindings": list(
            repair_findings(
                codes,
                entry_count=entry_count,
                non_chronological=non_chronological,
                duplicate_timestamp=duplicate_timestamp,
            )
        ),
        "entryCount": int(entry_count),
    }


__all__ = [
    "ACTIVITY_LOG_ACTIVITY_TYPES",
    "ACTIVITY_LOG_VERSION_MARKER",
    "ActivityLogEntry",
    "ActivityLogValidatorCode",
    "MAX_ACTIVITY_LOG_ENTRIES",
    "MAX_ACTIVITY_TEXT_CHARS",
    "MIN_ACTIVITY_LOG_ENTRIES",
    "WINDOW_DEFAULT_AFTER_MINUTES",
    "WINDOW_DEFAULT_BEFORE_MINUTES",
    "WINDOW_HARD_MAX_TOTAL_MINUTES",
    "activity_log_window_bounds",
    "activity_text_unsafe_tokens",
    "entity_leak_tokens",
    "log_validation_detail",
    "parse_activity_log",
    "primary_validator_code",
    "repair_findings",
    "truth_leak_tokens",
    "validate_activity_log",
]