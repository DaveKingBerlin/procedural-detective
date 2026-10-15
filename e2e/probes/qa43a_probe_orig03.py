"""QA probe (Phase43A governance): does the ORIGINAL malformed-EVIDENCE
scenario still pass on the Phase43A working tree? Mirrors pre-change test_03."""
import os
import sys
from pathlib import Path

REPO = Path(os.environ.get("QA43A_REPO", str(Path(__file__).resolve().parents[2])))
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import test_ollama_provider as t  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
import app.generation.pipeline as P  # noqa: E402

_orig_churn = P._repair_id_churn_issues


def _churn(attempt, new_draft):
    pre = getattr(attempt, "_repair_pre_ids", None)
    new = P.current_declaration_ids(new_draft)
    print("  [churn] pre.evidence =", sorted((pre or {}).get("evidence", ())))
    print("  [churn] new.evidence =", sorted(new.get("evidence", ())))
    print("  [churn] authorized   =", getattr(attempt, "_repair_authorized_sections", None))
    return _orig_churn(attempt, new_draft)


P._repair_id_churn_issues = _churn

print("=== ORIGINAL pre-change shape: malformed EVIDENCE ===")
transport = t.MockOllamaTransport(
    posts=t._golden_posts(malformed_stage=GenerationStage.EVIDENCE)
)
record = t._run_generation(t._provider(transport))
print("state:", record.state)
print("published:", record.published is not None)
print("repair_passes:", record.budget.repair_passes)
print("regenerations:", record.budget.regenerations)
print("call_count:", transport.call_count)
print("stages:", [transport.stage_of_call(i) for i in range(transport.call_count)])
if record.last_validation is not None:
    print("outcome:", record.last_validation.outcome)
    print("valid:", record.last_validation.valid)
    print("structural_issues:", record.last_validation.structural_issues)
    print("quality_issues:", record.last_validation.quality_issues)
print("reason:", record.reason)

print()
print("=== NEW shape: malformed WORLD_GRAPH ===")
transport2 = t.MockOllamaTransport(
    posts=t._golden_posts(malformed_stage=GenerationStage.WORLD_GRAPH)
)
record2 = t._run_generation(t._provider(transport2))
print("state:", record2.state)
print("repair_passes:", record2.budget.repair_passes)
print("call_count:", transport2.call_count)
