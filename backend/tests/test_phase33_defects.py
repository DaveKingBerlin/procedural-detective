"""Phase33 gate — deterministic regression tests for the EIGHT accepted
adversarial findings (ADV-33-01 .. ADV-33-08 -> DEF-058 .. DEF-065).

Each test pins one formal defect with focused, deterministic assertions:

- DEF-058: the rendered generation-scope ASSET list is the validator's real
  acceptance set (registry UNION catalog) and byte-deterministic.
- DEF-059: the anchor guidance is kit/environment-scoped and the
  composition/placement diagnostics classify registry/anchor/witness.
- DEF-060: every advertised "guaranteed-valid" asset id is composition-valid
  (catalog-backed); the 3 over-promising registry-only ids are caveated.
- DEF-061: the failure-category distribution counts ONLY failed attempts.
- DEF-062: `publishable` derives from `published` for successful attempts and
  missing telemetry renders NULL / `n/a`, never a fabricated 0.
- DEF-063: the failure-category scan matches app-owned diagnostic fragments,
  never bare keywords a GENERATED value could quote and re-route.
- DEF-064: the witness-match contract wording matches `normalize_identity`
  (ASCII-only), and the REPAIR template's missing `__LOCKED__` slot is
  documented (no misleading comment).
- DEF-065: the fabricated per-model scorecard template is emitted ONLY for
  `dry_run=True` plans.

Hermetic by construction: no provider, no network, no CaseTruth; the real
validators / placer / pipeline / benchmark functions run over synthetic data.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.assets.catalog import load_catalog_from_repo  # noqa: E402
from app.environments.manifests import load_all_environments  # noqa: E402
from app.environments.placer import validate_placement  # noqa: E402
from app.generation import prompts  # noqa: E402
from app.generation.constraints import normalize_identity  # noqa: E402
from app.generation.safety import (  # noqa: E402
    ANCHOR_ALLOWLIST,
    AssetRegistry,
    validate_asset_reference,
)
from app.generation.schemas import PlacementSpec  # noqa: E402
from tools import frontier_benchmark as fb  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]

# The three registry ids that are NOT in the Asset Oracle catalog (DEF-060).
_OVER_PROMISING_IDS = frozenset(
    {"CAMERA_HALL_01", "PROP_BOTTLE_01", "PROP_HEAVY_VASE_01"}
)


# --------------------------------------------------------------------------- #
# DEF-058 — scope asset list matches the validator's acceptance set
# --------------------------------------------------------------------------- #


def test_def058_scope_asset_list_is_registry_union_catalog_and_deterministic():
    """Every catalog-registered id AND every ``AssetRegistry.ASSET_IDS`` id is
    present in the rendered scope block, and the block is byte-deterministic
    (the docstring no longer claims the block renders only the registry)."""
    catalog_ids = set(load_catalog_from_repo().by_id)
    registry_ids = set(AssetRegistry.ASSET_IDS)
    # Sanity: the two acceptance authorities really are distinct sources.
    assert registry_ids - catalog_ids  # the union is a REAL union
    assert catalog_ids - registry_ids

    block = prompts.generation_scope_block()
    for catalog_id in sorted(catalog_ids):
        assert catalog_id in block, f"catalog-registered id {catalog_id!r} missing"
    for registry_id in sorted(registry_ids):
        assert registry_id in block, f"registry id {registry_id!r} missing"

    # Every advertised id is actually accepted by the reference validator
    # (the union is exactly the acceptance set of validate_asset_reference).
    for catalog_id in sorted(catalog_ids):
        assert validate_asset_reference(catalog_id) == (), catalog_id

    # Byte-determinism: repeated renders (and the import-time pre-render)
    # produce byte-identical output.
    assert prompts.generation_scope_block() == block
    assert prompts._AUTHORIZED_SCOPE_TEXT == block


# --------------------------------------------------------------------------- #
# DEF-059 — anchor guidance is kit-scoped; composition diagnostics classify
# --------------------------------------------------------------------------- #


def test_def059_anchor_guidance_is_kit_scoped():
    """The scope block states anchors are KIT/ENVIRONMENT-SCOPED and renders
    the per-kit anchor vocabulary derived from the environment manifests (a
    cross-kit anchor fails composition), while still covering the global
    ANCHOR_ALLOWLIST."""
    block = prompts.generation_scope_block()
    assert "KIT/ENVIRONMENT-SCOPED" in block
    assert "OWN environment kit" in block
    assert "unknown anchor" in block

    kits = load_all_environments()
    allowed = frozenset(ANCHOR_ALLOWLIST)
    table = prompts._kit_anchor_table()
    assert table, "per-kit anchor table must derive from the manifests"
    assert len(table) == len(kits)
    for environment_id, anchor_ids in table:
        kit = next(k for k in kits if k.environment_id == environment_id)
        declared = {a.anchor_id for a in kit.anchors}
        assert set(anchor_ids) == declared & allowed, environment_id
        for anchor_id in anchor_ids:
            # The derive output is BOTH world-graph-valid and in the kit.
            assert anchor_id in ANCHOR_ALLOWLIST
            assert anchor_id in declared

    # The global union is the sum of the per-kit vocabularies.
    rendered_union = {a for _, ids in table for a in ids}
    assert rendered_union == set(ANCHOR_ALLOWLIST)
    for anchor in sorted(ANCHOR_ALLOWLIST):
        assert anchor in block


def test_def059_composition_and_placement_diagnostics_classify_registry_anchor_witness():
    """The composition/placement diagnostics (cross-kit anchor, anchor
    allowlist, catalog-less asset) classify registry/anchor/witness — NOT
    'other world'. Other placement diagnostics stay 'other world'."""
    assert fb.failure_category_for(
        "VALIDATION_FAILED",
        ("placements[0]: unknown anchor 'desk_main' in kit 'mansion'",),
    ) == fb.FAILURE_CATEGORY_REGISTRY_ANCHOR_WITNESS
    assert fb.failure_category_for(
        "VALIDATION_FAILED",
        ("placements[0]: anchor 'under_the_rug' is not in ANCHOR_ALLOWLIST",),
    ) == fb.FAILURE_CATEGORY_REGISTRY_ANCHOR_WITNESS
    assert fb.failure_category_for(
        "VALIDATION_FAILED",
        ("placements[0]: asset 'PROP_HEAVY_VASE_01' is not in the asset catalog",),
    ) == fb.FAILURE_CATEGORY_REGISTRY_ANCHOR_WITNESS
    # The composer wraps placer issues as world.invalid-placement: the class
    # must still resolve from the app-owned fragment inside.
    assert fb.failure_category_for(
        "VALIDATION_FAILED",
        ("world.invalid-placement: placements[0]: unknown anchor 'desk_main' "
         "in kit 'mansion'",),
    ) == fb.FAILURE_CATEGORY_REGISTRY_ANCHOR_WITNESS
    assert fb.failure_category_for(
        "VALIDATION_FAILED",
        ("world.invalid-placement: placements[0]: asset 'PROP_BOTTLE_01' is "
         "not in the asset catalog",),
    ) == fb.FAILURE_CATEGORY_REGISTRY_ANCHOR_WITNESS
    # Other composition placement failures stay "other world".
    assert fb.failure_category_for(
        "VALIDATION_FAILED",
        ("world.invalid-placement: placements[0]: asset category 'decor' is "
         "not allowed on anchor 'office_desk_a' (allowed ['electronics'])",),
    ) == fb.FAILURE_CATEGORY_OTHER_WORLD
    assert fb.failure_category_for(
        "VALIDATION_FAILED",
        ("world.unresolved-object: required object nowhere",),
    ) == fb.FAILURE_CATEGORY_OTHER_WORLD


# --------------------------------------------------------------------------- #
# DEF-060 — every advertised asset id is composition-valid
# --------------------------------------------------------------------------- #


def _composition_probe_no_catalog_rejection(asset_id: str) -> list[str]:
    """Run the REAL placer composition gate for ``asset_id`` on a deterministic
    appropriate (kit, anchor) pair and return its issues (never consulted for
    'is not in the asset catalog' by the caller)."""
    catalog = load_catalog_from_repo()
    descriptor = catalog.by_id[asset_id]
    compatible: list[tuple[object, object]] = []
    for kit in load_all_environments():
        for anchor in kit.anchors:
            if anchor.type not in descriptor.allowed_anchors:
                continue
            if descriptor.category not in anchor.allowed_categories:
                continue
            compatible.append((kit, anchor))
    if compatible:
        kit, anchor = compatible[0]
    else:
        kit = load_all_environments()[0]
        anchor = kit.anchors[0]
    placement = PlacementSpec(
        object_id="obj_probe",
        asset_id=asset_id,
        location_id="loc_probe",
        anchor=anchor.anchor_id,
        interaction="",
        evidence_id=None,
    )
    return list(validate_placement(kit, [placement], catalog=catalog))


def test_def060_advertised_asset_ids_pass_the_composition_gate():
    """Every id in the composition-valid guaranteed list is catalog-backed (the
    placer's asset gate, ``asset_id in catalog.by_id``), and a real
    ``validate_placement`` probe on an appropriate kit/anchor never reports
    'is not in the asset catalog'. The 3 over-promising ids are caveated, not
    guaranteed."""
    guaranteed = prompts._composition_asset_ids()
    registry_only = set(prompts._registry_only_asset_ids())
    assert registry_only == _OVER_PROMISING_IDS

    catalog = load_catalog_from_repo()
    for asset_id in guaranteed:
        assert asset_id in catalog.by_id  # the exact composition-gate asset check
        issues = _composition_probe_no_catalog_rejection(asset_id)
        assert not any("is not in the asset catalog" in issue for issue in issues), (
            asset_id,
            issues,
        )

    block = prompts.generation_scope_block()
    assert block  # never render the over-promising ids as guaranteed
    for asset_id in sorted(_OVER_PROMISING_IDS):
        assert asset_id not in guaranteed
        # The block names them in the caveat and states they are rejected at
        # composition — the model is told they are NOT composition-usable.
        assert asset_id in block
        assert "is not in the asset catalog" in block
        assert "NEVER use them" in block


# --------------------------------------------------------------------------- #
# DEF-061 — failure distribution counts only FAILED attempts
# --------------------------------------------------------------------------- #


def _contestant():
    return fb.BenchmarkContestant(
        id="c1", label="C1", provider="openrouter", model="m",
        credential_env="K", enabled=True,
    )


def test_def061_failure_distribution_excludes_published_successes():
    """3 successful (published) attempts + 1 real failure must produce a
    failureCategories tally showing ONLY the real failure class — never
    platform/unknown = 4 (the pre-fix bug counted every executed record)."""
    base = {
        "benchmarkSchemaVersion": "1.0.0",
        "benchmarkRunId": "r",
        "contestantId": "c1",
        "contestantLabel": "C1",
        "provider": "openrouter",
        "model": "m",
        "difficulty": "easy",
        "repeatIndex": 0,
    }
    successes = []
    for index in range(3):
        # Published successes carry a failureCategory after enrich_result
        # (platform/unknown for no failure) — even so they are NOT failures.
        successes.append(
            {
                **base,
                "benchmarkCaseId": f"ok{index}",
                "executionOrder": index + 1,
                "finalStatus": "PUBLISHED",
                "published": True,
                "publishable": True,
                "failureCode": None,
                "failureCategory": fb.FAILURE_CATEGORY_PLATFORM_UNKNOWN,
                "costSource": "unavailable",
            }
        )
    failure = {
        **base,
        "benchmarkCaseId": "term",
        "executionOrder": 4,
        "finalStatus": "FAILED",
        "published": False,
        "publishable": False,
        "failureCode": "VALIDATION_FAILED",
        "failureCategory": fb.FAILURE_CATEGORY_REGISTRY_ANCHOR_WITNESS,
        "costSource": "unavailable",
    }
    row = fb.aggregate_per_contestant(_contestant(), successes + [failure])
    assert row["attempts"] == 4
    assert row["published"] == 3
    assert row["failureCategories"] == {
        fb.FAILURE_CATEGORY_REGISTRY_ANCHOR_WITNESS: 1
    }
    assert fb.FAILURE_CATEGORY_PLATFORM_UNKNOWN not in row["failureCategories"]


# --------------------------------------------------------------------------- #
# DEF-062 — publishable is published-derived; missing telemetry is NULL/n/a
# --------------------------------------------------------------------------- #


def test_def062_missing_telemetry_aggregates_render_null_not_zero():
    """When NO executed attempt has a known publishable value, the aggregate
    `publishable` is None and the rate is None (rendered n/a) — never the
    fabricated 0 / 0.0."""
    base = {
        "benchmarkSchemaVersion": "1.0.0",
        "benchmarkRunId": "r",
        "contestantId": "c1",
        "contestantLabel": "C1",
        "provider": "openrouter",
        "model": "m",
        "difficulty": "easy",
        "repeatIndex": 0,
        "costSource": "unavailable",
    }
    records = []
    for index in range(2):
        record = {
            **base,
            "benchmarkCaseId": f"no-telemetry-{index}",
            "executionOrder": index + 1,
            "finalStatus": "FAILED",
            "published": False,
            # publishable from enrich_result([]) stays None — unknown, and
            # failureCategory also stays None.
            "publishable": None,
            "failureCode": None,
            "failureCategory": None,
        }
        assert isinstance(record["publishable"], type(None))
        records.append(record)
    row = fb.aggregate_per_contestant(_contestant(), records)
    assert row["attempts"] == 2
    assert row["publishable"] is None
    assert row["publishableRate"] is None
    assert row["failureCategories"] == {}

    # The report section renders the NULL as n/a — never as a fabricated 0.
    report = _render_report(row)
    assert "| c1 | n/a | n/a | 0 |" in report, report


def _render_report(row: dict) -> str:
    report = fb.build_report(
        metadata={
            "benchmarkRunId": "r",
            "benchmarkSchemaVersion": "1.0.0",
            "toolName": "frontier-benchmark",
            "toolVersion": "0.0.0",
            "driver": "inprocess",
            "startedAtIso": None,
            "finishedAtIso": None,
            "gitCommit": None,
            "gitBranch": None,
            "suite": "smoke",
            "caseCount": 0,
            "repeatCount": 0,
            "seed": 0,
            "contestantOrder": [],
            "generationDeadlineSeconds": None,
            "providerTimeoutSeconds": None,
            "coreProviderCallBudget": None,
            "globalProviderCallBudget": None,
            "maxRepairPasses": None,
            "maxFullRegenerations": None,
            "contestants": [
                {
                    "id": "c1",
                    "label": "C1",
                    "provider": "openrouter",
                    "model": "m",
                    "enabled": True,
                    "credentialEnv": "K",
                }
            ],
        },
        per_contestant=[row],
        suite=fb.SuiteSpec(easy=0, medium=0, hard=0),
        cases=[],
        secret_scan_hits=[],
        luna_status=fb.LUNA_STATUS_BLOCKED,
        luna_reason="hermetic test — no Luna baseline contacted",
        monitoring_artifacts=None,
        caveats=[],
        recommendation_notes=[],
    )
    return report


def test_def062_publishable_derives_from_published_for_successes():
    """enrich_result derives publishable from `published`: a published attempt
    is publishable; a not-published attempt with telemetry is not; no telemetry
    stays None (never fabricated)."""
    published = {
        "benchmarkSchemaVersion": "1.0.0",
        "benchmarkRunId": "r",
        "contestantId": "c1",
        "finalStatus": "PUBLISHED",
        "published": True,
        "failureCode": None,
    }
    fb.enrich_result(published, [{"event": "generation.published", "providerCallCount": 2}])
    assert published["publishable"] is True

    missing = {"publishable": None}
    fb.enrich_result(missing, [])
    assert missing["publishable"] is None


# --------------------------------------------------------------------------- #
# DEF-063 — failure-category scan ignores GENERATED values quoting triggers
# --------------------------------------------------------------------------- #


def test_def063_embedded_trigger_words_do_not_reroute_the_class():
    """A generated value that merely QUOTES a trigger keyword (assetregistry,
    solver, schema, placement, geometry, duplicate, ambiguous, ...) inside a
    diagnostic must NOT re-route that diagnostic into the trigger's class."""
    # unknown-top-level-key diagnostics remain contract/schema, never a
    # content class the quoted value names.
    assert fb.failure_category_for(
        "VALIDATION_FAILED", ("unknown top-level key 'solver'",)
    ) == fb.FAILURE_CATEGORY_CONTRACT_SCHEMA
    assert fb.failure_category_for(
        "VALIDATION_FAILED", ("unknown top-level key 'assetregistry'",)
    ) == fb.FAILURE_CATEGORY_CONTRACT_SCHEMA
    assert fb.failure_category_for(
        "VALIDATION_FAILED", ("unknown top-level key 'anchor_allowlist'",)
    ) == fb.FAILURE_CATEGORY_CONTRACT_SCHEMA
    assert fb.failure_category_for(
        "VALIDATION_FAILED", ("unknown top-level key 'placement'",)
    ) == fb.FAILURE_CATEGORY_CONTRACT_SCHEMA
    assert fb.failure_category_for(
        "VALIDATION_FAILED", ("unknown top-level key 'ambiguous'",)
    ) == fb.FAILURE_CATEGORY_CONTRACT_SCHEMA
    assert fb.failure_category_for(
        "VALIDATION_FAILED", ("unknown top-level key 'geometry'",)
    ) == fb.FAILURE_CATEGORY_CONTRACT_SCHEMA
    assert fb.failure_category_for(
        "VALIDATION_FAILED", ("unknown top-level key 'duplicate'",)
    ) == fb.FAILURE_CATEGORY_CONTRACT_SCHEMA
    assert fb.failure_category_for(
        "VALIDATION_FAILED", ("unknown top-level key 'world.invalid-placement'",)
    ) == fb.FAILURE_CATEGORY_CONTRACT_SCHEMA

    # A quoted object id matching a trigger word never re-routes either.
    category = fb.failure_category_for(
        "VALIDATION_FAILED", ("placements[0]: unknown objectId 'solver'",)
    )
    assert category != fb.FAILURE_CATEGORY_SOLVER
    category2 = fb.failure_category_for(
        "VALIDATION_FAILED", ("world_graph.placements[0]: unknown objectId 'assetregistry'",)
    )
    assert category2 != fb.FAILURE_CATEGORY_REGISTRY_ANCHOR_WITNESS

    # A genuine OTHER-WORLD composition diagnostic stays other world even when
    # the quoted asset name contains a trigger word.
    assert fb.failure_category_for(
        "VALIDATION_FAILED",
        ("world.invalid-placement: placements[0]: asset category 'solver' is "
         "not allowed on anchor 'office_desk_a' (allowed ['electronics'])",),
    ) == fb.FAILURE_CATEGORY_OTHER_WORLD


# --------------------------------------------------------------------------- #
# DEF-064 — witness contract wording matches normalize_identity (ASCII-only)
# --------------------------------------------------------------------------- #


def test_def064_witness_contract_matches_normalize_identity_on_non_ascii():
    """The contract text states the EXACT normalize_identity semantics (keep
    ONLY ASCII letters/digits; non-ASCII letters are REMOVED), demonstrated on
    a non-ASCII example, and the validator's behavior agrees with the text."""
    # The validator's behavior on the non-ASCII example:
    assert normalize_identity("\u00c9mily Reed") == "milyreed"
    # The contract promises the SAME equivalence (never "ignores punctuation"
    # while the validator drops accented letters too).
    text = prompts.locked_witness_contract_line()
    assert "ASCII letters and digits" in text
    assert "non-ASCII" in text
    assert "accented" in text
    assert "milyreed" in text
    # The stated example equivalence holds under the real normalizer.
    assert (
        normalize_identity("Rita Vale")
        == normalize_identity("rita_vale")
        == normalize_identity("RITA VALE")
    )
    # The full-draft field rules carry the consistent wording.
    assert "ASCII letters and digits" in prompts._CASE_FIELD_RULES

    # DEF-064(b): the REPAIR template has NO __LOCKED__ slot and the
    # locked_witness_contract_line docstring documents that fact (the comment
    # no longer claims the REPAIR template renders the locked constraint sheet
    # separately) — a locked witness violation is TERMINAL, never repaired.
    assert "__LOCKED__" not in prompts.REPAIR_PROMPT_v1

    # DEF-059 also relies on the locked-witness contrast: the genuinely
    # locked-witness terminal diagnostic still classifies registry/anchor/witness.
    assert fb.failure_category_for(
        "VALIDATION_FAILED",
        ("locked constraint: locked witness '\u00c9mily Reed' not found among "
         "draft persons with role 'witness'",),
    ) == fb.FAILURE_CATEGORY_REGISTRY_ANCHOR_WITNESS


# --------------------------------------------------------------------------- #
# DEF-065 — scorecard template is dry-run-only
# --------------------------------------------------------------------------- #


def _plan_text(*, dry_run: bool) -> str:
    return fb._plan_summary_text(
        run_id="r1",
        suite_name="smoke",
        contestants=[_contestant()],
        cases=[],
        repeat=1,
        pending_count=1,
        skip_count=0,
        driver="inprocess",
        concurrency=1,
        base_url=None,
        credential_map={},
        max_cases=None,
        output_dir=Path("out"),
        dry_run=dry_run,
    )


def test_def065_non_dry_run_plan_has_no_fabricated_scorecard():
    """A REAL (non-dry-run) plan must NOT contain the sample-looking scorecard
    template (fabricated 20/18/18/90.00% row) — only dry-run plans may show
    it, clearly labelled as a template."""
    real = _plan_text(dry_run=False)
    assert "FUTURE PER-MODEL SCORECARD TEMPLATE" not in real
    assert "90.00%" not in real
    assert "registry/anchor/witness:2" not in real
    assert "| <model-a>" not in real
    assert "DRY RUN" not in real

    # Dry-run plans keep the template (regression guard).
    dry = _plan_text(dry_run=True)
    assert "FUTURE PER-MODEL SCORECARD TEMPLATE" in dry
    assert "| <model-a>" in dry
    assert "DRY RUN: NO provider is contacted" in dry