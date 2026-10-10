"""Phase35 — publication boundary + validator telemetry + prompt wiring.

- publication-boundary truth-isolation guard: a public DTO person whose role is
  ``murderer`` (or outside the closed public role vocabulary) is BLOCKED at the
  mapper (never silently renamed, never served) with the TYPED
  ``PublicRoleTruthLeak`` exception (DEF-078) — and every read/export path
  maps it to a 409 PUBLIC_ROLE_TRUTH_LEAK envelope instead of a generic 500;
- ``public_case_dict_from_payload`` stays byte-identical for the closed
  vocabulary roles (golden/driver worlds) — no regression;
- the Phase35 quality codes flow into validator-code telemetry and the
  report diagnostics (safe, closed, non-secret);
- the compact CASE QUALITY RULES block is wired into the persons/evidence/
  repair prompt templates AND no full golden savegame is ever embedded
  (incl. the §30 golden-person-id/evidence-id/name markers, DEF-081);
- the REVEAL-GATED savegame export answers a typed 409 (never a bare 500)
  for a legacy truth-bearing role row (DEF-078).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation import prompts  # noqa: E402
from app.generation import validation_codes as vc  # noqa: E402
from app.generation.report import ValidationReport  # noqa: E402
from app.services import publication as pub  # noqa: E402
from app.services.publication import PublicRoleTruthLeak  # noqa: E402

from phase5_helpers import auth  # noqa: E402
from phase6_helpers import client as phase6_client  # noqa: E402
from test_case_quality_corpus import canonical_inputs  # noqa: E402


# --------------------------------------------------------------------------- #
# publication-boundary role guard
# --------------------------------------------------------------------------- #


def _payload_with_role(payload: dict, role: str) -> dict:
    import copy

    hostile = copy.deepcopy(dict(payload))
    hostile["draft"]["persons"][0]["role"] = role
    return hostile


def _payload_without_role_key(payload: dict) -> dict:
    import copy

    hostile = copy.deepcopy(dict(payload))
    hostile["draft"]["persons"][0].pop("role", None)
    return hostile


def test_public_dto_rejects_murderer_role(monkeypatch):
    from test_phase19g_evidence_render import _hard_payload

    payload = _hard_payload()
    hostile = _payload_with_role(payload, "murderer")
    with pytest.raises(PublicRoleTruthLeak):
        pub.public_case_dict_from_payload(hostile)


def test_public_dto_rejects_any_role_outside_closed_vocabulary(monkeypatch):
    from test_phase19g_evidence_render import _hard_payload

    payload = _hard_payload()
    for role in ("killer", "detective", "perpetrator", "suspects", ""):
        hostile = _payload_with_role(payload, role)
        with pytest.raises(PublicRoleTruthLeak):
            pub.public_case_dict_from_payload(hostile)


def test_public_dto_rejects_deleted_role_key_as_typed_leak(monkeypatch):
    """The legacy missing-``role``-key shape is a typed leak, not a Value/text
    confusion — the DTO refuses to synthesize a public pre-reveal role."""
    from test_phase19g_evidence_render import _hard_payload

    payload = _hard_payload()
    hostile = _payload_without_role_key(payload)
    with pytest.raises(PublicRoleTruthLeak):
        pub.public_case_dict_from_payload(hostile)


def test_public_dto_closed_vocabulary_roles_unchanged(monkeypatch):
    from test_phase19g_evidence_render import _hard_payload

    payload = _hard_payload()
    dto = pub.public_case_dict_from_payload(payload)
    assert [p["role"] for p in dto["persons"]] == [
        "victim",
        "suspect",
        "suspect",
        "suspect",
        "witness",
    ]


def test_savegame_export_also_blocks_truth_bearing_role(monkeypatch):
    from test_phase19g_evidence_render import _hard_payload
    from app.services import savegame as sg

    payload = _hard_payload()
    hostile = _payload_with_role(payload, "murderer")
    with pytest.raises(PublicRoleTruthLeak):
        sg.project_savegame_v1(hostile, difficulty=None)


# --------------------------------------------------------------------------- #
# DEF-078 — typed legacy-role boundary on EVERY read/export path
# --------------------------------------------------------------------------- #


def test_legacy_truth_bearing_role_is_typed_not_500_on_get_case():
    """GET /cases/{id} (creator dossier) translates the typed leak to the
    non-500 PUBLIC_ROLE_TRUTH_LEAK envelope; the mapping never echoes the
    truth-bearing role token into the message."""
    from app.api.v1 import errors
    from test_phase19g_evidence_render import _hard_payload

    for role in ("murderer", "perpetrator", ""):
        payload = _hard_payload()
        hostile = _payload_with_role(payload, role)
        with pytest.raises(PublicRoleTruthLeak) as excinfo:
            pub.public_case_dict_from_payload(hostile)
        assert isinstance(excinfo.value, PublicRoleTruthLeak)
        assert "murderer" not in excinfo.value.reason
        mapped = errors.map_service_error(excinfo.value)
        assert mapped.status_code != 500
        assert mapped.status_code == 409
        detail = mapped.detail
        assert detail["code"] == "PUBLIC_ROLE_TRUTH_LEAK"
        assert "murderer" not in str(detail["message"])
        # SANITIZED: a non-empty offending role token is never echoed into the
        # envelope ('' is a substring of every string, so skipped).
        if role:
            assert role not in str(detail["message"])


def test_legacy_deleted_role_key_is_typed_not_500_on_playthrough_public_case():
    """GET /playthroughs/{id}/public-case shares the SAME mapper+translation:
    a missing role key answers the typed 409, never a bare 500, and no
    pre-reveal truth DTO is ever produced."""
    from app.api.v1 import errors
    from test_phase19g_evidence_render import _hard_payload

    payload = _hard_payload()
    hostile = _payload_without_role_key(payload)
    with pytest.raises(PublicRoleTruthLeak) as excinfo:
        pub.public_case_dict_from_payload(
            hostile, discovered=frozenset({"d_ev_when_last_seen"})
        )
    mapped = errors.map_service_error(excinfo.value)
    assert mapped.status_code == 409
    assert mapped.detail["code"] == "PUBLIC_ROLE_TRUTH_LEAK"


def test_legacy_truth_bearing_role_export_is_typed_not_500():
    """Save-Case export (``project_savegame_v1``) is a KNOWN-TRUST post-reveal
    document (it legitimately carries ``replayTruth``), but its
    ``case.publicCase`` block is the EXACT pre-reveal PublicCaseResponse shape
    the replay runtime renders before re-reveal — so the truth-bearing role is
    still refused (typed), and the API translation answers 409 (never 500).
    The 403 reveal gate runs first on the real route, so the only export
    outcomes for such a legacy row are 403 or this typed 409."""
    from app.api.v1 import playthroughs as pt_api
    from app.services import savegame as sg
    from test_phase19g_evidence_render import _hard_payload

    payload = _hard_payload()
    hostile = _payload_with_role(payload, "murderer")
    with pytest.raises(PublicRoleTruthLeak) as excinfo:
        sg.project_savegame_v1(hostile, difficulty="medium")
    mapped = pt_api._translate_savegame_error(excinfo.value)
    assert mapped.status_code == 409
    assert mapped.detail["code"] == "PUBLIC_ROLE_TRUTH_LEAK"
    assert "murderer" not in str(mapped.detail)
    # Security-boundary proof: the raw serialized payload itself still carries
    # the truth role only in the stored row — no DTO ever contains it.
    dto_attempt_never_emits = False
    try:
        pub.public_case_dict_from_payload(hostile)
    except PublicRoleTruthLeak:
        dto_attempt_never_emits = True
    assert dto_attempt_never_emits


def _legacy_published_version(
    store, case_id: str, legacy_payload: dict
) -> object:
    """A ``PublishedVersion``-shaped row carrying a legacy (poisoned) payload.

    The real published_versions table is DB-immutable (BEFORE UPDATE/DELETE
    triggers), so a pre-Phase35 legacy row is simulated by REPLACING the
    store read — every reader (generation/accusation/savegame services) only
    ever sees ``payload_json``. This is exactly the stored-row replay the
    ADV-35-02 finding said the corpus tests never exercised.
    """
    from app.models.published import PublishedVersion

    row = store.get_published(case_id, 1)
    assert row is not None
    return PublishedVersion(
        case_id=case_id,
        case_version=1,
        payload_json=json.dumps(
            legacy_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ),
        published_at=row.published_at,
    )


def test_legacy_truth_bearing_payload_replayed_from_stored_row(phase5_app, monkeypatch):
    """The ADV-35-02 circular hole: a PRE-PHASE35 ``published_versions`` row
    (role bare ``ValueError`` -> generic 500). After DEF-078 a stored-row
    replay of a legacy ``role="murderer"`` row answers the TYPED 409 on every
    read/export route."""
    from test_phase7_helpers import (  # noqa: PLC0415
        create_published_case_and_playthrough,
        get_reveal,
        make_accusation,
        winning_body,
    )

    bundle = create_published_case_and_playthrough(phase5_app)
    case_id = bundle["caseId"]
    store = phase5_app.state.store
    row = store.get_published(case_id, 1)
    assert row is not None
    legacy = json.loads(row.payload_json)
    legacy["draft"]["persons"][0]["role"] = "murderer"
    poisoned = _legacy_published_version(store, case_id, legacy)
    monkeypatch.setattr(store, "get_published", lambda c, v: poisoned)
    monkeypatch.setattr(store, "get_latest_published", lambda c: poisoned)

    with phase6_client(phase5_app) as c:
        # (a) GET case (creator dossier) — typed, never a generic 500.
        res = c.get(f"/api/v1/cases/{case_id}", headers=auth(bundle["creator"]))
        assert res.status_code == 409, res.status_code
        error = res.json()["error"]
        assert error["code"] == "PUBLIC_ROLE_TRUTH_LEAK"
        assert error["message"] == "Stored case data violates the closed public-role contract"
        assert "murderer" not in json.dumps(res.json())
        # (b) playthrough public-case — same typed outcome pre-accusation.
        res = c.get(
            f"/api/v1/playthroughs/{bundle['playthroughId']}/public-case",
            headers=auth(bundle["playthroughToken"]),
        )
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "PUBLIC_ROLE_TRUTH_LEAK"
        assert "murderer" not in json.dumps(res.json())
        # (c) Save-Case export: pre-reveal the 403 reveal gate runs FIRST
        # (never a 500). After accuse+reveal the export hits the same mapper
        # refusal and answers the typed 409.
        res = c.get(
            f"/api/v1/playthroughs/{bundle['playthroughId']}/savegame",
            headers=auth(bundle["playthroughToken"]),
        )
        assert res.status_code == 403, res.status_code  # reveal gate, not a 500
        body = winning_body(bundle["truth"])
        assert make_accusation(
            c, bundle["playthroughId"], bundle["playthroughToken"], body
        ).status_code == 200
        assert get_reveal(
            c, bundle["playthroughId"], bundle["playthroughToken"]
        ).status_code == 200
        res = c.get(
            f"/api/v1/playthroughs/{bundle['playthroughId']}/savegame",
            headers=auth(bundle["playthroughToken"]),
        )
        assert res.status_code == 409, res.status_code
        assert res.json()["error"]["code"] == "PUBLIC_ROLE_TRUTH_LEAK"
        assert "murderer" not in json.dumps(res.json())


def test_legacy_deleted_role_key_replayed_from_stored_row(phase5_app, monkeypatch):
    """The legacy deleted-``role`` key answers the same typed 409 on the read
    paths after a stored-row replay (DEF-078), and the DTO is never built."""
    from test_phase7_helpers import (  # noqa: PLC0415
        create_published_case_and_playthrough,
    )

    bundle = create_published_case_and_playthrough(phase5_app)
    case_id = bundle["caseId"]
    store = phase5_app.state.store
    row = store.get_published(case_id, 1)
    assert row is not None
    legacy = json.loads(row.payload_json)
    legacy["draft"]["persons"][0].pop("role", None)
    poisoned = _legacy_published_version(store, case_id, legacy)
    monkeypatch.setattr(store, "get_published", lambda c, v: poisoned)
    monkeypatch.setattr(store, "get_latest_published", lambda c: poisoned)
    with phase6_client(phase5_app) as c:
        res = c.get(f"/api/v1/cases/{case_id}", headers=auth(bundle["creator"]))
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "PUBLIC_ROLE_TRUTH_LEAK"
        res = c.get(
            f"/api/v1/playthroughs/{bundle['playthroughId']}/public-case",
            headers=auth(bundle["playthroughToken"]),
        )
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "PUBLIC_ROLE_TRUTH_LEAK"


# --------------------------------------------------------------------------- #
# validator-code telemetry (closed, safe)
# --------------------------------------------------------------------------- #


def test_quality_issues_map_to_closed_validator_codes():
    report = ValidationReport(quality_issues=("WITNESS_STATEMENT_MISSING",))
    codes = vc.validation_failure_codes(report)
    assert "WITNESS_STATEMENT_MISSING" in codes
    assert set(codes) <= vc.VALIDATOR_CODE_VOCABULARY
    assert vc.VALIDATOR_CODE_GENERIC in codes


def test_public_role_truth_leak_maps_to_validator_codes():
    report = ValidationReport(quality_issues=("PUBLIC_ROLE_TRUTH_LEAK",))
    codes = vc.validation_failure_codes(report)
    assert "PUBLIC_ROLE_TRUTH_LEAK" in codes
    assert set(codes) <= vc.VALIDATOR_CODE_VOCABULARY


def test_quality_codes_are_registered_in_the_closed_vocabulary():
    for code in (
        "PUBLIC_ROLE_TRUTH_LEAK",
        "VICTIM_IN_SUSPECT_CANDIDATES",
        "WITNESS_IN_SUSPECT_CANDIDATES",
        "MURDERER_NOT_SUSPECT_CANDIDATE",
        "WITNESS_STATEMENT_MISSING",
        "WITNESS_STATEMENT_EMPTY",
        "WITNESS_STATEMENT_UNKNOWN_WITNESS",
        "WITNESS_STATEMENT_WITNESS_ID_MISMATCH",
        "WITNESS_STATEMENT_SPEAKER_MISMATCH",
    ):
        assert code in vc.VALIDATOR_CODE_VOCABULARY, code


def test_outcome_branch_is_recoverable_repair_for_quality_issues():
    from app.generation.state_machine import ValidationOutcome

    report = ValidationReport(quality_issues=("WITNESS_STATEMENT_MISSING",))
    assert report.outcome is ValidationOutcome.RECOVERABLE_REPAIR
    report2 = ValidationReport(quality_issues=("PUBLIC_ROLE_TRUTH_LEAK",))
    assert report2.outcome is ValidationOutcome.RECOVERABLE_REPAIR


def test_quality_codes_never_leak_secret_material():
    report = ValidationReport(
        quality_issues=(
            "WITNESS_STATEMENT_MISSING",
            "PUBLIC_ROLE_TRUTH_LEAK",
        )
    )
    diagnostics = report.repair_diagnostics
    assert diagnostics == (
        "PUBLIC_ROLE_TRUTH_LEAK",
        "WITNESS_STATEMENT_MISSING",
    )
    codes = vc.validation_failure_codes(report)
    blob = "|".join(codes)
    assert "thomas_reed" not in blob
    assert "murdererId" not in blob


# --------------------------------------------------------------------------- #
# golden negative through the full PARSED validator does not touch providers
# --------------------------------------------------------------------------- #


def test_negative_codes_flow_into_report_diagnostics():
    document = _read_negative()
    public, truth, evidence, universes = canonical_inputs(document)
    from app.generation import case_quality

    codes = case_quality.validate_case_quality(public, truth, evidence, universes)
    report = ValidationReport(quality_issues=codes)
    for code in codes:
        assert code in report.repair_diagnostics


def _read_negative():
    from test_case_quality_corpus import NEGATIVE_01

    return json.loads(NEGATIVE_01.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# prompt wiring — compact CASE QUALITY RULES + no full golden embedded
# --------------------------------------------------------------------------- #


def _markers_for(path) -> tuple[str, ...]:
    """Unique marker strings of one golden savegame — the §30 markers.

    DEF-081 (ADV-35-06): the marker set is NOT just ``sourceCaseId`` + the
    first 60 chars of each statement. It also covers the §30-named classes —
    each golden's DISTINCTIVE person ids, person NAMES and evidence ids (the
    Demo-case identities from the golden corpus, e.g. ``demora`` names like
    ``amara_okafor`` / ``daniel_voss`` / ``thomas_reed`` and evidence ids
    like ``witness_statement_emily_01``) — so a future golden injection into a
    prompt template or stage context is caught even when the statement text
    changed. Exception (documented, pre-existing Phase 33 prompt content):
    ``prompts._IDENTITY_MATCH_CONTRACT`` ships the accented example
    ``'Émily Reed'`` in the identity-normalization contract. The accented
    spelling is NOT a golden injection (it predates Phase35 and differs from
    the golden's plain ``Emily Reed``); the golden markers therefore use the
    exact plain names/ids — never the bare surname token ``Reed`` (which WOULD
    match the accented example) — plus the golden's specific evidence id.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    case = document["case"]
    markers: list[str] = [case["metadata"]["sourceCaseId"]]
    public_persons = case["publicCase"]["persons"]
    for person in public_persons:
        pid = str(person.get("personId") or "person")
        name = str(person.get("name") or "")
        # Distinctive person id (never generic, never the accented prompt
        # spelling) + the exact public display name.
        markers.append(pid)
        if name and len(name) >= 6:
            markers.append(name)
    for rec in case["evidence"]:
        markers.append(str(rec["evidenceId"]))
        content = rec.get("content") or {}
        statement = content.get("statement")
        if isinstance(statement, str) and statement:
            markers.append(statement[:60])
    return tuple(dict.fromkeys(markers))


def _all_marker_substrings() -> tuple[str, ...]:
    from test_case_quality_corpus import GOLDEN_01, GOLDEN_02, GOLDEN_03

    out = []
    for path in (GOLDEN_01, GOLDEN_02, GOLDEN_03):
        out.extend(_markers_for(path))
    return tuple(out)


def test_golden_markers_cover_person_and_evidence_identities():
    """DEF-081 — the §30 golden-marker gate genuinely scans the NAMED classes:
    every golden's distinctive person ids, exact person names and evidence ids
    are members of the marker set, so a future golden identity injection
    (names/ids — even without the statement text) is caught. Note: the bare
    surname marker ``Reed`` is deliberately NOT present — ``prompts.py`` ships
    a pre-existing, pre-Phase35 accented ``'Émily Reed'`` identity-normalization
    example that is NOT a golden injection; the more specific
    ``witness_statement_emily_01`` / ``emily_reed`` / plain ``Emily Reed``
    markers cover the golden instead."""
    from test_case_quality_corpus import GOLDEN_01, GOLDEN_02, GOLDEN_03

    all_markers = "|".join(_all_marker_substrings())
    for name in ("amara_okafor", "daniel_voss", "thomas_reed", "emily_reed",
                 "hugo_brandt", "yara_salim", "jana_petersen", "sarah_miller"):
        assert name in all_markers, name
    for evidence_id in ("witness_statement_emily_01", "witness_statement_hugo_01",
                        "witness_statement_yara_01", "witness_statement_jana_01"):
        assert evidence_id in all_markers, evidence_id
    for name in ("Dr. Amara Okafor", "Daniel Voss", "Thomas Reed", "Emily Reed"):
        assert name in all_markers, name
    assert "Reed" not in all_markers.split("|"), (
        "the bare surname must not be a marker (collides with the pre-existing "
        "accented 'Émily Reed' prompt example)"
    )
    # The §30-gate is exercised against the real templates: an injected golden
    # identity would trip the absence loop.
    for marker in ("emily_reed", "witness_statement_emily_01", "amara_okafor"):
        for template in (
            prompts.CASE_PEOPLE_PROMPT_v1,
            prompts.EVIDENCE_PROMPT_v1,
            prompts.WORLD_REQUIREMENTS_PROMPT_v1,
            prompts.REPAIR_PROMPT_v1,
        ):
            assert marker not in template, marker


def test_case_quality_rules_block_is_present_in_templates():
    assert "CASE QUALITY RULES" in prompts.CASE_PEOPLE_PROMPT_v1
    assert "CASE QUALITY RULES" in prompts.EVIDENCE_PROMPT_v1
    assert "CASE QUALITY RULES" in prompts.REPAIR_PROMPT_v1
    rendered = prompts.build_case_people_prompt("ctx", None)
    assert "CASE QUALITY RULES" in rendered


def test_case_quality_rules_mirror_the_deterministic_contract():
    block = prompts.CASE_QUALITY_RULES
    lowered = block.casefold()
    assert "never appear in suspect candidates" in lowered
    assert "murderer" in lowered  # the TRUTH-ISOLATION sentence
    assert "witness_statement" in lowered


def test_full_golden_savegame_never_embedded_in_prompt_templates():
    markers = _all_marker_substrings()
    assert markers, "expected at least one unique golden marker"
    templates = (
        prompts.CASE_PEOPLE_PROMPT_v1,
        prompts.EVIDENCE_PROMPT_v1,
        prompts.WORLD_REQUIREMENTS_PROMPT_v1,
        prompts.REPAIR_PROMPT_v1,
        prompts.ASSET_SPEC_PROMPT_v1,
        prompts.ACTIVITY_LOG_PROMPT_v1,
    )
    for template in templates:
        for marker in markers:
            assert marker not in template, (
                f"full golden content leaked into a prompt template ({marker[:30]}...)"
            )


def test_full_golden_savegame_never_embedded_in_stage_context():
    """The pipeline stage-context builder (REPAIR / PUBLIC_WORLD / EVIDENCE /
    CASE_TRUTH) never embeds any full golden savegame marker."""
    from app.generation.pipeline import AttemptRecord, _stage_context
    from app.generation.provider import GenerationStage

    attempt = AttemptRecord(
        attempt_id="GA-golden-leak", case_id="CASE-x", session_id="QUOTA-x"
    )
    markers = _all_marker_substrings()
    for stage in (
        GenerationStage.CASE_TRUTH,
        GenerationStage.PUBLIC_WORLD,
        GenerationStage.EVIDENCE,
        GenerationStage.WORLD_GRAPH,
        GenerationStage.REPAIR,
    ):
        try:
            context = _stage_context(attempt, stage)
        except Exception:  # noqa: BLE001 - an empty attempt degrades safely
            continue
        for marker in markers:
            assert marker not in context, (
                f"full golden content leaked into {stage.value} context "
                f"({marker[:30]}...)"
            )


def test_repair_context_carries_bounded_quality_context():
    """A quality-broken draft's REPAIR diagnostics carry the safe closed issue
    code; the REPAIR stage context includes the bounded CASE QUALITY FIX
    CONTEXT block (never truth)."""
    from app.generation.parser import parse_full_draft  # noqa: PLC0415
    from dataclasses import replace  # noqa: PLC0415

    from fixtures.golden_generation import GOLDEN_FULL_DRAFT  # noqa: PLC0415
    from app.generation.pipeline import AttemptRecord, validate_draft  # noqa: PLC0415

    draft = parse_full_draft(GOLDEN_FULL_DRAFT)
    assert draft is not None
    draft = replace(
        draft,
        evidence=tuple(e for e in draft.evidence if e.kind != "witness_statement"),
    )
    attempt = AttemptRecord(
        attempt_id="GA-quality-repair", case_id="CASE-x", session_id="QUOTA-x"
    )
    attempt.draft = draft
    report = validate_draft(attempt)
    assert "WITNESS_STATEMENT_MISSING" in report.repair_diagnostics


def test_repair_context_masks_truth_bearing_public_role():
    """DEF-080 — the REPAIR FIX CONTEXT projects every public role through
    PUBLIC_ROLE_VOCABULARY: a quality-broken draft whose public role was
    mutated to ``murderer`` (PUBLIC_ROLE_TRUTH_LEAK) NEVER re-echoes the raw
    ``murderer`` (or any out-of-vocabulary role token) — the person id +
    ``<invalid>`` is printed instead, so the §37/"never murderer identity"
    contract actually holds."""
    from app.generation.case_quality import PUBLIC_ROLE_TRUTH_LEAK  # noqa: PLC0415
    from app.generation.parser import parse_full_draft  # noqa: PLC0415

    from fixtures.golden_generation import GOLDEN_FULL_DRAFT  # noqa: PLC0415
    from app.generation.pipeline import (  # noqa: PLC0415
        AttemptRecord,
        case_quality_repair_context,
        validate_draft,
    )

    raw = json.loads(GOLDEN_FULL_DRAFT)
    mutated_role_person = None
    for person in raw["persons"]:
        if person.get("name") == "Thomas Reed":
            person["role"] = "murderer"  # the draft itself carries the leak
            mutated_role_person = person.get("personId")
    assert mutated_role_person, "golden full draft should carry Thomas Reed"
    draft = parse_full_draft(json.dumps(raw))
    assert draft is not None
    attempt = AttemptRecord(
        attempt_id="GA-quality-role-mask", case_id="CASE-x", session_id="QUOTA-x"
    )
    attempt.draft = draft
    report = validate_draft(attempt)
    assert PUBLIC_ROLE_TRUTH_LEAK in report.quality_issues
    context = case_quality_repair_context(attempt)
    assert context, "repair context must be produced for the broken draft"
    # The truth-bearing token and every out-of-vocabulary role are masked.
    assert "murderer" not in context
    assert f"{mutated_role_person} (<invalid>)" in context
    # Closed-vocabulary roles are still echoed (the required fix guidance).
    assert f"{mutated_role_person} (murderer)" not in context
    assert "(witness)" in context


def test_repair_prompt_diagnostics_are_safe():
    from app.generation.pipeline import build_request
    from app.generation.provider import GenerationStage
    from app.generation.report import ValidationReport

    report = ValidationReport(quality_issues=("WITNESS_STATEMENT_MISSING",))
    attempt = _attempt_with_report(report)
    request = build_request(
        attempt,
        GenerationStage.REPAIR,
        diagnostics=report.repair_diagnostics,
    )
    assert request.diagnostics == ("WITNESS_STATEMENT_MISSING",)
    assert "murdererId" not in "|".join(request.diagnostics)


def _attempt_with_report(report):
    from app.generation.pipeline import AttemptRecord

    attempt = AttemptRecord(
        attempt_id="GA-quality-telemetry", case_id="CASE-x", session_id="QUOTA-x"
    )
    attempt.last_validation = report
    return attempt
