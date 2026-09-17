"""Phase 12 — developer-only catalog validation/report CLI (Track A half).

Loads and validates the Asset Oracle manifest through the backend package
(``app.assets``), enumerates every asset, computes per-category/template/
variant/dimension statistics, runs the frozen-template and string-safety
checks, and emits a deterministic JSON report plus a human-readable summary.

Usage (local, build-time, headless — no server, no network, no secrets):

    python tools/catalog_report.py --catalog assets/catalog/catalog.json [--report path]

Exit codes:
    0  catalog loads with ZERO issues (the JSON report records the same)
    2  any validation/detection issue OR an unusable/absent catalog path
       (a deliberately-bad temp catalog therefore exits 2)

Unreadable / unparseable-JSON / non-object inputs exit 2 with a SANITIZED
message on stderr ("catalog path <p> is not valid JSON: ...") — never a
traceback (DEF-064). The JSON report, when requested, still gets written with
the full report shape and ``ok: false``.

The tool imports the backend package by deriving ``<repo>/backend`` from the
script location (like the test suite does), so it runs with the repo's own
Python and never depends on the frontend or on any browser tooling.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# imports (backend package resolved relative to the script, never CWD)
# --------------------------------------------------------------------------- #

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_BACKEND_DIR = _REPO_ROOT / "backend"
for _path in (_BACKEND_DIR,):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.assets.catalog import (  # noqa: E402
    TEMPLATE_VOCABULARY,
    Catalog,
    CatalogError,
    CatalogValidationError,
    load_catalog,
    validate_catalog_data,
)
from app.assets.resolver import (  # noqa: E402
    AssetRequest,
    AssetResolver,
    Provenance,
)

# Forbidden tokens mirrored from the catalog/validators (the manifest loader
# already rejects these; the report re-scans every string incl. variants).
_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "http://",
    "https://",
    "data:",
    "file:",
    "javascript:",
)
_MAX_CATALOG_STRING_LENGTH = 120


def _deep_strings(node) -> list[str]:
    """Every string (keys + values) in a JSON tree, deterministic order."""
    out: list[str] = []
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                out.append(key)
            out.extend(_deep_strings(value))
    elif isinstance(node, (list, tuple)):
        for value in node:
            out.extend(_deep_strings(value))
    return out


# --------------------------------------------------------------------------- #
# report assembly (deterministic)
# --------------------------------------------------------------------------- #


def _scan_forbidden(raw, where: str) -> list[str]:
    """Deep string scan for forbidden tokens / path separators / traversal."""
    findings: list[str] = []
    for text in _deep_strings(raw):
        lowered = text.casefold()
        for token in _FORBIDDEN_TOKENS:
            if token in lowered:
                findings.append(
                    f"{where}: {token!r} found in string {text!r}"
                )
        if "/" in text or "\\" in text or ".." in text:
            findings.append(f"{where}: path separator/traversal in {text!r}")
        if len(text) > _MAX_CATALOG_STRING_LENGTH:
            findings.append(
                f"{where}: string exceeds {_MAX_CATALOG_STRING_LENGTH} chars"
            )
    return sorted(set(findings))


def _template_resource_issues(catalog: Catalog | None, raw_assets: list) -> list[str]:
    """Every composite references a frozen TEMPLATE_VOCABULARY template; no
    non-composite carries a templateId (missing local render resources)."""
    issues: list[str] = []
    if catalog is not None:
        for asset in catalog.assets:
            if asset.render_kind == "composite":
                if asset.template_id not in TEMPLATE_VOCABULARY:
                    issues.append(
                        f"{asset.asset_id}: templateId {asset.template_id!r} "
                        "is not in TEMPLATE_VOCABULARY"
                    )
            elif asset.template_id is not None:
                issues.append(
                    f"{asset.asset_id}: non-composite asset declares a "
                    f"templateId {asset.template_id!r}"
                )
        return sorted(issues)
    # Catalog failed to load: best-effort raw scan.
    for index, item in enumerate(raw_assets):
        if not isinstance(item, dict):
            continue
        if item.get("renderKind") == "composite":
            template = item.get("templateId")
            if template not in TEMPLATE_VOCABULARY:
                issues.append(
                    f"assets[{index}] {item.get('assetId')}: templateId "
                    f"{template!r} is not in TEMPLATE_VOCABULARY"
                )
        elif item.get("templateId") is not None:
            issues.append(
                f"assets[{index}] {item.get('assetId')}: non-composite asset "
                "declares a templateId"
            )
    return sorted(issues)


def _dimensions_table(catalog: Catalog | None, raw_assets: list) -> list[dict]:
    table: list[dict] = []
    if catalog is not None:
        for asset in catalog.assets:
            table.append(
                {
                    "assetId": asset.asset_id,
                    "renderKind": asset.render_kind,
                    "templateId": asset.template_id,
                    "dimensions": {
                        "x": asset.dimensions.x,
                        "y": asset.dimensions.y,
                        "z": asset.dimensions.z,
                    },
                    "category": asset.category,
                }
            )
    else:
        for item in raw_assets:
            if not isinstance(item, dict):
                continue
            dims = item.get("dimensions")
            table.append(
                {
                    "assetId": item.get("assetId"),
                    "renderKind": item.get("renderKind"),
                    "templateId": item.get("templateId"),
                    "dimensions": (
                        {
                            "x": dims.get("x"),
                            "y": dims.get("y"),
                            "z": dims.get("z"),
                        }
                        if isinstance(dims, dict)
                        else None
                    ),
                    "category": item.get("category"),
                }
            )
    return sorted(table, key=lambda row: str(row["assetId"]))


def _variant_inventory(catalog: Catalog | None, raw_assets: list) -> dict:
    inventory: list[dict] = []
    variant_assets = 0
    total_variants = 0
    if catalog is not None:
        for asset in catalog.assets:
            if not asset.variants:
                continue
            variant_assets += 1
            total_variants += len(asset.variants)
            entries = []
            for variant in asset.variants:
                params = {
                    key: {
                        "kind": spec.kind,
                        "allowlist": list(spec.allowlist),
                        "default": spec.default,
                        "min": spec.min_value,
                        "max": spec.max_value,
                    }
                    for key, spec in variant.params.items()
                }
                entries.append({"name": variant.name, "params": params})
            inventory.append(
                {
                    "assetId": asset.asset_id,
                    "templateId": asset.template_id,
                    "variants": entries,
                }
            )
    else:
        for item in raw_assets:
            if not isinstance(item, dict):
                continue
            variants = item.get("variants") or []
            if not variants:
                continue
            variant_assets += 1
            total_variants += len(variants)
            inventory.append(
                {
                    "assetId": item.get("assetId"),
                    "templateId": item.get("templateId"),
                    "variants": variants,
                }
            )
    return {
        "variantAssets": variant_assets,
        "totalVariants": total_variants,
        "assets": sorted(inventory, key=lambda row: str(row["assetId"])),
    }


def _resolution_check(catalog: Catalog | None, raw_assets: list) -> dict:
    """Every declared assetId must resolve to itself (CATALOG_EXACT/ALIAS)."""
    if catalog is None:
        return {"checked": False, "unresolved": []}
    resolver = AssetResolver(catalog)
    unresolved: list[str] = []
    for asset in catalog.assets:
        result = resolver.resolve_request(AssetRequest(requested_name=asset.asset_id))
        if (
            not result.resolved
            or result.ambiguous
            or result.asset_id != asset.asset_id
            or result.provenance not in (Provenance.CATALOG_EXACT, Provenance.CATALOG_ALIAS)
        ):
            unresolved.append(asset.asset_id)
    return {"checked": True, "unresolved": unresolved}


def _brief_error(exc: BaseException) -> str:
    """One-line, truncated, whitespace-collapsed exception text (sanitized)."""
    text = " ".join(str(exc).split())
    if len(text) > 120:
        return text[:120] + "..."
    return text or type(exc).__name__


def _fatal_report(catalog_path: Path, message: str) -> dict:
    """A FULL-SHAPE report for a path that could not be parsed at all.

    Carries every key ``_human_summary`` renders (catalogVersion, fallbackAsset,
    assetCount, ... all present with null/empty defaults) so a malformed path
    can NEVER crash the CLI with a KeyError (DEF-064: clean exit 2 + a
    sanitized message on stderr, never a traceback). ``fatal`` holds the
    sanitized message; the success path sets it to None.
    """
    return {
        "schema": "catalog-report-v1",
        "catalogPath": str(catalog_path),
        "fatal": message,
        "catalogVersion": None,
        "fallbackAsset": None,
        "ok": False,
        "assetCount": 0,
        "categoryCounts": {},
        "renderKindCounts": {},
        "templateCounts": {},
        "compositeAssets": None,
        "renderResourceIssues": [],
        "forbiddenTokenFindings": [],
        "duplicateIds": [],
        "conflictingIdentities": [],
        "issues": [message],
        "dimensionsTable": [],
        "variantInventory": {"variantAssets": 0, "totalVariants": 0, "assets": []},
        "resolution": {"checked": False, "unresolved": []},
    }


def build_report(catalog_path: Path) -> dict:
    """Deterministic report dict for one manifest path.

    Every return path (including unreadable / unparseable / non-object files)
    produces the SAME full report shape, so the human summary can never hit a
    missing key (DEF-064).
    """
    issues: list[str] = []
    schema_issues: list[str] = []
    raw: dict | None = None
    catalog: Catalog | None = None
    raw_assets: list = []

    try:
        raw_text = catalog_path.read_text(encoding="utf-8")
    except OSError as exc:
        return _fatal_report(
            catalog_path,
            f"catalog path {catalog_path} cannot be read: {_brief_error(exc)}",
        )

    try:
        loaded = json.loads(raw_text)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        return _fatal_report(
            catalog_path,
            f"catalog path {catalog_path} is not valid JSON: {_brief_error(exc)}",
        )

    if not isinstance(loaded, dict):
        return _fatal_report(
            catalog_path,
            f"catalog path {catalog_path} is not a catalog document: the root "
            f"must be a JSON object (got {type(loaded).__name__})",
        )
    raw = loaded

    if isinstance(raw, dict):
        schema_issues = sorted(validate_catalog_data(raw))
        if isinstance(raw.get("assets"), list):
            raw_assets = raw["assets"]
            try:
                catalog = load_catalog(catalog_path)
            except CatalogValidationError as exc:
                schema_issues = sorted(set(schema_issues) | set(exc.issues))
            except CatalogError as exc:
                schema_issues = sorted(set(schema_issues) | {str(exc)})

    issues = sorted(set(schema_issues))

    forbidden = _scan_forbidden(raw, "catalog")
    render_issues = _template_resource_issues(catalog, raw_assets)

    category_counts: dict[str, int] = {}
    template_counts: dict[str, int] = {}
    render_kind_counts: dict[str, int] = {}
    if catalog is not None:
        for asset in catalog.assets:
            category_counts[asset.category] = category_counts.get(asset.category, 0) + 1
            render_kind_counts[asset.render_kind] = (
                render_kind_counts.get(asset.render_kind, 0) + 1
            )
            if asset.template_id is not None:
                template_counts[asset.template_id] = (
                    template_counts.get(asset.template_id, 0) + 1
                )
    else:
        for item in raw_assets:
            if not isinstance(item, dict):
                continue
            category = item.get("category")
            if isinstance(category, str):
                category_counts[category] = category_counts.get(category, 0) + 1
            kind = item.get("renderKind")
            if isinstance(kind, str):
                render_kind_counts[kind] = render_kind_counts.get(kind, 0) + 1
            template = item.get("templateId")
            if isinstance(template, str):
                template_counts[template] = template_counts.get(template, 0) + 1

    all_issues = sorted(set(issues) | set(render_issues) | set(forbidden))

    return {
        "schema": "catalog-report-v1",
        "catalogPath": str(catalog_path),
        "fatal": None,
        "catalogVersion": int(raw["catalogVersion"]) if isinstance(raw.get("catalogVersion"), int) else None,
        "fallbackAsset": raw.get("fallbackAsset"),
        "ok": not all_issues if catalog is not None else False,
        "assetCount": len(catalog.assets) if catalog is not None else len(raw_assets),
        "categoryCounts": dict(sorted(category_counts.items())),
        "renderKindCounts": dict(sorted(render_kind_counts.items())),
        "templateCounts": dict(sorted(template_counts.items())),
        "compositeAssets": (
            sum(1 for a in catalog.assets if a.render_kind == "composite")
            if catalog is not None
            else None
        ),
        "renderResourceIssues": render_issues,
        "forbiddenTokenFindings": forbidden,
        "duplicateIds": [i for i in issues if "duplicate assetId" in i],
        "conflictingIdentities": [i for i in issues if "conflicting" in i],
        "issues": all_issues,
        "dimensionsTable": _dimensions_table(catalog, raw_assets),
        "variantInventory": _variant_inventory(catalog, raw_assets),
        "resolution": _resolution_check(catalog, raw_assets),
    }


def _human_summary(report: dict) -> str:
    lines: list[str] = []
    lines.append(f"catalog report: {report['catalogPath']}")
    lines.append(
        f"  catalogVersion={report['catalogVersion']} "
        f"fallback={report['fallbackAsset']} assets={report['assetCount']}"
    )
    if report["categoryCounts"]:
        lines.append("  per-category counts:")
        for category, count in report["categoryCounts"].items():
            lines.append(f"    {category}: {count}")
    lines.append(f"  render kinds: {report['renderKindCounts']}")
    if report["compositeAssets"] is not None:
        lines.append(f"  composite assets: {report['compositeAssets']}")
    variant = report["variantInventory"]
    lines.append(
        f"  variants: {variant['variantAssets']} assets, "
        f"{variant['totalVariants']} declared variants"
    )
    duplicate = report["duplicateIds"] or report["conflictingIdentities"]
    if duplicate:
        lines.append(f"  duplicate/conflicting identities: {len(duplicate)}")
    if report["renderResourceIssues"]:
        lines.append(f"  render-resource issues: {len(report['renderResourceIssues'])}")
    if report["forbiddenTokenFindings"]:
        lines.append(f"  forbidden-token findings: {len(report['forbiddenTokenFindings'])}")
    if not report["resolution"].get("unresolved"):
        lines.append(
            "  resolution: every asset resolves to itself (CATALOG_EXACT/CATALOG_ALIAS)"
            if report["resolution"].get("checked")
            else "  resolution: skipped (catalog did not load)"
        )
    else:
        lines.append(
            f"  resolution: {len(report['resolution']['unresolved'])} "
            "assets did not resolve to themselves"
        )
    lines.append(f"  issues: {len(report['issues'])}")
    for issue in report["issues"]:
        lines.append(f"    - {issue}")
    lines.append(f"  exit code: {'0 (clean)' if report['ok'] else '2 (issues found)'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="catalog_report",
        description="Phase 12 developer-only catalog validation/report CLI.",
    )
    default_catalog = _REPO_ROOT / "assets" / "catalog" / "catalog.json"
    parser.add_argument(
        "--catalog",
        type=Path,
        default=default_catalog,
        help=f"catalog manifest path (default: {default_catalog})",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="optional deterministic JSON report output path",
    )
    args = parser.parse_args(argv)

    catalog_path: Path = args.catalog
    if not catalog_path.exists():
        print(f"catalog report: catalog path does not exist: {catalog_path}", file=sys.stderr)
        return 2

    report = build_report(catalog_path)

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # Fatal (unreadable / unparseable / non-object) inputs: report the
    # sanitized message on stderr and exit 2 — never a traceback (DEF-064).
    fatal = report.get("fatal")
    if fatal:
        print(fatal, file=sys.stderr)
        return 2

    print(_human_summary(report))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())