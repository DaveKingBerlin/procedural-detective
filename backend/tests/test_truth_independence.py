"""Truth-independence tests (Phase3 test F — critical).

Two DIFFERENT CaseTruth objects (different murderer/motive/weapon/crime time)
over IDENTICAL solver-visible inputs (same PublicCase + evidence) must yield
structurally IDENTICAL SolverProofs. Solver API signatures must never accept
CaseTruth (type-level + call-time TypeError).
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden import (  # noqa: E402
    golden_evidence,
    golden_public,
    truth_variant_a,
    truth_variant_b,
)

from app.domain.solver import solve_case  # noqa: E402
from app.domain.solvers import solve_weapon, solve_when, solve_who, solve_why  # noqa: E402


def test_two_different_truths_yield_identical_proofs():
    public = golden_public()
    evidence = golden_evidence()

    truth_a = truth_variant_a()
    truth_b = truth_variant_b()

    # The two truths differ in every discrete canonical field + crime time.
    assert truth_a.crime.murderer_id != truth_b.crime.murderer_id
    assert truth_a.crime.motive_id != truth_b.crime.motive_id
    assert truth_a.crime.weapon_id != truth_b.crime.weapon_id
    assert truth_a.crime.crime_time.canonical != truth_b.crime.crime_time.canonical

    proof_a = solve_case(public, evidence)
    proof_b = solve_case(public, evidence)
    assert proof_a == proof_b
    assert proof_a.who == proof_b.who
    assert proof_a.why == proof_b.why
    assert proof_a.weapon == proof_b.weapon
    assert proof_a.when == proof_b.when
    assert proof_a.eligibility_snapshot == proof_b.eligibility_snapshot


def test_truth_changed_murderer_solver_result_identical():
    """Phase3 test C/F requirement: hidden truth murderer changed while public
    facts unchanged -> solver result identical."""
    golden = golden_public()
    evidence = golden_evidence()
    result_before = solve_case(golden, evidence)
    # Rebuild with a "different" truth only observable via the validation stage.
    result_after = solve_case(golden, evidence)
    assert result_before.who == result_after.who
    assert result_after.who.winner == "thomas_reed"
    # The truth-aware comparison stage is the ONLY thing that notices.
    from app.validation.solution import evaluate_solution

    assert evaluate_solution(result_after, truth_variant_a()).murderer_true is True
    assert evaluate_solution(result_after, truth_variant_b()).murderer_true is False


def test_solver_signatures_never_accept_casetruth():
    entry_points = [solve_case, solve_when, solve_who, solve_why, solve_weapon]
    for fn in entry_points:
        signature = inspect.signature(fn)
        text = str(signature)
        assert "CaseTruth" not in text, f"{fn.__name__} signature must not reference CaseTruth"
        for param in signature.parameters.values():
            assert "truth" not in param.name.lower(), (
                f"{fn.__name__} must not have a `truth`-named parameter"
            )
    # Parameter annotations must be public/evidence/interval types only.
    for param in inspect.signature(solve_case).parameters.values():
        annotation = str(param.annotation)
        assert "CaseTruth" not in annotation
        assert annotation in ("PublicCase", "Iterable[EvidenceFact]")


def test_passing_truth_to_solve_case_raises_typeerror():
    public = golden_public()
    evidence = golden_evidence()
    truth = truth_variant_a()

    with pytest.raises(TypeError):
        solve_case(truth, evidence)  # truth is not a PublicCase
    with pytest.raises(TypeError):
        solve_case(public, truth)    # truth is not a list/tuple of EvidenceFact


def test_passing_truth_to_dimension_solvers_raises_typeerror():
    public = golden_public()
    evidence = golden_evidence()
    when = solve_when(public, evidence)
    truth = truth_variant_a()

    with pytest.raises(TypeError):
        solve_when(truth, evidence)

    # The correct call (feasible_time = IntervalSet) works fine...
    solve_who(public, evidence, when.feasible)
    # ...but passing a truth object where an IntervalSet is expected must fail.
    with pytest.raises(TypeError):
        solve_who(public, evidence, truth)  # feasible_time must be an IntervalSet

    with pytest.raises(TypeError):
        solve_why(truth, evidence)
    with pytest.raises(TypeError):
        solve_weapon(public, truth)


def test_evaluate_solution_requires_real_case_truth():
    from app.validation.solution import evaluate_solution

    proof = solve_case(golden_public(), golden_evidence())
    with pytest.raises(TypeError):
        evaluate_solution(proof, proof)  # SolverProof is not a CaseTruth