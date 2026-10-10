"""Phase35 — generated case quality + publication + savegame export regression.

Proves the hermetic generator-to-publish path end-to-end over a REAL driver-
generated world (same deterministic controller the rest of the suite uses):

- the generated case passes the Phase35 case-quality validator;
- the controller PUBLISHES it (quality gate must not block a valid case);
- the published payload projects through ``public_case_dict_from_payload``
  (closed role vocabulary) and ``project_savegame_v1`` (source == generated);
- the committed generated-case corpus fixture under
  ``tests/fixtures/case_quality/generated/`` is a valid SavegameV1 document
  that also passes the quality validator — the frontend-dev replay reference.

ZERO provider calls (mock transport); deterministic (IdSource counters +
pinned exportedAt), so the live projection is byte-content-identical to the
committed fixture's ``case`` section.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation.state_machine import GenerationState  # noqa: E402
from app.services import savegame as sg  # noqa: E402

from test_phase19g_evidence_render import _hard_payload  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATED_FIXTURE = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "case_quality"
    / "generated"
    / "procedural-detective-case-demo-hard-generate.ok.pdcase"
)
_PINNED_EXPORTED_AT = "2026-10-10T00:00:00Z"


def test_generated_world_publishes_with_clean_quality():
    """The driver-generated world (mock transport, hermetic) PASSES quality and
    publishes — the Phase35 gate must accept witness-complete generated cases."""
    payload = _hard_payload()  # asserts PUBLISHED internally
    from app.domain.eligibility import derive_universes
    from app.generation import case_quality

    public = _public_of(payload)
    truth = _truth_of(payload)
    evidence = _evidence_of(payload)
    codes = case_quality.validate_case_quality(
        public, truth, evidence, derive_universes(public), None
    )
    assert codes == (), codes


def test_generated_savegame_export_has_source_generated_and_valid_shape():
    payload = _hard_payload()
    doc = sg.project_savegame_v1(
        payload, difficulty="medium", exported_at=_PINNED_EXPORTED_AT
    )
    assert doc["format"] == sg.SAVEGAME_FORMAT
    assert doc["formatVersion"] == sg.SAVEGAME_FORMAT_VERSION
    assert doc["case"]["metadata"]["source"] == "generated"
    assert doc["case"]["replayTruth"]["murdererId"]  # truth present post-reveal
    assert sg.serialize_savegame_v1(doc)


def test_driver_witness_statement_grounds_time_observation_and_location():
    """DEF-079 — the canonical driver-built witness statement is NOT a
    byte-identical generic sentence: it carries a TIME anchor (the case's own
    canonical clock) and the scene's public name, so
    ``project_witness_statement`` grounds TIME + OBSERVATION (+ LOCATION) for
    every generated witness instead of only the single generic OBSERVATION.
    ``_hard_payload`` runs the REAL driver (mock transport, hermetic)."""
    from app.domain import witness as witness_domain
    from app.domain.witness import WitnessQuestionType

    payload = _hard_payload()
    draft = payload["draft"]
    witnesses = [p for p in draft["persons"] if p["role"] == "witness"]
    assert witnesses, "the hard driver world carries a witness"
    for person in witnesses:
        witness_view = {"person_id": person["person_id"], "name": person["name"]}
        for question in (
            WitnessQuestionType.OBSERVATION,
            WitnessQuestionType.TIME,
            WitnessQuestionType.LOCATION,
        ):
            projection = witness_domain.project_witness_statement(
                payload, witness_view, question
            )
            assert projection.statement.grounded, (
                person["person_id"],
                question,
            )
    # The statement is case-specific, not the old fixed sentence.
    statements = [
        (e.get("presentation") or {}).get("statement")
        for e in draft["evidence"]
        if e.get("kind") == "witness_statement"
        and (e.get("presentation") or {}).get("statement")
    ]
    assert statements, "driver world must carry a witness statement"
    assert all(
        "nearby during the evening" not in stmt and ":" in stmt for stmt in statements
    )


def test_committed_generated_fixture_is_valid_and_passes_quality():
    assert GENERATED_FIXTURE.is_file(), "generated corpus fixture missing"
    document = json.loads(GENERATED_FIXTURE.read_text(encoding="utf-8"))
    assert document["format"] == sg.SAVEGAME_FORMAT
    assert document["formatVersion"] == sg.SAVEGAME_FORMAT_VERSION
    assert document["case"]["metadata"]["source"] == "generated"
    # Same deterministic canonical inputs the quality validator consumes.
    from test_case_quality_corpus import canonical_inputs

    codes = __import__("app.generation.case_quality", fromlist=["validate_case_quality"]).validate_case_quality(
        *canonical_inputs(document)
    )
    assert codes == (), codes


def test_committed_generated_fixture_matches_live_deterministic_export():
    """The committed generated case is EXACTLY what a fresh identical
    generation produces (deterministic content + pinned exportedAt)."""
    payload = _hard_payload()
    live = sg.project_savegame_v1(
        payload, difficulty="medium", exported_at=_PINNED_EXPORTED_AT
    )
    committed = json.loads(GENERATED_FIXTURE.read_text(encoding="utf-8"))
    assert live["case"] == committed["case"]


def test_fake_provider_generated_case_passes_quality_and_publishes():
    """A COMPLETED generated case driven through the controller with the
    deterministic FakeProvider (golden script) passes the Phase35 quality
    validator and publishes (the fake path is the demo/try-case provider)."""
    from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS  # noqa: PLC0415
    from app.generation import pipeline, case_quality  # noqa: PLC0415
    from app.generation.admission import AdmissionController  # noqa: PLC0415
    from app.generation.clock import ManualClock  # noqa: PLC0415
    from app.generation.controller import GenerationController  # noqa: PLC0415
    from app.generation.fake_provider import FakeProvider  # noqa: PLC0415
    from app.generation.ids import IdSource  # noqa: PLC0415
    from app.generation.state_machine import GenerationState  # noqa: PLC0415

    prompt = (
        "Victim: sarah_miller\nMurderer: thomas_reed\n"
        "Motive: cover_up_embezzlement\nWeapon: kitchen_knife\n"
        "Time: 2026-09-11T22:17:00+02:00\nWitness: emily_reed\n"
    )
    script = {
        pipeline.GenerationStage.CASE_TRUTH: [GOLDEN_STAGE_PAYLOADS[pipeline.GenerationStage.CASE_TRUTH]],
        pipeline.GenerationStage.PUBLIC_WORLD: [GOLDEN_STAGE_PAYLOADS[pipeline.GenerationStage.PUBLIC_WORLD]],
        pipeline.GenerationStage.EVIDENCE: [GOLDEN_STAGE_PAYLOADS[pipeline.GenerationStage.EVIDENCE]],
        pipeline.GenerationStage.WORLD_GRAPH: [GOLDEN_STAGE_PAYLOADS[pipeline.GenerationStage.WORLD_GRAPH]],
    }
    clock = ManualClock()
    ids = IdSource()
    admission = AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=2,
        max_concurrent_generations_global=4,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        anonymous_quota_session_ttl_seconds=86400,
    )
    session = admission.create_anonymous_quota_session()
    controller = GenerationController(
        provider=FakeProvider(script=script),
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=0,
        max_full_regenerations=0,
        max_prompt_chars=4000,
    )
    handle = controller.start_generation(
        prompt, anonymous_quota_session_id=session.session_id
    )
    attempt = controller.attempt(handle.attempt_id)
    assert attempt.state is GenerationState.PUBLISHED
    assert attempt.last_validation is not None
    assert attempt.last_validation.outcome.value == "VALID"
    assert attempt.last_validation.quality_issues == ()
    # The EXPORT path works on a real generated published case (backend side).
    payload = attempt.published
    from app.services.publication import serialize_published_payload  # noqa: PLC0415

    import json as _json

    exported = _json.loads(serialize_published_payload(payload, title="t"))
    doc = sg.project_savegame_v1(exported, difficulty=None)
    assert doc["format"] == sg.SAVEGAME_FORMAT
    assert doc["case"]["metadata"]["source"] in ("demo", "generated")


# --------------------------------------------------------------------------- #
# canonical reconstruction helpers (same shape as the corpus test)
# --------------------------------------------------------------------------- #


def _public_of(payload: dict):
    from app.domain.public import (
        PublicCase,
        PublicLocation,
        PublicMotive,
        PublicObject,
        PublicPerson,
        PublicScene,
        PublicTravelRule,
    )

    draft = payload["draft"]
    scene = draft.get("scene") or {}
    return PublicCase(
        case_id=payload["caseId"],
        case_version=payload["caseVersion"],
        persons=tuple(
            PublicPerson(
                person_id=p["person_id"],
                name=p["name"],
                role=p["role"],
                public_affordances=frozenset(p["affordances"]),
            )
            for p in draft["persons"]
        ),
        motives=tuple(
            PublicMotive(
                motive_id=m["motive_id"],
                label=m["label"],
                public_affordances=frozenset(m["affordances"]),
            )
            for m in draft["motives"]
        ),
        objects=tuple(
            PublicObject(
                object_id=o["object_id"],
                asset_id=o["asset_id"],
                public_affordances=frozenset(o["affordances"]),
                subtype=o.get("subtype"),
            )
            for o in draft["objects"]
        ),
        locations=tuple(
            PublicLocation(location_id=l["location_id"], name=l["name"])
            for l in draft["locations"]
        ),
        travel_rules=tuple(
            PublicTravelRule(
                from_location_id=t["from_location_id"],
                to_location_id=t["to_location_id"],
                travel_time_seconds=t["travel_time_seconds"],
            )
            for t in draft["travel_rules"]
        ),
        scene=(
            PublicScene(location_id=scene["location_id"], name=scene["name"])
            if scene
            else None
        ),
    )


def _truth_of(payload: dict):
    from app.domain.truth import CaseTruth, Crime, CrimeTime

    crime = payload["truth"]["crime"]
    return CaseTruth(
        case_id=payload["caseId"],
        case_version=1,
        title=payload.get("title") or "t",
        crime=Crime(
            type=crime["type"],
            victim_id=crime["victim_id"],
            murderer_id=crime["murderer_id"],
            motive_id=crime["motive_id"],
            weapon_id=crime["weapon_id"],
            location_id=crime["location_id"],
            crime_time=CrimeTime(
                canonical=crime["crime_time"]["canonical"],
                accusation_tolerance_seconds=crime["crime_time"][
                    "accusation_tolerance_seconds"
                ],
            ),
        ),
    )


def _evidence_of(payload: dict):
    # The serialized draft evidence dicts carry id/kind/presentation — the
    # canonical attribution helpers consume kind + presentation (speakerName).
    from types import SimpleNamespace

    out = []
    for e in payload["draft"]["evidence"]:
        out.append(
            SimpleNamespace(
                id=e["id"],
                kind=e["kind"],
                presentation=dict(e.get("presentation") or {}),
                propositions=tuple(
                    SimpleNamespace(person_id=p.get("person_id")) for p in e["propositions"]
                ),
                discoverable=e.get("discoverable", True),
            )
        )
    return tuple(out)
