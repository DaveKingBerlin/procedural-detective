"""Truth-aware validation/comparison stage (server/internal only).

The deduction solvers must never see ``CaseTruth``. Only this package may
import ``app.domain.truth`` — for the final result-vs-truth comparison and the
accepted-scoring computation (§10.6/31.7/31.10-31.12), which happen strictly
AFTER independent deduction.
"""

from __future__ import annotations