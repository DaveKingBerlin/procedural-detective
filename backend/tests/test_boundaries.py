"""Architectural import/boundary separation tests (Phase3 requirement 13).

Static AST scans over ``backend/app`` that assert:

(a) none of app.domain.solvers / eligibility / evidence / rules / time_interval
    / public / proof / solver / inputs imports app.domain.truth;
(b) app.api and app.schemas never import app.domain.truth;
(c) truth.py contains no Pydantic BaseModel (and no pydantic import);
(d) app.validation.solution (the ONLY permitted consumer) DOES import
    app.domain.truth (positive control so the scan has teeth).

This is enforcement by architecture — the AST walker treats imports as source
of truth rather than relying on comments.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BACKEND_DIR = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND_DIR / "app"


def _module_path(rel_path: Path) -> str:
    parts = rel_path.with_suffix("").parts
    return "app." + ".".join(parts)


def _resolve_relative_import(package: str | None, level: int, module: str) -> str | None:
    """Resolve a relative import to an absolute dotted module path.

    ``level`` is the number of leading dots (``.`` == 1, ``..`` == 2, ...);
    ``module`` is the module text after the dots (possibly empty). Walk up
    ``level - 1`` components from ``package`` (the importing module's
    ``__package__``), then append ``module``.
    """
    if package is None:
        return None
    parts = package.split(".")
    drop = min(level - 1, len(parts))
    base_parts = parts[: len(parts) - drop] or parts[:1]
    base = ".".join(base_parts)
    if not module:
        return base
    return f"{base}.{module}"


def _collect_import_targets(source: str, package: str | None = None) -> set[str]:
    """Resolve EVERY import in ``source`` to absolute dotted module paths.

    Handles both absolute imports and relative imports (``from .x import y`` /
    ``from ..x import y`` / ``from . import y``) using the importing module's
    ``__package__`` context (DEF-024). ``package`` must be the dotted package
    name of the module containing ``source``, e.g. ``"app.domain.solvers"``.

    Returns the set of absolute module paths referenced by the imports
    (including the resolved module and each imported member for ``ImportFrom``;
    star imports are skipped — they are not allowed in this project).
    """
    tree = ast.parse(source)
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            names = [alias.name for alias in node.names if alias.name != "*"]
            level = node.level  # number of leading dots (0 == absolute import)
            module = node.module or ""
            if level:
                resolved = _resolve_relative_import(package, level, module)
                if resolved is None:
                    continue
                targets.add(resolved)
                for name in names:
                    targets.add(f"{resolved}.{name}")
            else:
                targets.add(module)
                for name in names:
                    targets.add(f"{module}.{name}")
    return targets


def _truth_targets(targets: set[str]) -> set[str]:
    return {t for t in targets if t == "app.domain.truth" or t.startswith("app.domain.truth")}


def _scan() -> dict[str, set[str]]:
    """module -> set of app.domain.truth import targets found."""
    result: dict[str, set[str]] = {}
    for path in sorted(APP_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = _module_path(path.relative_to(APP_DIR))
        package = module.rsplit(".", 1)[0] if "." in module else None
        targets = _truth_targets(
            _collect_import_targets(path.read_text(encoding="utf-8"), package=package)
        )
        if targets:
            result[module] = targets
    return result


# -- DEF-024: pure-function relative-import resolution (defense in depth) ----

def test_collect_import_targets_absolute_from_import():
    source = "from app.domain.truth import CaseTruth"
    targets = _collect_import_targets(source)
    assert targets == {"app.domain.truth", "app.domain.truth.CaseTruth"}
    assert _truth_targets(targets) == {"app.domain.truth", "app.domain.truth.CaseTruth"}


def test_collect_import_targets_relative_one_level_up():
    # Inside a solver module (package app.domain.solvers), `from ..truth import X`
    # resolves to app.domain.truth — the PRIMARY-SAFETY evasion a solver could
    # attempt. It must be caught.
    source = "from ..truth import CaseTruth"
    targets = _collect_import_targets(source, package="app.domain.solvers")
    assert "app.domain.truth" in targets
    assert "app.domain.truth.CaseTruth" in targets
    assert _truth_targets(targets) == {"app.domain.truth", "app.domain.truth.CaseTruth"}


def test_collect_import_targets_relative_multilevel():
    source = "from ...truth import CaseTruth"
    targets = _collect_import_targets(source, package="app.domain.solvers")
    # Two levels up from app.domain.solvers is app; `...truth` -> app.truth.
    assert targets == {"app.truth", "app.truth.CaseTruth"}


def test_collect_import_targets_import_statement():
    source = "import app.domain.truth"
    targets = _collect_import_targets(source)
    assert "app.domain.truth" in targets
    assert _truth_targets(targets) == {"app.domain.truth"}


def test_collect_import_targets_from_package_import_submodule():
    source = "from app.domain import truth"
    targets = _collect_import_targets(source)
    assert "app.domain" in targets
    assert "app.domain.truth" in targets
    assert _truth_targets(targets) == {"app.domain.truth"}


def test_collect_import_targets_relative_from_same_package():
    # `from .evidence import EvidenceFact` inside app.domain.solvers resolves
    # to app.domain.solvers.evidence — NOT truth.
    source = "from .evidence import EvidenceFact"
    targets = _collect_import_targets(source, package="app.domain.solvers")
    assert targets == {"app.domain.solvers.evidence", "app.domain.solvers.evidence.EvidenceFact"}
    assert _truth_targets(targets) == set()


def test_collect_import_targets_without_package_context_does_not_crash():
    # A module with no package context must not raise and simply resolve
    # absolute imports.
    targets = _collect_import_targets("from app.domain.truth import X", package=None)
    assert "app.domain.truth" in targets


# Phase 4 truth-aware lifecycle modules (the ONLY new allowed truth importers,
# besides the truth-aware validation stage).
_P4_TRUTH_AWARE = frozenset(
    {
        "app.validation.solution",
        "app.generation.controller",
        "app.generation.pipeline",
        "app.generation.publish",
    }
)


def test_no_solver_or_public_domain_module_imports_truth():
    allowed = _P4_TRUTH_AWARE
    offenders = _scan()
    for module, targets in sorted(offenders.items()):
        assert module in allowed, (
            f"{module} must not import app.domain.truth (found {sorted(targets)}); "
            "only the truth-aware validation/lifecycle modules may touch truth."
        )


def test_api_and_schemas_never_import_truth():
    forbidden_prefixes = ("app.api", "app.schemas")
    for module, targets in sorted(_scan().items()):
        if module.startswith(forbidden_prefixes):
            raise AssertionError(
                f"{module} imports {sorted(targets)} — CaseTruth must never reach "
                "HTTP/OpenAPI/DTOs (REQUIREMENTS 41.4)"
            )


def test_truth_never_reaches_http_or_schemas_via_validation():
    # A belt-and-braces check: validation may touch truth, but api/schemas must
    # not even import the validation *solution* module (which is truth-aware).
    for path in sorted((APP_DIR / "api").rglob("*.py")) + sorted((APP_DIR / "schemas").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        module = _module_path(path.relative_to(APP_DIR))
        package = module.rsplit(".", 1)[0] if "." in module else None
        targets = _collect_import_targets(source, package=package)
        assert not any(
            t == "app.validation.solution" or t.startswith("app.validation.solution")
            for t in targets
        ), f"{path} must not import the truth-aware validation module"


def test_truth_module_has_no_pydantic_base_model():
    source = (APP_DIR / "domain" / "truth.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    # No pydantic imports at all.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] == "pydantic" for a in node.names), (
                "truth.py must not import pydantic"
            )
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "pydantic", (
                "truth.py must not import from pydantic"
            )

    # No class derives from BaseModel (or anything named BaseModel).
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = [
                _base_name(base)
                for base in node.bases
            ]
            assert "BaseModel" not in bases, (
                f"truth.py class {node.name} must not derive from BaseModel"
            )


def _base_name(node: ast.AST) -> str:
    """Best-effort textual name of a class base expression."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return "<expression>"


def _imported_pydantic(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            a.name.split(".")[0] == "pydantic" for a in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "pydantic":
            return True
    return False


def test_solver_visible_domain_modules_never_import_pydantic():
    # The domain (solvers + eligibility + evidence + rules + time algebra +
    # public + proof) must stay Pydantic-free so it can never be serialized
    # accidentally as public DTOs/OpenAPI schemas.
    domain_dir = APP_DIR / "domain"
    for path in sorted(domain_dir.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "truth.py":
            continue
        source = path.read_text(encoding="utf-8")
        assert not _imported_pydantic(source), f"{path} imports pydantic"


def test_validation_solution_is_the_only_truth_importer(positive_control=True):
    offenders = _scan()
    assert "app.validation.solution" in offenders
    assert "app.domain.truth" in offenders["app.validation.solution"]
    # The Phase 4 truth-aware lifecycle modules must import truth (positive
    # control) and NO other module may.
    assert "app.generation.pipeline" in offenders
    assert "app.generation.publish" in offenders
    assert set(offenders.keys()) == {"app.validation.solution", "app.generation.pipeline", "app.generation.publish"}


# ---------------------------------------------------------------------------
# Phase 4 boundary additions (spec test 29): generation-module separation.
# ---------------------------------------------------------------------------


def _validation_targets(targets: set[str]) -> set[str]:
    return {
        t
        for t in targets
        if t == "app.validation" or t.startswith("app.validation")
    }


def test_generation_primitives_never_import_truth_or_validation():
    """The delivered primitives are data/schema layers: no truth, no validation."""
    primitives = {
        "app.generation.provider",
        "app.generation.fake_provider",
        "app.generation.live_provider",
        "app.generation.parser",
        "app.generation.safety",
        "app.generation.schemas",
        "app.generation.constraints",
        "app.generation.prompt",
    }
    for path in sorted(APP_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = _module_path(path.relative_to(APP_DIR))
        if module not in primitives:
            continue
        package = module.rsplit(".", 1)[0] if "." in module else None
        targets = _collect_import_targets(path.read_text(encoding="utf-8"), package=package)
        forbidden = sorted(_truth_targets(targets) | _validation_targets(targets))
        assert not forbidden, (
            f"primitive module {module} must not import app.domain.truth or "
            f"app.validation material (found {forbidden})"
        )


def test_generation_modules_never_import_truth_except_truth_aware_lifecycle():
    """No app.generation module may import truth except {controller,pipeline,publish}."""
    allowed = {"app.generation.controller", "app.generation.pipeline", "app.generation.publish"}
    for module, targets in sorted(_scan().items()):
        if module.startswith("app.generation") and module not in allowed:
            raise AssertionError(
                f"{module} imports {sorted(targets)} — only the truth-aware "
                "lifecycle modules {controller, pipeline, publish} may touch truth."
            )


def test_generation_recovery_and_reporting_modules_never_import_truth():
    """admission/budgets/clock/ids/report/state_machine stay truth-free."""
    forbidden_prefix_modules = {
        "app.generation.admission",
        "app.generation.budgets",
        "app.generation.clock",
        "app.generation.ids",
        "app.generation.report",
        "app.generation.state_machine",
    }
    for module in forbidden_prefix_modules:
        path = APP_DIR / (module.replace("app.", "", 1).replace(".", "/") + ".py")
        package = module.rsplit(".", 1)[0] if "." in module else None
        targets = _truth_targets(
            _collect_import_targets(path.read_text(encoding="utf-8"), package=package)
        )
        assert not targets, f"{module} must not import app.domain.truth (found {sorted(targets)})"


def test_solver_domain_still_never_imports_truth_or_validation():
    """Phase 3 solvers/eligibility/evidence/rules/time_interval remain pure."""
    domain_modules = {
        "app.domain.solver",
        "app.domain.eligibility",
        "app.domain.evidence",
        "app.domain.rules",
        "app.domain.time_interval",
        "app.domain.public",
        "app.domain.proof",
        "app.domain.inputs",
        "app.domain.solvers.who_solver",
        "app.domain.solvers.why_solver",
        "app.domain.solvers.weapon_solver",
        "app.domain.solvers.when_solver",
    }
    for module in domain_modules:
        path = APP_DIR / (module.replace("app.", "", 1).replace(".", "/") + ".py")
        package = module.rsplit(".", 1)[0] if "." in module else None
        targets = _collect_import_targets(path.read_text(encoding="utf-8"), package=package)
        forbidden = sorted(_truth_targets(targets) | _validation_targets(targets))
        assert not forbidden, (
            f"{module} must stay free of app.domain.truth and app.validation "
            f"(found {forbidden})"
        )


def test_api_and_schemas_never_import_domain_validation_or_generation():
    """app.api / app.schemas must not import domain, validation or generation."""
    forbidden_bases = ("app.domain", "app.validation", "app.generation")
    for path in sorted((APP_DIR / "api").rglob("*.py")) + sorted(
        (APP_DIR / "schemas").rglob("*.py")
    ):
        if "__pycache__" in path.parts:
            continue
        module = _module_path(path.relative_to(APP_DIR))
        package = module.rsplit(".", 1)[0] if "." in module else None
        targets = _collect_import_targets(path.read_text(encoding="utf-8"), package=package)
        hits = sorted(
            t
            for t in targets
            if any(t == base or t.startswith(base + ".") for base in forbidden_bases)
        )
        assert not hits, (
            f"{module} must not import domain/validation/generation material "
            f"(found {hits}) — API/DTOs stay schema-only."
        )


# ---------------------------------------------------------------------------
# Phase 14_5 boundary addition: the AssetSpecProvider / procedural-asset
# surface NEVER receives CaseTruth (REQUIREMENT + Phase 14_5 deliverable 2:
# the provider request carries only the bounded ObjectRequirement surface).
# ---------------------------------------------------------------------------


def test_procedural_asset_modules_never_import_truth_or_case_material():
    """app.assets (spec provider/oracle/compiler/specs) and the world composer
    stay free of app.domain.truth — CaseTruth must never reach the provider
    request construction (import-graph enforcement).
    """
    procedural_modules = {
        "app.assets.spec_provider",
        "app.assets.oracle",
        "app.assets.compiler",
        "app.assets.specs",
        "app.assets.generated_cache",
        "app.assets.resolver",
        "app.world.composer",
        "app.world.extract",
        "app.world.requirements",
    }
    for module in procedural_modules:
        path = APP_DIR / (module.replace("app.", "", 1).replace(".", "/") + ".py")
        package = module.rsplit(".", 1)[0] if "." in module else None
        targets = _collect_import_targets(path.read_text(encoding="utf-8"), package=package)
        forbidden = sorted(_truth_targets(targets) | _validation_targets(targets))
        assert not forbidden, (
            f"{module} must not import app.domain.truth or app.validation "
            f"material (found {forbidden}) — the AssetSpecProvider surface is "
            "bounded to the ObjectRequirement request."
        )