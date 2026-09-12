"""Deterministic solvers: WHEN -> WHO / WHY / WEAPON.

None of these modules may import ``app.domain.truth`` (enforced by
``backend/tests/test_boundaries.py``).
"""

from __future__ import annotations

from app.domain.solvers.weapon_solver import solve_weapon
from app.domain.solvers.when_solver import WhenResult, solve_when
from app.domain.solvers.who_solver import solve_who
from app.domain.solvers.why_solver import solve_why

__all__ = [
    "WhenResult",
    "solve_when",
    "solve_who",
    "solve_why",
    "solve_weapon",
]