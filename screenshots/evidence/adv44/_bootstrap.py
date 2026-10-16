"""Shared bootstrap for Phase 44 adversarial probes (hermetic, zero network)."""
import sys
from pathlib import Path

REPO = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
BACKEND = REPO / "backend"
TESTS = BACKEND / "tests"
for p in (str(BACKEND), str(TESTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from dataclasses import replace  # noqa: E402
from app.generation import playability, pipeline, difficulty_policy  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.schemas import (  # noqa: E402
    GeneratedDraft,
    WorldGraphSpec,
    PlacementSpec,
    ObjectSpec,
    EvidenceSpec,
    PropSpec,
    EvidenceSetSpec,
    PublicWorldSpec,
)
from app.domain.eligibility import derive_universes  # noqa: E402

# Reuse the phase44 test builders (pure, no side effects on import)
import test_phase44_playability_gate as t44  # noqa: E402
import test_phase44_playability_pipeline as t44p  # noqa: E402


def evaluate(draft, difficulty):
    return t44.evaluate(draft, difficulty)


def show(label, report):
    print(f"[{label}] valid={report.valid} insp={report.inspectable_count} "
          f"mean={report.meaningful_inspectable_count} "
          f"src={report.distinct_meaningful_source_count} "
          f"rh={report.red_herring_count} "
          f"reach={report.reachable_critical_dimensions} "
          f"unreach={report.unreachable_critical_dimensions} "
          f"issues={report.issue_codes}")
