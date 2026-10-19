r"""ADV-48.1 probe 4 - fail-closed branch reachability / plan-source mismatch.

Drives a REAL GenerationController with a monkeypatched ``pipeline.validate_draft``
(controlled report sequence) and a no-op driver ``run_into`` so the
``UNCHANGED`` targeted fail-closed branch is exercised deterministically.
ZERO provider calls.

Run from backend/:
    python ..\screenshots\evidence\phase48a\adv\probe4_failclosed.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="backslashreplace")
_REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO / "backend"))
sys.path.insert(0, str(_REPO / "backend" / "tests"))

from app.generation import pipeline  # noqa: E402
from app.generation.report import ValidationReport  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from test_ollama_driver import (  # noqa: E402
    MockOllamaTransport,
    _admission,
    _controller,
    _make_driver,
)

from app.generation.clock import ManualClock  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402

UNPREFIXED = "draft assembly failed: generated draft cannot become a public case"
PREFIXED_W = "world_requirements.relations[0]: kind 'on_kitchen_counter' not in []"
PREFIXED_W2 = "world_requirements.relations[0]: kind 'on_kitchen_counter' not in []"


def drive(reports, *, max_repair_passes=3, label=""):
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    transport = MockOllamaTransport(posts=[])
    driver = _make_driver(transport)

    repair_plans = []

    def fake_run_into(attempt, diagnostics=(), repair_plan=None):
        repair_plans.append(
            tuple(o.value for o in repair_plan.repair_owners)
            if repair_plan is not None
            else None
        )
        return None

    driver.run_into = fake_run_into  # type: ignore[assignment]
    driver.reset_attempt_cache = lambda _aid: None  # type: ignore[assignment]

    queue = list(reports)
    seen = []

    def fake_validate(attempt, *, event_prefix="generation.validation"):
        if queue:
            rep = queue.pop(0)
        else:
            rep = queue_last
        seen.append(rep)
        attempt.last_validation = rep
        return rep

    queue_last = reports[-1] if reports else ValidationReport()
    pipeline.validate_draft = fake_validate  # type: ignore[assignment]

    controller = _controller(
        driver, transport, admission, clock, ids,
        max_repair_passes=max_repair_passes,
    )
    handle = controller.start_generation(
        "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: x\nWeapon: y\n",
        anonymous_quota_session_id=session.session_id,
    )
    attempt = controller.attempt(handle.attempt_id)
    print(f"--- {label} ---")
    print(f"reports validated: {len(seen)}")
    for i, r in enumerate(seen):
        print(f"  R{i}: structural={r.structural_issues!r}")
    print(f"repair_plans actually executed: {repair_plans}")
    print(f"state={attempt.state.value} failure_code={attempt.failure_code!r}")
    print(f"reason={attempt.reason!r}")
    print(f"repair_passes={attempt.budget.repair_passes}")
    return attempt, repair_plans


print("=== SCENARIO A: executed DEFAULT(UNKNOWN) then current TARGETED, same codes ===")
R_UNK = ValidationReport(structural_issues=(UNPREFIXED,))
R_W1 = ValidationReport(structural_issues=(PREFIXED_W,))
R_W2 = ValidationReport(structural_issues=(PREFIXED_W2,))
drive([R_UNK, R_W1, R_W2, R_W2], label="A")

print()
print("=== SCENARIO B: UNKNOWN unchanged (legacy) - should NOT fail closed early ===")
R_UNK2 = ValidationReport(structural_issues=(UNPREFIXED,))
drive([R_UNK, R_UNK2, R_UNK2, R_UNK2], label="B")

print()
print("=== SCENARIO C: targeted world unchanged - fail closed ===")
drive([R_W1, R_W1, R_W1, R_W1], label="C")
