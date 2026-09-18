"""QA Phase 15 — independent demo-mode honesty probe (durable, e2e/probes/).

Same-kit prompt pairs -> BYTE-DIFFERENT published world graphs with the FROZEN
golden truth byte-equal. Runs the REAL GenerationService over a scratch SQLite
(the same in-process path as the matrix harness / backend tests).

Usage:  python e2e/probes/qa-phase15-demo-honesty.py
Exit 0 = HONESTY_OK (10 distinct worlds, per-kit byte-distinct pairs, frozen
truth identical across all rows). Exit 1 = any assertion failed.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_BACKEND = _REPO / "backend"
_TESTS = _BACKEND / "tests"
for _path in (_BACKEND, _TESTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.persistence.store import Store  # noqa: E402
from app.persistence.timebase import EpochClock  # noqa: E402
from app.services.generation import GenerationService  # noqa: E402
from conftest import upgrade_db  # noqa: E402
from fixtures.world_matrix import MATRIX_EXPECTED, MATRIX_ORDER, MATRIX_PROMPTS  # noqa: E402
from phase5_helpers import _phase5_settings_for, seed_session  # noqa: E402


def _diff_only_caseid_title(a: dict, b: dict) -> bool:
    """True when the two truth blocks differ ONLY in case_id / title."""
    diffs = []
    for key in sorted(set(a) | set(b)):
        if key not in a or key not in b:
            return False
        if key not in ("case_id", "title") and a[key] != b[key]:
            return False
    return True


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="qa_p15_honesty_"))
    url = f"sqlite:///{(tmp / 'h.db').as_posix()}"
    upgrade_db(url)
    store = Store(url)
    service = GenerationService(settings=_phase5_settings_for(url), store=store)
    clock = EpochClock()

    payloads = {}
    try:
        for index, row_id in enumerate(MATRIX_ORDER):
            session_id = f"HON-{index}"
            seed_session(store, session_id, clock)
            started = service.start_case_generation(
                MATRIX_PROMPTS[row_id], anonymous_quota_session_id=session_id
            )
            assert started.status == "PUBLISHED", (row_id, started.status)
            payloads[row_id] = json.loads(
                store.get_published(started.case_id, 1).payload_json
            )
    finally:
        store.dispose()

    def world_sha(payload):
        draft = payload["draft"]
        placements = tuple(
            sorted(
                (
                    pl.get("object_id"),
                    pl.get("asset_id"),
                    pl.get("anchor"),
                    pl.get("interaction"),
                    pl.get("evidence_id"),
                    (pl.get("generated_definition") or {}).get("assetId"),
                )
                for pl in draft["world_graph"]["placements"]
            )
        )
        material = {"env": draft["scene"].get("environment_id"), "placements": placements}
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()

    all_ten_distinct = len({world_sha(p) for p in payloads.values()}) == 10
    per_kit = {}
    for kit in ("apartment", "office", "hotel_suite", "warehouse", "mansion"):
        rows = [r for r in MATRIX_ORDER if MATRIX_EXPECTED[r].kit == kit]
        a, b = rows[0], rows[1]
        sa, sb = world_sha(payloads[a]), world_sha(payloads[b])
        truth_a_crime = json.dumps(payloads[a]["truth"]["crime"], sort_keys=True)
        truth_b_crime = json.dumps(payloads[b]["truth"]["crime"], sort_keys=True)
        # The FROZEN GOLDEN TRUTH contract (backend tests assert truth.crime
        # byte-equality): the whole `truth` block legitimately differs by the
        # per-case CASE_ID nonce and the prompt-echo `title`; the crime itself
        # (murderer/victim/motive/weapon/time) is the frozen truth and must be
        # byte-identical across rows.
        per_kit[kit] = {
            "rows": [a, b],
            "hash_a": sa,
            "hash_b": sb,
            "worldGraphsByteDistinct": sa != sb,
            "envIds": [
                payloads[a]["draft"]["scene"].get("environment_id"),
                payloads[b]["draft"]["scene"].get("environment_id"),
            ],
            "frozenTruthCrimeByteEqual": truth_a_crime == truth_b_crime,
            "truthCrimeIdentical": payloads[a]["truth"]["crime"] == payloads[b]["truth"]["crime"],
            "fullTruthDiffersOnlyByCaseIdAndTitle": _diff_only_caseid_title(
                payloads[a]["truth"], payloads[b]["truth"]
            ),
        }

    print(json.dumps({"tenWorldGraphsByteDistinct": all_ten_distinct, "perKit": per_kit}, indent=1))
    ok = all_ten_distinct and all(
        v["worldGraphsByteDistinct"] and v["frozenTruthCrimeByteEqual"]
        and v["fullTruthDiffersOnlyByCaseIdAndTitle"]
        for v in per_kit.values()
    )
    print("HONESTY_OK" if ok else "HONESTY_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())