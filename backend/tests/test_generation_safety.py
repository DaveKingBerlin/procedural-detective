"""Generated-content safety tests (Phase4 I / REQUIREMENTS 6.x / 26)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden_generation import (  # noqa: E402
    GOLDEN_FULL_DRAFT,
    GOLDEN_STAGE_PAYLOADS,
)

from app.generation.parser import parse_full_draft  # noqa: E402
from app.generation.pipeline import (  # noqa: E402
    AttemptRecord,
    apply_stage_output,
    normalize_prompt,
    validate_draft,
)
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.safety import (  # noqa: E402
    ANCHOR_ALLOWLIST,
    INTERACTION_ALLOWLIST,
    AssetRegistry,
    sanitize_for_repair,
    validate_asset_reference,
    validate_content_safety,
    validate_world_graph,
)
from app.generation.schemas import (  # noqa: E402
    PlacementSpec,
    WorldGraphSpec,
)
from app.generation.state_machine import ValidationOutcome  # noqa: E402

_GOLDEN_DRAFT = parse_full_draft(GOLDEN_FULL_DRAFT)

_GOLDEN_PROBE_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)


def _dump(doc) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)


def _probe_with_evidence_description(description: str) -> AttemptRecord:
    """Golden attempt whose first evidence description is ``description``."""
    evidence_doc = json.loads(GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE])
    evidence_doc["evidence"][0]["presentation"]["description"] = description
    docs = {
        GenerationStage.CASE_TRUTH: GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH],
        GenerationStage.PUBLIC_WORLD: GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD],
        GenerationStage.EVIDENCE: _dump(evidence_doc),
        GenerationStage.WORLD_GRAPH: GOLDEN_STAGE_PAYLOADS[GenerationStage.WORLD_GRAPH],
    }
    attempt = AttemptRecord(
        attempt_id="GA-def040", case_id="CASE-x", session_id="QUOTA-x"
    )
    attempt.locked, _ = normalize_prompt(_GOLDEN_PROBE_PROMPT, max_chars=4000)
    for stage in (
        GenerationStage.CASE_TRUTH,
        GenerationStage.PUBLIC_WORLD,
        GenerationStage.EVIDENCE,
        GenerationStage.WORLD_GRAPH,
    ):
        apply_stage_output(attempt, stage, docs[stage])
    attempt.last_validation = validate_draft(attempt)
    return attempt


# ---------------------------------------------------------------------------
# REQUIREMENTS 6.6 malicious-content payloads
# ---------------------------------------------------------------------------

_MALICIOUS_PAYLOADS = (
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "javascript:alert(1)",
    "../../../../windows/system32",
    "file:///etc/passwd",
    "https://attacker.example/evil.glb",
)


def test_requirements_6_6_payloads_rejected():
    for payload in _MALICIOUS_PAYLOADS:
        assert validate_content_safety(payload), payload


def test_requirements_6_6_payloads_rejected_inside_trees():
    # Nested dict/list trees are scanned all the way down.
    tree = {"evidence": [{"presentation": {"description": "<script>alert(1)</script>"}}]}
    assert validate_content_safety(tree)
    tree2 = {"nested": {"nested": [{"x": "https://attacker.example/evil.glb"}]}}
    assert validate_content_safety(tree2)


def test_mapping_keys_are_scanned():
    tree = {"<script>alert(1)</script>": "value"}
    assert validate_content_safety(tree)


# ---------------------------------------------------------------------------
# forbidden substrings
# ---------------------------------------------------------------------------

_FORBIDDEN_STRINGS = (
    "<script",
    "</script>",
    "eval(var x = 1)",
    "new Function('return 1')",
    "data:text/html;base64,PHN0",
    "file:///etc/passwd",
    "http://example.com",
    "https://example.com",
    "onclick=alert(1)",
    "onload=x",
    "onerror=x",
    "onmouseover=x",
    "<img src=x>",
    "<svg onload=x>",
    "<iframe src=evil>",
    "<object data=x>",
    "<embed src=x>",
    "<style>body{}</style>",
    "<link rel=stylesheet href=x>",
    "import(os)",
    "exec('x')",
    "shader_fragment",
    ".glb-url",
    ".gltf-url",
    "--input",
    "powershell -c x",
    "cmd.exe /c x",
    "os.system('x')",
    "subprocess.run('x')",
    "ftp://host/evil.glb",
    "ws://host/socket",
)


def test_forbidden_substrings_rejected():
    for payload in _FORBIDDEN_STRINGS:
        assert validate_content_safety(payload), payload


def test_backtick_code_fences_rejected():
    assert validate_content_safety("`code`")
    assert validate_content_safety("```python\nprint(1)\n```")
    assert validate_content_safety("use the `eval` feature")


def test_clean_text_accepted():
    assert validate_content_safety("The neighbour saw Sarah alive at 22:15.") == ()
    assert validate_content_safety(("clean", "another clean string")) == ()


# ---------------------------------------------------------------------------
# asset references
# ---------------------------------------------------------------------------


def test_asset_registry_has_required_entries():
    assert len(AssetRegistry.ASSET_IDS) >= 8
    for asset_id in (
        "PROP_KITCHEN_KNIFE_01",
        "PROP_HEAVY_VASE_01",
        "PROP_TABLE_01",
        "PROP_CHAIR_01",
        "PROP_LAPTOP_01",
        "PROP_BOTTLE_01",
        "CAMERA_HALL_01",
        "DOOR_APARTMENT_01",
    ):
        assert AssetRegistry.is_allowed(asset_id) is True


def test_validate_asset_reference_ok():
    assert validate_asset_reference("PROP_KITCHEN_KNIFE_01") == ()


def test_validate_asset_reference_unknown_rejected():
    issues = validate_asset_reference("PROP_EVIL_99")
    assert issues
    assert any("not in the AssetRegistry" in issue for issue in issues)


def test_validate_asset_reference_pattern_rejected():
    for bad in ("prop_kitchen_knife_01", "PROP-KNIFE-01", "1PROP_X", "PROP X", "_PROP_X", "x"):
        issues = validate_asset_reference(bad)
        assert issues
        assert any("pattern" in issue for issue in issues)


def test_validate_asset_reference_url_rejected():
    issues = validate_asset_reference("https://attacker.example/evil.glb")
    assert issues
    assert any("pattern" in issue for issue in issues)


def test_validate_asset_reference_non_string():
    assert validate_asset_reference(None)


# ---------------------------------------------------------------------------
# world graph allowlists
# ---------------------------------------------------------------------------


def test_interaction_allowlist_contains_required_mvp_values():
    for interaction in (
        "inspect",
        "collect",
        "open",
        "read",
        "activate",
        "talk",
        "view_record",
        "add_to_evidence_board",
    ):
        assert interaction in INTERACTION_ALLOWLIST


def test_anchor_allowlist_contains_required_anchors():
    for anchor in (
        "desk_main",
        "kitchen_counter",
        "dining_table",
        "bedside_table",
        "floor_body_position",
        "shelf_01",
        "hall_wall_01",
        "office_desk_01",
    ):
        assert anchor in ANCHOR_ALLOWLIST


def _golden_world_graph_validation():
    draft = _GOLDEN_DRAFT
    object_ids = {o.object_id for o in draft.objects}
    evidence_ids = {e.id for e in draft.evidence}
    return validate_world_graph(draft.world_graph, object_ids, evidence_ids)


def test_golden_world_graph_validates():
    assert _golden_world_graph_validation() == ()


def _wg(*placements, locations=None):
    return WorldGraphSpec(
        locations=locations or (),
        placements=tuple(placements),
    )


def test_world_graph_bad_interaction_rejected():
    placement = PlacementSpec(
        object_id="o1",
        asset_id="PROP_KITCHEN_KNIFE_01",
        location_id="l1",
        anchor="desk_main",
        interaction="fetch",
    )
    wg = _wg(placement, locations=())
    issues = validate_world_graph(wg, {"o1"}, {"ev1"})
    assert any("interaction" in issue for issue in issues)


def _legal_wg(*placements):
    """World graph with a declared location so only the tested rule can fire."""
    from app.generation.schemas import WorldGraphLocationSpec

    return WorldGraphSpec(
        placements=tuple(placements),
        locations=(
            WorldGraphLocationSpec(
                location_id="l1", template="kitchen_template", rooms=("kitchen",)
            ),
        ),
    )


def test_world_graph_empty_interaction_is_decorative_legal():
    """DEF-062: interaction "" = decorative / not interactable — a legal,
    issue-free placement when no evidence is linked."""
    placement = PlacementSpec(
        object_id="o1",
        asset_id="PROP_KITCHEN_KNIFE_01",
        location_id="l1",
        anchor="desk_main",
        interaction="",
    )
    wg = _legal_wg(placement)
    assert validate_world_graph(wg, {"o1"}, {"ev1"}) == ()


def test_world_graph_empty_interaction_with_evidence_rejected():
    """DEF-062: an evidence-linked placement requires a non-empty interaction
    (otherwise the linked evidence could never be reached through a player
    interaction)."""
    placement = PlacementSpec(
        object_id="o1",
        asset_id="PROP_KITCHEN_KNIFE_01",
        location_id="l1",
        anchor="desk_main",
        interaction="",
        evidence_id="ev1",
    )
    wg = _legal_wg(placement)
    issues = validate_world_graph(wg, {"o1"}, {"ev1"})
    assert any(
        "requires a non-empty interaction" in issue for issue in issues
    )


def test_world_graph_unknown_non_empty_interaction_still_rejected():
    """DEF-062: ONLY "" is additionally legal — unknown NON-empty values are
    still rejected against INTERACTION_ALLOWLIST."""
    placement = PlacementSpec(
        object_id="o1",
        asset_id="PROP_KITCHEN_KNIFE_01",
        location_id="l1",
        anchor="desk_main",
        interaction="teleport",
    )
    wg = _legal_wg(placement)
    issues = validate_world_graph(wg, {"o1"}, {"ev1"})
    assert any(
        "interaction 'teleport' is not in INTERACTION_ALLOWLIST"
        in issue
        for issue in issues
    )


def test_world_graph_bad_anchor_rejected():
    placement = PlacementSpec(
        object_id="o1",
        asset_id="PROP_KITCHEN_KNIFE_01",
        location_id="l1",
        anchor="https://evil.example/anchor",
        interaction="inspect",
    )
    wg = _wg(placement, locations=())
    issues = validate_world_graph(wg, {"o1"}, {"ev1"})
    assert any("anchor" in issue for issue in issues)


def test_world_graph_unknown_object_rejected():
    placement = PlacementSpec(
        object_id="unknown_object",
        asset_id="PROP_KITCHEN_KNIFE_01",
        location_id="l1",
        anchor="desk_main",
        interaction="inspect",
    )
    wg = _wg(placement, locations=())
    issues = validate_world_graph(wg, {"o1"}, {"ev1"})
    assert any("unknown objectId" in issue for issue in issues)


def test_world_graph_unknown_asset_rejected():
    placement = PlacementSpec(
        object_id="o1",
        asset_id="https://attacker.example/evil.glb",
        location_id="l1",
        anchor="desk_main",
        interaction="inspect",
    )
    wg = _wg(placement, locations=())
    issues = validate_world_graph(wg, {"o1"}, {"ev1"})
    assert any("pattern" in issue or "AssetRegistry" in issue for issue in issues)


def test_world_graph_unknown_location_rejected():
    placement = PlacementSpec(
        object_id="o1",
        asset_id="PROP_KITCHEN_KNIFE_01",
        location_id="not_declared",
        anchor="desk_main",
        interaction="inspect",
    )
    wg = _wg(placement, locations=())
    issues = validate_world_graph(wg, {"o1"}, {"ev1"})
    assert any("unknown locationId" in issue for issue in issues)


def test_world_graph_unknown_evidence_rejected():
    placement = PlacementSpec(
        object_id="o1",
        asset_id="PROP_KITCHEN_KNIFE_01",
        location_id="l1",
        anchor="desk_main",
        interaction="inspect",
        evidence_id="ev-unknown",
    )
    wg = _wg(placement, locations=())
    issues = validate_world_graph(wg, {"o1"}, {"ev1"})
    assert any("evidenceId" in issue for issue in issues)


def test_world_graph_path_traversal_anchor_rejected():
    placement = PlacementSpec(
        object_id="o1",
        asset_id="PROP_KITCHEN_KNIFE_01",
        location_id="l1",
        anchor="../../../../etc/passwd",
        interaction="inspect",
    )
    wg = _wg(placement, locations=())
    issues = validate_world_graph(wg, {"o1"}, {"ev1"})
    assert issues


def test_world_graph_script_interaction_rejected():
    placement = PlacementSpec(
        object_id="o1",
        asset_id="PROP_KITCHEN_KNIFE_01",
        location_id="l1",
        anchor="desk_main",
        interaction="<script>alert(1)</script>",
    )
    wg = _wg(placement, locations=())
    issues = validate_world_graph(wg, {"o1"}, {"ev1"})
    assert issues


# ---------------------------------------------------------------------------
# sanitize_for_repair
# ---------------------------------------------------------------------------


def test_sanitize_for_repair_contains_no_hidden_internals():
    sanitized = sanitize_for_repair(
        _GOLDEN_DRAFT,
        diagnostics=("sanity issue: evidence id ev1 unresolved",),
    )
    # structured material is present (crime facts are part of the draft)
    assert "sarah_miller" in sanitized
    assert "2026-09-11T22:17:00+02:00" in sanitized
    # sanitized issues are included verbatim
    assert "sanity issue: evidence id ev1 unresolved" in sanitized
    # hidden internals / solver internals / paths NEVER appear
    for forbidden in (
        "SOLVER",
        "Traceback",
        "CaseTruth",
        "backend/app",
        "C:\\\\",
        "generation/parser",
        "app.domain.truth",
    ):
        assert forbidden not in sanitized


def test_sanitize_for_repair_is_stable():
    first = sanitize_for_repair(_GOLDEN_DRAFT, diagnostics=("a", "b"))
    second = sanitize_for_repair(_GOLDEN_DRAFT, diagnostics=("a", "b"))
    assert first == second


def test_sanitize_for_repair_accepts_mapping_or_string():
    as_mapping = sanitize_for_repair({"crime": {"victimId": "sarah_miller"}})
    assert "sarah_miller" in as_mapping
    as_string = sanitize_for_repair("plain draft text")
    assert as_string == "plain draft text"


# ---------------------------------------------------------------------------
# DEF-040 / ADV-127 — content-scanner canonicalization
# ---------------------------------------------------------------------------
#
# Encoded/mangled payloads that previously passed the RAW substring scan and
# reached pipeline outcome=VALID. The canonicalization layer normalizes NFKC /
# lowercase / HTML entities / literal backslash escapes / control & zero-width
# chars, then runs anchored whitespace-tolerant tokens over the normalized form.

_EVASION_PAYLOADS = (
    "&lt;script&gt;alert(1)&lt;/script&gt;",
    "&#x3C;script&gt;alert(1)&#x3C;/script&gt;",
    "java\nscript:alert(1)",
    "<scr\x00ipt>alert(1)</scr\x00ipt>",
    "%2e%2e%2f%2e%2e%2fetc/passwd",
    "\\u003cscript>alert(1)\\u003c/script>",
    "java\u200bscript:alert(1)",
    "\uff1cscript\uff1ealert(1)\uff1c/script\uff1e",
    'globalThis.constructor.constructor("alert(1)")()',
    "eval (alert(1))",
)


def test_def040_all_ten_evasion_payloads_rejected_directly():
    for payload in _EVASION_PAYLOADS:
        issues = validate_content_safety(payload)
        assert issues, f"evasion payload not rejected: {payload!r}"


def test_def040_evasion_payloads_break_validate_draft_outcome():
    # Clean golden probe first: the harness itself must classify VALID with
    # zero safety issues (otherwise this regression would be vacuous).
    clean = _probe_with_evidence_description("The neighbour saw Sarah alive at 22:15.")
    assert clean.last_validation.safety_issues == ()
    assert clean.last_validation.outcome is ValidationOutcome.VALID

    for payload in _EVASION_PAYLOADS:
        attempt = _probe_with_evidence_description(payload)
        assert attempt.last_validation.safety_issues, (
            f"end-to-end safety issues empty for {payload!r}"
        )
        assert attempt.last_validation.outcome is not ValidationOutcome.VALID


def test_def040_existing_controls_still_rejected():
    for payload in ("<script>alert(1)</script>", "<ScRiPt>alert(1)</ScRiPt>"):
        assert validate_content_safety(payload), payload


def test_def040_benign_prose_not_rejected():
    # Guard: bare words "script" / ".constructor" in prose are NOT tokens —
    # only executable forms (a "<" preceding script, a constructor
    # chain/invocation, ...) are rejected.
    benign = (
        "The victim left a script for the accountant.",
        "schema.constructor details",
        "The suspect rehearsal notes mention a construction error.",
        "The player may read the script of the interview.",
    )
    for prose in benign:
        assert validate_content_safety(prose) == (), prose