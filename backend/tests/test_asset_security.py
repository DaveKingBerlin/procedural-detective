"""Phase 10 — RAW asset-request security validation.

Covers the Phase 10 required security tests: arbitrary URLs (http/https/data
/file/javascript), path traversal, absolute paths, forbidden executable tokens
(script/handler/shader/function/eval), control characters (NUL included),
oversized strings/arrays, and non-string types. The resolver gate raises
``AssetRequestValidationError`` for unsafe payloads BEFORE any resolution, and
validation never raises raw exceptions.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.assets import (
    AssetRequest,
    AssetRequestValidationError,
    MAX_ASSET_STRING_LENGTH,
    resolve,
    validate_asset_request,
)


def _issues(payload) -> tuple[str, ...]:
    return validate_asset_request(payload)


def test_http_url_rejected():
    issues = _issues({"requestedName": "http://evil.example/asset.glb"})
    assert any("forbidden URL scheme 'http:'" in issue for issue in issues)


def test_https_url_rejected():
    issues = _issues({"requestedName": "https://evil.example/x"})
    assert any("forbidden URL scheme 'https:'" in issue for issue in issues)


def test_data_url_rejected():
    issues = _issues({"requestedName": "data:text/html;base64,PHN0"})
    assert any("forbidden URL scheme 'data:'" in issue for issue in issues)


def test_javascript_url_rejected():
    issues = _issues({"requestedName": "javascript:alert(1)"})
    assert any("forbidden URL scheme 'javascript:'" in issue for issue in issues)


def test_file_url_rejected():
    issues = _issues({"requestedName": "file:///etc/passwd"})
    assert any("forbidden URL scheme 'file:'" in issue for issue in issues)


def test_url_smuggled_in_hints_and_tags_rejected():
    issues = _issues(
        {
            "requestedName": "knife",
            "categoryHint": "evidence",
            "subtypeHint": "javascript:void(0)",
            "tags": ["ok", "data:text/html,oops"],
        }
    )
    joined = "\n".join(issues)
    assert "javascript:" in joined
    assert "data:" in joined


def test_path_traversal_rejected():
    for name in ("../../x", "..\\x", "../..", "a/../../b"):
        issues = _issues({"requestedName": name})
        assert any("path traversal" in issue for issue in issues), name
        assert any("path separator" in issue for issue in issues)


def test_absolute_paths_rejected():
    for name in ("/usr/bin/sh", "\\windows\\system32\\x", "C:\\evil\\x", "C:/evil/x"):
        issues = _issues({"requestedName": name})
        assert any("absolute path" in issue for issue in issues), name


def test_forbidden_tokens_rejected():
    for token in ("script", "handler", "shader", "function", "eval"):
        issues = _issues({"requestedName": "my " + token + " thing"})
        assert any(f"forbidden token '{token}'" in issue for issue in issues), token
    # Prose that merely CONTAINS a token is NOT a token (word boundaries).
    clean = _issues({"requestedName": "functionality evaluator"})
    assert clean == ()


def test_control_characters_rejected():
    for name in ("bad\x00name", "bad\x1fname", "bad\tname", "bad\r\nname"):
        issues = _issues({"requestedName": name})
        assert any("control character" in issue for issue in issues), repr(name)


def test_oversized_string_rejected():
    name = "x" * (MAX_ASSET_STRING_LENGTH + 1)
    issues = _issues({"requestedName": name})
    assert any(f"exceeds {MAX_ASSET_STRING_LENGTH} characters" in issue for issue in issues)


def test_oversized_arrays_rejected():
    issues = _issues(
        {
            "requestedName": "knife",
            "tags": [f"t{i}" for i in range(17)],
            "requiredEvidenceCapabilities": [f"c{i}" for i in range(9)],
        }
    )
    joined = "\n".join(issues)
    assert "tags: exceeds the maximum of 16 entries" in joined
    assert (
        "requiredEvidenceCapabilities: exceeds the maximum of 8 entries" in joined
    )


def test_non_string_types_rejected():
    issues = _issues(
        {
            "requestedName": 12345,
            "categoryHint": {"secret": 1},
            "subtypeHint": ["list"],
            "tags": ["ok", 7, None],
            "requiredEvidenceCapabilities": {"not": "list"},
        }
    )
    joined = "\n".join(issues)
    assert "requestedName: must be a non-empty string" in joined
    assert "categoryHint: must be a non-empty string" in joined
    assert "subtypeHint: must be a non-empty string" in joined
    assert "tags[1]: must be a non-empty string" in joined
    assert "tags[2]: must be a non-empty string" in joined
    assert (
        "requiredEvidenceCapabilities: must be an array of non-empty strings"
        in joined
    )


def test_missing_requested_name_rejected():
    issues = _issues({"categoryHint": "evidence"})
    assert any("requestedName" in issue for issue in issues)


def test_empty_requested_name_rejected():
    issues = _issues({"requestedName": "   "})
    assert issues


def test_clean_payload_has_no_issues():
    assert _issues(
        {
            "requestedName": "kitchen knife",
            "categoryHint": "evidence",
            "subtypeHint": "sharp",
            "tags": ["weapon", "blade"],
            "styleHints": ["warm", "moody"],
        }
    ) == ()


def test_issues_are_sorted_and_deduplicated():
    first = validate_asset_request({"requestedName": "z-./http://x"})
    second = validate_asset_request({"requestedName": "z-./http://x"})
    assert first == second
    assert first == tuple(sorted(set(first)))
    assert len(first) == len(set(first))


def test_validate_never_raises_on_garbage():
    for payload in (None, 42, "text", ["list"], {"assets": []}):
        issues = validate_asset_request(payload)
        assert isinstance(issues, tuple)
        assert all(isinstance(issue, str) for issue in issues)


def test_resolve_gates_unsafe_raw_payload():
    """The resolve() gateway rejects unsafe raw payloads BEFORE resolution."""
    with pytest.raises(AssetRequestValidationError) as excinfo:
        resolve({"requestedName": "http://evil.example/x"})
    assert any("URL scheme" in issue for issue in excinfo.value.issues)
    # A safe but unknown name still falls back explicitly (never nonsense).
    fallback = resolve({"requestedName": "a magical crystal orb"})
    assert fallback.asset_id == "PROP_FALLBACK_01"
    assert fallback.provenance.value == "FALLBACK"


def test_typed_asset_request_path_is_trusted_and_deterministic():
    """Typed AssetRequest callers (internal pipeline) skip the raw gate."""
    result = resolve(AssetRequest(requested_name="chef knife"))
    assert result.asset_id == "PROP_KITCHEN_KNIFE_01"
    assert result.provenance.value == "CATALOG_ALIAS"


# --------------------------------------------------------------------------- #
# DEF-061 — REQUEST-GATE EVASIONS: NFKC-normalized scan pass + %-encoding /
# entity-colon / whitespace-split / fullwidth evasions.
# --------------------------------------------------------------------------- #


def test_html_entity_colon_scheme_rejected():
    for name in (
        "javascript&colon;alert(1)",
        "javascript&#58;alert(1)",
        "data&colon;text/html,oops",
        "file&#58;//etc/passwd",
    ):
        issues = _issues({"requestedName": name})
        assert any("forbidden URL scheme" in issue for issue in issues), name


def test_percent_encoded_traversal_rejected():
    for name in ("%2e%2e/x", "a%2e%2e%2fb", "%2e%2e%2fetc%2fpasswd", "x%2e%2e"):
        issues = _issues({"requestedName": name})
        assert any(
            "percent-encoded path traversal" in issue for issue in issues
        ), name


def test_fullwidth_separators_and_colon_rejected():
    # Fullwidth solidus "／／" (U+FF0F) NFKC-normalizes to "//" -> separator.
    issues = _issues({"requestedName": "\uff0f\uff0fetc"})
    assert any("path separator" in issue for issue in issues)
    # Fullwidth colon "：" (U+FF1A) NFKC-normalizes to ":" -> scheme scan.
    issues = _issues({"requestedName": "http\uff1a//x"})
    assert any("forbidden URL scheme 'http:'" in issue for issue in issues)


def test_fullwidth_letters_composing_forbidden_token_rejected():
    # Fullwidth 'ｓｃｒｉｐｔ' (U+FF53...) NFKC-normalizes to 'script'.
    issues = _issues({"requestedName": "\uff53\uff43\uff52\uff49\uff50\uff54 thing"})
    assert any("forbidden token 'script'" in issue for issue in issues)


def test_whitespace_split_scheme_rejected():
    issues = _issues({"requestedName": "java script:"})
    assert any(
        "forbidden URL scheme 'javascript:'" in issue for issue in issues
    )
    issues = _issues({"requestedName": "data :text/html"})
    assert any("forbidden URL scheme 'data:'" in issue for issue in issues)


def test_benign_names_still_pass():
    """The NFKC/normalized pass must not reject genuine object names."""
    for name in ("kitchen knife", "blue vase", "letter opener", "notebook"):
        assert _issues({"requestedName": name}) == (), name