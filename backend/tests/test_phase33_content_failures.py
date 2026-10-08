"""Phase33 RAD-1 — deterministic OFFLINE red regressions for the three
generation content failure classes, plus the "missing trusted context" evidence
for each (Phase33.md §RAD-1 table).

One file, three independent classes:

- **Class A — unknown/mismatched asset identifier against the trusted
  registry.** The draft's world-graph placement (and/or object) references an
  id that is NOT in ``AssetRegistry.ASSET_IDS`` / the Asset Oracle catalog.
  Phase33.md RAD-1 classification row: **Missing trusted context** — the
  full-draft REPAIR contract (``prompts._full_draft_contract``) says
  ``"assetId": "asset id or object id"`` and the model never receives the
  allowed asset-id vocabulary anywhere.

- **Class B — disallowed anchor.** A world-graph placement uses an anchor that
  is NOT in ``ANCHOR_ALLOWLIST``. Phase33.md RAD-1 classification row:
  **Missing trusted context** — ``_WORLD_FIELD_RULES`` teaches relation kinds
  and environmentHint tokens but never the anchor-id vocabulary.

- **Class C — locked-witness mismatch.** The draft's persons contain no
  role-normalized "witness" whose person_id/name identifies the locked
  ``Witness:`` constraint (DEF-054). Phase33.md RAD-1 classification row:
  **Ambiguous guidance** — the persons rule says "include the witness" but
  never ties the generated witness to the locked Witness value; the lock is
  enforced as TERMINAL_FAILURE and is unfixable inside the attempt.

Design rules (deterministic-testing policy):
- Synthetic parsed drafts only — never real provider bodies, never CaseTruth.
- The strict parser, the real validators and the real pipeline are exercised;
  only the fixtures are synthetic.
- For each class the test asserts (1) the EXACT rejection semantics and (2)
  the model-facing context that is currently missing (the RED part): the
  REPAIR prompt context must eventually carry the authoritative registry /
  anchor / witness contract. On the current (unfixed) code part (2) FAILS;
  after the RAD-2 fix it passes while part (1) stays byte-identical.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation import pipeline, prompts  # noqa: E402
from app.generation.constraints import LockedConstraints  # noqa: E402
from app.generation.parser import parse_full_draft  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.safety import (  # noqa: E402
    ANCHOR_ALLOWLIST,
    AssetRegistry,
    validate_asset_reference,
    validate_world_graph,
)
from app.generation.state_machine import ValidationOutcome  # noqa: E402

# ---------------------------------------------------------------------------
# deterministic synthetic fixtures (sanitized, offline)
# ---------------------------------------------------------------------------

_CANONICAL_TIME = "2026-09-11T22:17:00+02:00"


def _synthetic_draft_doc(*, asset_id: str, anchor: str, interaction: str = "inspect") -> dict:
    """A small, structurally-valid FULL-DRAFT document (deterministic).

    Every id resolves inside the document; the only variables are the
    placement/object ``asset_id`` and the placement ``anchor`` so a single
    fixture parameterizes both Class A and Class B. ``evidence`` is empty.
    """
    return {
        "crime": {
            "type": "murder",
            "victimId": "victim_sarah",
            "murdererId": "suspect_thomas",
            "motiveId": "motive_greed",
            "weaponId": "obj_knife",
            "locationId": "loc_office",
            "crimeTime": {
                "canonical": _CANONICAL_TIME,
                "accusationToleranceSeconds": 120,
            },
        },
        "persons": [
            {
                "personId": "victim_sarah",
                "name": "Sarah Miller",
                "role": "victim",
                "affordances": ["VISIBLE_CHARACTER", "INSPECTABLE"],
            },
            {
                "personId": "suspect_thomas",
                "name": "Thomas Reed",
                "role": "suspect",
                "affordances": ["SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER", "INSPECTABLE"],
            },
            {
                "personId": "suspect_gina",
                "name": "Gina Costa",
                "role": "suspect",
                "affordances": ["SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER", "INSPECTABLE"],
            },
        ],
        "motives": [
            {"motiveId": "motive_greed", "label": "Greed", "affordances": ["MOTIVE_CANDIDATE"]},
            {"motiveId": "motive_revenge", "label": "Revenge", "affordances": ["MOTIVE_CANDIDATE"]},
        ],
        "objects": [
            {
                "objectId": "obj_knife",
                "assetId": asset_id,
                "affordances": ["POTENTIAL_WEAPON", "INSPECTABLE"],
            }
        ],
        "locations": [
            {"locationId": "loc_office", "name": "Office"},
            {"locationId": "loc_lobby", "name": "Lobby"},
        ],
        "travelRules": [
            {
                "fromLocationId": "loc_lobby",
                "toLocationId": "loc_office",
                "travelTimeSeconds": 1800,
            }
        ],
        "scene": {"locationId": "loc_office", "name": "Office"},
        "evidence": [],
        "worldGraph": {
            "locations": [
                {"locationId": "loc_office", "template": "kitchen_template", "rooms": ["kitchen"]}
            ],
            "placements": [
                {
                    "objectId": "obj_knife",
                    "assetId": asset_id,
                    "locationId": "loc_office",
                    "anchor": anchor,
                    "interaction": interaction,
                    "evidenceId": None,
                }
            ],
        },
    }


def _draft_json(asset_id: str = "PROP_KITCHEN_KNIFE_01", anchor: str = "desk_main") -> str:
    return json.dumps(_synthetic_draft_doc(asset_id=asset_id, anchor=anchor), sort_keys=True)


def _attempt(draft: object, locked: LockedConstraints | None = None) -> pipeline.AttemptRecord:
    """A pipeline AttemptRecord whose draft is the parsed synthetic document."""
    attempt = pipeline.AttemptRecord(
        attempt_id="GA-phase33-synthetic",
        case_id="CASE-phase33",
        session_id="QUOTA-phase33",
    )
    if locked is not None:
        attempt.locked = locked
    attempt.draft = draft if not isinstance(draft, str) else parse_full_draft(draft)
    return attempt


def _repair_request(attempt: pipeline.AttemptRecord):
    """The model-facing REPAIR ``GenerateRequest`` for a failing draft.

    Mirrors exactly what the controller builds before a REPAIR provider call
    (``pipeline.build_request`` with the full report diagnostics). The
    ``prompt_context`` is what a provider actually receives for the repair.
    """
    report = pipeline.validate_draft(attempt)
    request = pipeline.build_request(
        attempt, GenerationStage.REPAIR, diagnostics=report.repair_diagnostics
    )
    return request, report


# ---------------------------------------------------------------------------
# CLASS A — unknown asset identifier against the trusted registry
# (Phase33.md RAD-1 row: Missing trusted context)
# ---------------------------------------------------------------------------


def test_phase33_a_unknown_asset_id_rejected():
    """Rejection semantics (must stay green, byte-identical): an asset id that
    is in NEITHER ``AssetRegistry.ASSET_IDS`` NOR the catalog is rejected with
    the exact 'not in the AssetRegistry' diagnostic and the draft classifies
    RECOVERABLE_REPAIR."""
    unknown = "PROP_UNREGISTERED_99"
    assert unknown not in AssetRegistry.ASSET_IDS

    # Direct reference check.
    issues = validate_asset_reference(unknown)
    assert issues
    assert f"asset id {unknown!r} is not in the AssetRegistry" in issues

    # World-graph placement check.
    draft = parse_full_draft(_draft_json(asset_id=unknown))
    wg_issues = validate_world_graph(
        draft.world_graph,
        {o.object_id for o in draft.objects},
        {e.id for e in draft.evidence},
    )
    assert any(
        f"asset id {unknown!r} is not in the AssetRegistry" in i for i in wg_issues
    )

    # Full deterministic pipeline classification.
    attempt = _attempt(draft)
    report = pipeline.validate_draft(attempt)
    assert any(
        f"asset id {unknown!r} is not in the AssetRegistry" in i
        for i in report.safety_issues
    )
    assert report.repair_diagnostics
    assert report.outcome is ValidationOutcome.RECOVERABLE_REPAIR


def test_phase33_a_repair_context_missing_authoritative_asset_vocabulary():
    """MODEL-FACING GAP (RED on the current code): the REPAIR prompt context
    does NOT carry the authoritative asset-id vocabulary, so the model must
    invent (or guess) asset ids and can never reliably satisfy the registry.

    Phase33.md RAD-1 classification: **Missing trusted context**. Once the
    RAD-2 scope block supplies ``AssetRegistry.ASSET_IDS`` this test turns
    green; the rejection test above stays unchanged.
    """
    attempt = _attempt(parse_full_draft(_draft_json(asset_id="PROP_UNREGISTERED_99")))
    request, _report = _repair_request(attempt)

    context = request.prompt_context
    # The failing draft's own (invalid) id IS present — but the authoritative
    # registry vocabulary must be supplied by the prompt, not the draft.
    assert "PROP_UNREGISTERED_99" in context
    for asset_id in sorted(AssetRegistry.ASSET_IDS):
        assert asset_id in context, (
            f"REPAIR prompt context lacks the authoritative asset id {asset_id!r} — "
            "the model is forced to guess registry ids (Phase33 RAD-1 "
            "'Missing trusted context')"
        )

    # The rendered Ollama repair template must teach the same vocabulary.
    rendered = prompts.build_repair_prompt(request.prompt_context, request.diagnostics)
    for asset_id in sorted(AssetRegistry.ASSET_IDS):
        assert asset_id in rendered, (
            f"REPAIR template lacks asset id {asset_id!r} in its scope block"
        )


# ---------------------------------------------------------------------------
# CLASS B — disallowed anchor against ANCHOR_ALLOWLIST
# (Phase33.md RAD-1 row: Missing trusted context)
# ---------------------------------------------------------------------------


def test_phase33_b_disallowed_anchor_rejected():
    """Rejection semantics (must stay green): a placement anchor that is not in
    ANCHOR_ALLOWLIST is rejected with the exact diagnostic and the draft
    classifies RECOVERABLE_REPAIR."""
    anchor = "under_the_rug"
    assert anchor not in ANCHOR_ALLOWLIST

    draft = parse_full_draft(_draft_json(anchor=anchor))
    issues = validate_world_graph(
        draft.world_graph,
        {o.object_id for o in draft.objects},
        {e.id for e in draft.evidence},
    )
    assert f"placements[0]: anchor {anchor!r} is not in ANCHOR_ALLOWLIST" in issues

    attempt = _attempt(draft)
    report = pipeline.validate_draft(attempt)
    assert any(
        f"anchor {anchor!r} is not in ANCHOR_ALLOWLIST" in i
        for i in report.safety_issues
    )
    assert report.outcome is ValidationOutcome.RECOVERABLE_REPAIR


def test_phase33_b_repair_context_missing_authoritative_anchor_vocabulary():
    """MODEL-FACING GAP (RED on the current code): the REPAIR prompt context
    does NOT carry the anchor-id vocabulary (``_WORLD_FIELD_RULES`` teaches
    relation kinds + environmentHint but never anchor ids), so a model cannot
    know any legal anchor.

    Phase33.md RAD-1 classification: **Missing trusted context**. Green once
    the RAD-2 scope block states ANCHOR_ALLOWLIST.
    """
    attempt = _attempt(parse_full_draft(_draft_json(anchor="under_the_rug")))
    request, _report = _repair_request(attempt)

    context = request.prompt_context
    assert "under_the_rug" in context  # the draft's own invalid anchor
    for anchor in sorted(ANCHOR_ALLOWLIST):
        assert anchor in context, (
            f"REPAIR prompt context lacks the authoritative anchor {anchor!r} — "
            "no legal anchor is known to the model (Phase33 RAD-1 'Missing "
            "trusted context')"
        )

    rendered = prompts.build_repair_prompt(request.prompt_context, request.diagnostics)
    for anchor in sorted(ANCHOR_ALLOWLIST):
        assert anchor in rendered, (
            f"REPAIR template lacks anchor {anchor!r} in its scope block"
        )


# ---------------------------------------------------------------------------
# CLASS C — locked-witness mismatch (terminal) 
# (Phase33.md RAD-1 row: Ambiguous guidance)
# ---------------------------------------------------------------------------


def _locked_witness_draft():
    """A deterministic structurally-valid draft with NO witness person."""
    return parse_full_draft(_draft_json())


def test_phase33_c_locked_witness_mismatch_terminal():
    """Rejection semantics (must stay green): a draft whose persons contain no
    role-normalized witness matching the locked Witness id/name is a TERMINAL
    failure — the lock is unfixable inside the attempt."""
    locked = LockedConstraints(witness="emily_reed")
    draft = _locked_witness_draft()
    violations = locked.violations_against(draft)
    assert violations
    assert any(
        "locked witness 'emily_reed' not found among draft persons with role "
        "'witness'" in v
        for v in violations
    )

    attempt = _attempt(draft, locked=locked)
    report = pipeline.validate_draft(attempt)
    assert report.locked_violations
    assert report.outcome is ValidationOutcome.TERMINAL_FAILURE
    # Terminal: nothing is repairable about a violated lock by design.
    assert any(
        "locked witness" in d for d in report.repair_diagnostics
    )


def test_phase33_c_repair_context_missing_witness_contract_guidance():
    """MODEL-FACING GAP (RED on the current code): even the REPAIR/PUBLIC_WORLD
    prompt context does NOT tell the model that the locked ``Witness:`` value
    MUST be realized as a person whose id/name matches it — the persons rule
    only says 'include the witness' without binding it to the lock.

    Phase33.md RAD-1 classification: **Ambiguous guidance**. Green once the
    RAD-2 scope block states the witness contract explicitly (the lock itself
    is unchanged and still terminal).
    """
    locked = LockedConstraints(witness="emily_reed")
    attempt = _attempt(_locked_witness_draft(), locked=locked)
    request, _report = _repair_request(attempt)

    context = request.prompt_context
    assert "emily_reed" not in context  # the value is only in locked constraints
    # The REPAIR context must state the witness-in-the-lock contract.
    assert "witness" in context.casefold()
    assert any(
        marker in context.casefold()
        for marker in (
            "must include",          # persons MUST include the locked witness
            "matches the locked witness",
            "locked witness",
            "witness contract",
        )
    ), (
        "REPAIR prompt context lacks the locked-witness contract guidance — "
        "the model cannot know persons must realize the locked Witness "
        "(Phase33 RAD-1 'Ambiguous guidance')"
    )

    rendered = prompts.build_repair_prompt(request.prompt_context, request.diagnostics)
    assert any(
        marker in rendered.casefold()
        for marker in (
            "matches the locked witness",
            "locked witness",
            "must include",
            "witness contract",
        )
    ), "REPAIR template lacks the locked-witness contract guidance"