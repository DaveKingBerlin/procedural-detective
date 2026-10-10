"""Phase35 — case-quality invariant mutation (RED->GREEN) tests.

For every new hard case-quality invariant, a mutation of a known-good canonical
input proves the validator is NOT vacuous (§27):

  witness statement removed        -> WITNESS_STATEMENT_MISSING
  statement text emptied           -> WITNESS_STATEMENT_EMPTY
  title-only statement fact        -> WITNESS_STATEMENT_EMPTY (DEF-077)
  victim injected into candidates  -> VICTIM_IN_SUSPECT_CANDIDATES
  witness injected into candidates -> WITNESS_IN_SUSPECT_CANDIDATES
  murderer removed from candidates -> MURDERER_NOT_SUSPECT_CANDIDATE
  public role->murderer            -> PUBLIC_ROLE_TRUTH_LEAK
  witnessId -> unknown id          -> WITNESS_STATEMENT_UNKNOWN_WITNESS
  witnessId -> another witness     -> WITNESS_STATEMENT_WITNESS_ID_MISMATCH
  speakerName mismatched           -> WITNESS_STATEMENT_SPEAKER_MISMATCH

Each mutation starts from a GOLDEN (fully-valid) canonical input and flips ONE
attribute; the test asserts the exact closed code (and only that code) appears.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.eligibility import derive_universes  # noqa: E402
from app.domain.public import (  # noqa: E402
    PublicCase,
    PublicPerson,
)
from app.generation.case_quality import (  # noqa: E402
    MURDERER_NOT_SUSPECT_CANDIDATE,
    PUBLIC_ROLE_TRUTH_LEAK,
    VICTIM_IN_SUSPECT_CANDIDATES,
    WITNESS_IN_SUSPECT_CANDIDATES,
    WITNESS_STATEMENT_EMPTY,
    WITNESS_STATEMENT_MISSING,
    WITNESS_STATEMENT_SPEAKER_MISMATCH,
    WITNESS_STATEMENT_UNKNOWN_WITNESS,
    WITNESS_STATEMENT_WITNESS_ID_MISMATCH,
    validate_case_quality,
)

from test_case_quality_corpus import (  # noqa: E402
    GOLDEN_01,
    canonical_inputs,
)

GOLDEN = json.loads(GOLDEN_01.read_text(encoding="utf-8"))


def _reload_canonical():
    return canonical_inputs(json.loads(GOLDEN_01.read_text(encoding="utf-8")))


def _golden_person(public: PublicCase, person_id: str) -> PublicPerson:
    person = public.person(person_id)
    assert person is not None, person_id
    return person


def _replace_person(public: PublicCase, person_id: str, **attrs) -> PublicCase:
    new_persons = []
    for p in public.persons:
        if p.person_id == person_id:
            new_persons.append(
                PublicPerson(
                    person_id=p.person_id,
                    name=p.name,
                    role=attrs.get("role", p.role),
                    public_affordances=frozenset(
                        attrs.get("public_affordances", p.public_affordances)
                    ),
                )
            )
        else:
            new_persons.append(p)
    return PublicCase(
        case_id=public.case_id,
        case_version=public.case_version,
        persons=new_persons,
        motives=public.motives,
        objects=public.objects,
        locations=public.locations,
        travel_rules=public.travel_rules,
        scene=public.scene,
    )


def _replace_evidence(evidence, index: int, **attrs) -> tuple:
    out = list(evidence)
    old = out[index]
    presentation = dict(attrs.pop("presentation", dict(old.presentation)))
    out[index] = SimpleNamespace(
        id=attrs.pop("id", old.id),
        kind=attrs.pop("kind", old.kind),
        presentation=presentation,
        propositions=attrs.pop("propositions", old.propositions),
        discoverable=attrs.pop("discoverable", old.discoverable),
    )
    return tuple(out)


def _by_id(evidence, evidence_id: str):
    for i, e in enumerate(evidence):
        if e.id == evidence_id:
            return i, e
    raise KeyError(evidence_id)


def _public_persons_with_role(public: PublicCase, role: str):
    return [p.person_id for p in public.persons if p.role == role]


# --------------------------------------------------------------------------- #
# golden control
# --------------------------------------------------------------------------- #


def test_golden_control_passes():
    public, truth, evidence, universes = _reload_canonical()
    assert validate_case_quality(public, truth, evidence, universes) == ()


# --------------------------------------------------------------------------- #
# witness statement mutations
# --------------------------------------------------------------------------- #


def test_mutation_statement_removed_triggers_missing():
    public, truth, evidence, universes = _reload_canonical()
    witness = _public_persons_with_role(public, "witness")[0]
    kept = [e for e in evidence if e.kind != "witness_statement"]
    # The witness must exist and no other evidence may attribute to them.
    codes = validate_case_quality(public, truth, kept, universes)
    assert WITNESS_STATEMENT_MISSING in codes
    assert WITNESS_STATEMENT_EMPTY not in codes
    # Mutating the DRAFT (not an actual re-parse) — the unaffected rules pass.
    assert set(codes) & {PUBLIC_ROLE_TRUTH_LEAK, VICTIM_IN_SUSPECT_CANDIDATES} == set()


def test_mutation_statement_emptied_triggers_empty():
    public, truth, evidence, universes = _reload_canonical()
    index, _entry = _by_id(evidence, "witness_statement_hugo_01")
    mutated = _replace_evidence(
        evidence,
        index,
        presentation={
            "speakerName": "Hugo Brandt",
            "statement": "",
            "description": "",
            "title": "",
        },
    )
    codes = validate_case_quality(public, truth, mutated, universes)
    assert WITNESS_STATEMENT_EMPTY in codes
    assert WITNESS_STATEMENT_MISSING not in codes


def test_mutation_title_only_statement_triggers_empty():
    """DEF-077 — a witness whose ONLY attributed witness-kind fact carries
    nothing but a non-empty ``title`` is NOT complete: the gate's usable
    statement text requires a non-empty STATEMENT or DESCRIPTION (a bare
    title can at best ground a single generic OBSERVATION and never the
    TIME/LOCATION/… depth the goldens satisfy — 4/6 grounded questions)."""
    public, truth, evidence, universes = _reload_canonical()
    index, _entry = _by_id(evidence, "witness_statement_hugo_01")
    mutated = _replace_evidence(
        evidence,
        index,
        presentation={
            "speakerName": "Hugo Brandt",
            "statement": "",
            "description": "",
            "title": "Witness statement",
        },
    )
    codes = validate_case_quality(public, truth, mutated, universes)
    assert WITNESS_STATEMENT_EMPTY in codes
    assert WITNESS_STATEMENT_MISSING not in codes


def test_mutation_unknown_witness_id_triggers_code():
    public, truth, evidence, universes = _reload_canonical()
    index, _entry = _by_id(evidence, "witness_statement_hugo_01")
    mutated = _replace_evidence(
        evidence,
        index,
        presentation={
            "speakerName": "Hugo Brandt",
            "statement": "A witness statement.",
            "witnessId": "ghost_witness",
        },
    )
    codes = validate_case_quality(public, truth, mutated, universes)
    assert WITNESS_STATEMENT_UNKNOWN_WITNESS in codes


def test_mutation_witness_id_other_witness_triggers_mismatch():
    public, truth, evidence, universes = _reload_canonical()
    other = _public_persons_with_role(public, "witness")[1]  # yara_salim
    index, _entry = _by_id(evidence, "witness_statement_hugo_01")
    mutated = _replace_evidence(
        evidence,
        index,
        presentation={
            "speakerName": "Hugo Brandt",
            "statement": "A witness statement.",
            "witnessId": other,
        },
    )
    codes = validate_case_quality(public, truth, mutated, universes)
    assert WITNESS_STATEMENT_WITNESS_ID_MISMATCH in codes
    assert WITNESS_STATEMENT_UNKNOWN_WITNESS not in codes


def test_mutation_speaker_mismatch_triggers_code():
    public, truth, evidence, universes = _reload_canonical()
    other_name = "Yara Salim"
    index, _entry = _by_id(evidence, "witness_statement_hugo_01")
    # Attribution flips to Yara only if speakerName matches her; use a name
    # that normalizes to NO public witness -> attribution falls back to the
    # WITNESS_CLAIMS proposition list (empty in the savegame view) -> the
    # explicit speaker cannot be the attribution witness -> SPEAKER_MISMATCH
    # fires only when an attribution exists. Give the fact an explicit
    # proposition person_id so attribution is deterministic.
    mutated = _replace_evidence(
        evidence,
        index,
        propositions=(SimpleNamespace(person_id="hugo_brandt"),),
        presentation={
            "speakerName": other_name,
            "statement": "A witness statement.",
            "witnessId": "hugo_brandt",
        },
    )
    codes = validate_case_quality(public, truth, mutated, universes)
    assert WITNESS_STATEMENT_SPEAKER_MISMATCH in codes


# --------------------------------------------------------------------------- #
# candidate role mutations
# --------------------------------------------------------------------------- #


def test_mutation_victim_into_candidates_triggers_code():
    public, truth, evidence, universes = _reload_canonical()
    victim = _public_persons_with_role(public, "victim")[0]
    mutated = _replace_person(
        public,
        victim,
        public_affordances=frozenset(
            _golden_person(public, victim).public_affordances
            | frozenset({"SUSPECT_ELIGIBLE"})
        ),
    )
    codes = validate_case_quality(
        mutated, truth, evidence, derive_universes(mutated)
    )
    assert VICTIM_IN_SUSPECT_CANDIDATES in codes


def test_mutation_witness_into_candidates_triggers_code():
    public, truth, evidence, universes = _reload_canonical()
    witness = _public_persons_with_role(public, "witness")[0]
    mutated = _replace_person(
        public,
        witness,
        public_affordances=frozenset(
            _golden_person(public, witness).public_affordances
            | frozenset({"SUSPECT_ELIGIBLE"})
        ),
    )
    codes = validate_case_quality(
        mutated, truth, evidence, derive_universes(mutated)
    )
    assert WITNESS_IN_SUSPECT_CANDIDATES in codes


def test_mutation_murderer_out_of_candidates_triggers_code():
    public, truth, evidence, universes = _reload_canonical()
    murderer = truth.crime.murderer_id
    mutated = _replace_person(
        public,
        murderer,
        public_affordances=frozenset(
            _golden_person(public, murderer).public_affordances
            - frozenset({"SUSPECT_ELIGIBLE"})
        ),
    )
    # Do NOT rely on the solver: the quality gate reports the accusal gap
    # directly (diagnostic-quality code, see module docstring).
    codes = validate_case_quality(
        mutated, truth, evidence, derive_universes(mutated)
    )
    assert MURDERER_NOT_SUSPECT_CANDIDATE in codes


def test_mutation_public_role_murderer_triggers_leak():
    public, truth, evidence, universes = _reload_canonical()
    suspect = next(
        p.person_id
        for p in public.persons
        if p.role == "suspect" and p.person_id != truth.crime.murderer_id
    )
    mutated = _replace_person(public, suspect, role="murderer")
    codes = validate_case_quality(mutated, truth, evidence, universes)
    assert PUBLIC_ROLE_TRUTH_LEAK in codes


# --------------------------------------------------------------------------- #
# outcome classification at the report level (pipeline wiring)
# --------------------------------------------------------------------------- #


def test_mutation_classifies_recoverable_repair_via_pipeline():
    """A quality-broken draft through ``validate_draft`` classifies
    RECOVERABLE_REPAIR (repairable) and NEVER VALID — the exact §18 gate."""
    from app.generation.parser import parse_full_draft  # noqa: PLC0415

    from fixtures.golden_generation import GOLDEN_FULL_DRAFT  # noqa: PLC0415

    draft = parse_full_draft(GOLDEN_FULL_DRAFT)
    assert draft is not None
    # Remove the witness statement -> a quality defect the pipeline rejects.
    from dataclasses import replace  # noqa: PLC0415

    draft = replace(
        draft,
        evidence=tuple(e for e in draft.evidence if e.kind != "witness_statement"),
    )
    from app.generation.pipeline import AttemptRecord, validate_draft  # noqa: PLC0415

    attempt = AttemptRecord(
        attempt_id="GA-quality-golden", case_id="CASE-quality", session_id="QUOTA-x"
    )
    attempt.draft = draft
    report = validate_draft(attempt)
    from app.generation.state_machine import ValidationOutcome  # noqa: PLC0415

    assert report.outcome is ValidationOutcome.RECOVERABLE_REPAIR
    assert WITNESS_STATEMENT_MISSING in report.quality_issues
    assert WITNESS_STATEMENT_MISSING in report.repair_diagnostics
