"""QA-owned independent DEF-067 FIX-READY retest probe (Phase 13 gate).

Asserts the POST-FIX contract: deep-JSON nesting bombs and deep PYTHON
structures are REJECTED deterministically (issues / None / typed errors) at
ALL FOUR entry points with NO uncaught RecursionError, depth-32 is still
accepted, and the deep catalog / environment / provider-script FILE loads
reject cleanly too. Includes independent variants (object-style ``"{"*...``
bomb, mixed arrays, brackets inside JSON strings).

Run:  python e2e/probes/qa-phase13-recursion-retest.py
Exit 0 = fix verified (clean rejection everywhere); exit 1 = any escape/
regression (each failure printed).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.assets.catalog import (  # noqa: E402
    CatalogError,
    load_catalog,
    validate_catalog_data,
)
from app.assets.depthguard import (  # noqa: E402
    BoundedJsonError,
    MAX_STRUCT_NESTING,
    bounded_json_loads,
    bracket_depth,
)
from app.assets.oracle import (  # noqa: E402
    AssetGenerationError,
    GeneratedAssetOracle,
)
from app.assets.spec_provider import AssetSpecResponse  # noqa: E402
from app.assets.specs import (  # noqa: E402
    AssetSpecError,
    parse_asset_spec,
    validate_asset_spec,
)
from app.environments.manifests import (  # noqa: E402
    EnvironmentValidationError,
    load_all_environments,
    validate_environment_data,
)

ARRAY_BOMB = "[" * 10000 + "]" * 10000  # 20 KB < 262144 cap
OBJECT_BOMB = "{" * 4000 + '"k":1' + "}" * 4000  # object-style variant
MIXED_BOMB = "[" * 4000 + "{" * 4000 + "}" * 4000 + "]" * 4000

for _b in (ARRAY_BOMB, OBJECT_BOMB, MIXED_BOMB):
    assert len(_b) < 262144, "bomb must stay under the documented char cap"


class BombProvider:
    def __init__(self, content: str = ARRAY_BOMB):
        self.content = content

    def generate(self, request):  # noqa: ANN001
        return AssetSpecResponse(content=self.content)


def _oracle():
    from app.assets.catalog import load_catalog_from_repo

    return GeneratedAssetOracle(catalog=load_catalog_from_repo())


def _deep_dict(levels: int) -> dict:
    deep: dict = {}
    cursor = deep
    for _ in range(levels):
        cursor["a"] = {}
        cursor = cursor["a"]
    return deep


def _valid_spec(canonical_name: str, parts: list):
    return {
        "canonicalName": canonical_name,
        "category": "evidence",
        "dimensions": {"x": 0.2, "y": 0.2, "z": 0.2},
        "parts": parts,
    }


def _deep_parts(extra_levels: int):
    """Nested-array chain under ``parts``; the dict root is 1 level and each
    nested array adds exactly 1 (the developer/DEF-067 boundary shape)."""
    tail: list = []
    current = tail
    for _ in range(extra_levels):
        nxt: list = []
        current.append(nxt)
        current = nxt
    return tail


def main() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            failures.append(f"{name}: {detail}")

    # ---- 1) validate_asset_spec: clean issue tuples, never raises --------
    for label, bomb in (("array", ARRAY_BOMB), ("object", OBJECT_BOMB), ("mixed", MIXED_BOMB)):
        try:
            issues = validate_asset_spec(bomb)
        except RecursionError as exc:
            check(f"1.validate_asset_spec[{label}]", False, f"RAISED RecursionError: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            check(f"1.validate_asset_spec[{label}]", False, f"raised {type(exc).__name__}: {exc}")
            continue
        check(
            f"1.validate_asset_spec[{label}]",
            any("exceeds the maximum" in i for i in issues),
            f"expected a nesting issue, got issues={issues!r}",
        )

    # ~3000-deep python dict -> deterministic issue
    try:
        deep_issues = validate_asset_spec(_deep_dict(3000))
    except RecursionError as exc:
        check("1.validate_asset_spec[py3000]", False, f"RAISED RecursionError: {exc}")
        deep_issues = ()
    check(
        "1.validate_asset_spec[py3000]",
        any("nesting depth" in i and "exceeds the maximum" in i for i in deep_issues),
        f"expected a nesting issue, got issues={deep_issues!r}",
    )

    # ---- 2) parse_asset_spec: None (non_throwing) / AssetSpecError ------
    doc = ARRAY_BOMB
    try:
        parsed = parse_asset_spec(doc, non_throwing=True)
    except RecursionError as exc:
        check("2.parse[array] non_throwing", False, f"RAISED RecursionError: {exc}")
        parsed = object()
    check("2.parse[array] non_throwing", parsed is None, f"expected None, got {parsed!r}")
    try:
        parse_asset_spec(doc, non_throwing=False)
        check("2.parse[array] non_throwing=False", False, "did NOT raise AssetSpecError")
    except RecursionError as exc:
        check("2.parse[array] non_throwing=False", False, f"RAISED RecursionError: {exc}")
    except AssetSpecError:
        pass
    try:
        parsed_obj = parse_asset_spec(OBJECT_BOMB, non_throwing=True)
    except RecursionError as exc:
        check("2.parse[object]", False, f"RAISED RecursionError: {exc}")
        parsed_obj = object()
    check("2.parse[object].none", parsed_obj is None, f"expected None, got {parsed_obj!r}")

    # ---- 3) resolve_or_generate: FALLBACK resolution + sanitized error ---
    oracle = _oracle()
    try:
        outcome = oracle.resolve_or_generate(
            {"requestedName": "bomb"},
            spec_provider=BombProvider(),
            force_generate=True,
        )
    except RecursionError as exc:
        check("3.resolve_or_generate", False, f"leaked RecursionError: {exc}")
        outcome = None  # type: ignore[assignment]
    if outcome is not None:
        check("3.resolve_or_generate.resolution", outcome.resolution is not None, "resolution is None")
        check("3.resolve_or_generate.generated", outcome.generated is None, "generated should be None on provider failure")
        check(
            "3.resolve_or_generate.error",
            outcome.error is not None and "nesting" in outcome.error.lower(),
            f"expected sanitized nesting error, got error={outcome.error!r}",
        )

    # ---- 4) generate_and_stage: typed AssetGenerationError, never raw -----
    try:
        oracle.generate_and_stage({"requestedName": "bomb"}, BombProvider())
        check("4.generate_and_stage", False, "did NOT raise AssetGenerationError")
    except RecursionError as exc:
        check("4.generate_and_stage", False, f"leaked RecursionError: {exc}")
    except AssetGenerationError as exc:
        check("4.generate_and_stage.msg", "nesting" in str(exc).lower(), f"unexpected message: {exc}")

    # ---- 5) depth-32 boundary: exactly 32 accepted, 33 rejected --------
    assert MAX_STRUCT_NESTING == 32
    at_limit = _valid_spec("Edge", _deep_parts(30))
    over_limit = _valid_spec("Edge", _deep_parts(31))
    check(
        "5.depth32.accepted",
        not any("nesting depth" in i for i in validate_asset_spec(at_limit)),
        "a depth-32 document must NOT trip the nesting guard",
    )
    check(
        "5.depth33.rejected",
        any("nesting depth" in i and "exceeds the maximum" in i for i in validate_asset_spec(over_limit)),
        "a depth-33 document must trip the nesting guard",
    )
    # JSON-string boundary: a 32-deep array decodes via bounded_json_loads;
    # a 33-deep one is rejected with BoundedJsonError.
    depth_32_json = "[" * 32 + "0" + "]" * 32  # 32 bracket levels (== MAX)
    depth_33_json = "[" * 33 + "0" + "]" * 33  # 33 > MAX -> must reject
    assert bracket_depth(depth_32_json) == 32
    assert bracket_depth(depth_33_json) == 33
    try:
        bounded_json_loads(depth_32_json)
        bounded_ok = True
    except Exception as exc:  # noqa: BLE001
        bounded_ok = False
        check("5.depth32.bounded_json", False, f"32-deep JSON rejected: {exc}")
    if bounded_ok:
        check("5.depth32.bounded_json", True)
    try:
        bounded_json_loads(depth_33_json)
        check("5.depth33.bounded_json", False, "33-deep JSON was NOT rejected")
    except BoundedJsonError:
        pass
    except RecursionError as exc:
        check("5.depth33.bounded_json", False, f"RAISED RecursionError: {exc}")
    except Exception as exc:  # noqa: BLE001
        check("5.depth33.bounded_json", False, f"raised {type(exc).__name__}: {exc}")
    try:
        bounded_json_loads(ARRAY_BOMB)
        check("5.bomb.bounded_json", False, "bomb not rejected by bounded_json_loads")
    except BoundedJsonError:
        pass
    except RecursionError as exc:
        check("5.bomb.bounded_json", False, f"RAISED RecursionError: {exc}")
    except Exception as exc:  # noqa: BLE001
        check("5.bomb.bounded_json", False, f"raised {type(exc).__name__}: {exc}")

    # ---- 6) brackets inside JSON strings are ignored by the scan ---------
    # "x" nests [[1]] three levels -> max bracket depth 3 comes from the REAL
    # structure, never from the brackets inside the string literals.
    in_string = '{"name": "a[b]c", "note": "[[[", "x": [[1]]}'
    escaped = '{"k": "say \\"hi\\" [ still a string", "n": [[1]], "m": "}"}'
    check("6.bracket_depth.in_string", bracket_depth(in_string) == 3, f"got {bracket_depth(in_string)}")
    check("6.bracket_depth.escaped", bracket_depth(escaped) == 3, f"got {bracket_depth(escaped)}")
    try:
        a = bounded_json_loads(in_string)
        b = bounded_json_loads(escaped)
        check("6.bounded.decode.in_string", isinstance(a, dict) and isinstance(b, dict), "valid JSON with brackets in strings must decode")
    except Exception as exc:  # noqa: BLE001
        check("6.bounded.decode.in_string", False, f"raised {type(exc).__name__}: {exc}")

    # ---- 7) deep catalog FILE load rejects cleanly (CatalogError) --------
    with tempfile.TemporaryDirectory() as td:
        bomb_cat = Path(td) / "catalog.json"
        bomb_cat.write_text(json.dumps(_deep_dict(300)), encoding="utf-8")
        try:
            load_catalog(bomb_cat)
            check("7.catalog.file", False, "catalog bomb did NOT raise CatalogError")
        except RecursionError as exc:
            check("7.catalog.file", False, f"RAISED RecursionError: {exc}")
        except CatalogError as exc:
            check("7.catalog.file.msg", "nesting" in str(exc), f"unexpected message: {exc}")
        # valid catalog still loads from the loader family
        real = REPO_ROOT / "assets" / "catalog" / "catalog.json"
        try:
            real_cat = load_catalog(real)
            check("7.catalog.real", real_cat.catalog_version >= 1, "real catalog must still load")
        except Exception as exc:  # noqa: BLE001
            check("7.catalog.real", False, f"real catalog failed: {type(exc).__name__}: {exc}")
    # validate_catalog_data on a deep python structure -> nesting issue
    cat_issues = validate_catalog_data(_deep_dict(300))
    check(
        "7.catalog.data.deep",
        any("nesting depth" in i and "exceeds the maximum" in i for i in cat_issues),
        f"expected nesting issue, got {cat_issues!r}",
    )

    # ---- 8) deep environment-manifest FILE load rejects cleanly ----------
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "bomb.json").write_text(json.dumps(_deep_dict(300)), encoding="utf-8")
        try:
            load_all_environments(directory=td)
            check("8.environment.file", False, "environment bomb did NOT raise EnvironmentValidationError")
        except RecursionError as exc:
            check("8.environment.file", False, f"RAISED RecursionError: {exc}")
        except EnvironmentValidationError as exc:
            check(
                "8.environment.file.msg",
                any("nesting" in i for i in exc.issues),
                f"unexpected issues: {exc.issues!r}",
            )
    env_issues = validate_environment_data(_deep_dict(300))
    check(
        "8.environment.data.deep",
        any("nesting depth" in i and "exceeds the maximum" in i for i in env_issues),
        f"expected nesting issue, got {env_issues!r}",
    )

    # ---- 9) provider-script loader rejects cleanly (BoundedJsonError) ----
    from app.services.generation import _bounded_provider_script_load

    with tempfile.TemporaryDirectory() as td:
        bomb_script = Path(td) / "fake.json"
        bomb_script.write_text(json.dumps(_deep_dict(300)), encoding="utf-8")
        try:
            _bounded_provider_script_load(bomb_script)
            check("9.provider_script", False, "deep script did NOT raise")
        except RecursionError as exc:
            check("9.provider_script", False, f"RAISED RecursionError: {exc}")
        except BoundedJsonError:
            pass  # the loader is wired to a caller converting this to ProviderConfigError

    print(f"\nDEF-067 RETEST: {len(failures)} failure(s)")
    for f in failures:
        print(f"  FAIL - {f}")
    if not failures:
        print("ALL CLEAN-REJECTION CHECKS PASS (no uncaught RecursionError anywhere)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())