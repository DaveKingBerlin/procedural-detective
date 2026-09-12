"""Phase 3 — Truth, Evidence, Time, and Deterministic Deduction domain.

Module layout (also enforced by ``backend/tests/test_boundaries.py``):

- ``truth``          — server/internal-only CaseTruth value objects. NO solver
                       module may import it.
- ``public``         — purely public (solver-visible) case model.
- ``eligibility``    — candidate universes derived ONLY from public predicates.
- ``evidence``       — typed structured facts shared by solver and presentation.
- ``rules``          — three-state proposition status, rule effects, outcomes.
- ``time_interval``  — exact half-open integer-tick interval algebra + ISO parser.
- ``proof``          — deterministic machine-readable result model.
- ``solvers``        — WHEN / WHO / WHY / WEAPON deterministic solvers.
- ``solver``         — ``solve_case`` orchestration entry point.

CaseTruth may be used ONLY by ``app.validation.solution`` (truth-aware
comparison/scoring stage) and by truth-independence tests.
"""

from __future__ import annotations