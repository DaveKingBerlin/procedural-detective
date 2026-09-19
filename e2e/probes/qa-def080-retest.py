"""DEF-080 independent QA retest (2026-09-19).

The defect: ``Settings.generation_deadline_seconds`` documented the canonical
:math:\\section-45 env name ``CASE_GENERATION_DEADLINE_SECONDS`` but the
pydantic-settings binding was derived from the field name
(``GENERATION_DEADLINE_SECONDS``), so an operator setting the DOCUMENTED name
silently kept the 60 s default and slow real-model WAN attempts FAILED.

Fix claim (developer backend-dev): the field now carries
``validation_alias="CASE_GENERATION_DEADLINE_SECONDS"`` so the canonical name
is the ONLY binding and ALSO the construction keyword.

This probe retests EXACTLY the four acceptance lines from the retest task:
  1. env ``CASE_GENERATION_DEADLINE_SECONDS=123``  -> effective 123
  2. env ``GENERATION_DEADLINE_SECONDS=999``      -> ignored (stays 60)
  3. no env at all                                -> default 60
  4. the OPERATIONALLY-SIGNIFICANT fix: env
     ``CASE_GENERATION_DEADLINE_SECONDS=900`` MUST actually raise the deadline
     (a slow WAN real-model attempt must not silently FAIL at 60 s).

Each scenario runs in a FRESH subprocess so pydantic-settings resolves real
env vars (hermetic: ENV_FILE pointed at os.devnull like the backend suite
conftest, so the operator dotenv can never leak in).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

SCENARIOS = [
    # (label, extra_env, expected)
    ("default", {}, 60),
    ("canonical-env-123", {"CASE_GENERATION_DEADLINE_SECONDS": "123"}, 123),
    ("old-env-999-ignored", {"GENERATION_DEADLINE_SECONDS": "999"}, 60),
    ("old-999-plus-canonical-123", {"GENERATION_DEADLINE_SECONDS": "999", "CASE_GENERATION_DEADLINE_SECONDS": "123"}, 123),
    ("canonical-900", {"CASE_GENERATION_DEADLINE_SECONDS": "900"}, 900),
    ("canonical-0-rejected-by-gt0", {"CASE_GENERATION_DEADLINE_SECONDS": "0"}, None),  # gt=0 rejects at Settings
]

PROBE = """
import os
import sys
sys.path.insert(0, os.environ.pop("QA_BACKEND_DIR"))
from app.core.config import Settings
s = Settings()
sys.stdout.write(str(s.generation_deadline_seconds))
"""


def run_scenario(label: str, extra_env: dict, expected) -> bool:
    env = os.environ.copy()
    env["ENV_FILE"] = os.devnull
    for k in ("GENERATION_PROVIDER", "OLLAMA_BASE_URL", "OLLAMA_MODEL", "OLLAMA_TEMPERATURE",
              "OLLAMA_NUM_CTX", "OLLAMA_TIMEOUT_SECONDS", "CASE_GENERATION_DEADLINE_SECONDS",
              "GENERATION_DEADLINE_SECONDS", "LIVE_PROVIDER_URL", "LLM_API_KEY", "LLM_MODEL",
              "FAKE_PROVIDER_SCRIPT"):
        if k not in extra_env:
            env.pop(k, None)
    env.update(extra_env)
    env["QA_BACKEND_DIR"] = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
    try:
        proc = subprocess.run(
            [sys.executable, "-c", PROBE],
            capture_output=True, text=True, env=env, timeout=60,
        )
    except subprocess.TimeoutExpired:
        print(f"FAIL {label}: timeout"); return False
    if expected is None:
        # Rejection scenario: Settings() must FAIL (ValidationError) — e.g. 0
        # violates gt=0. assert the process exits non-zero with a clean error.
        ok = proc.returncode != 0 and "ValidationError" in proc.stderr
        print(f"{'PASS' if ok else 'FAIL'} {label}: rejected={ok} exit={proc.returncode}")
        return ok
    if proc.returncode != 0:
        print(f"FAIL {label}: exit {proc.returncode}: {proc.stderr.strip()[:200]}"); return False
    try:
        got = int(proc.stdout.strip())
    except ValueError:
        print(f"FAIL {label}: non-int stdout {proc.stdout!r}: {proc.stderr.strip()[:200]}"); return False
    ok = got == expected
    print(f"{'PASS' if ok else 'FAIL'} {label}: got={got} expected={expected}")
    return ok


def main() -> int:
    results = [run_scenario(label, extra, expected) for label, extra, expected in SCENARIOS]
    total = len(results)
    passed = sum(results)
    print(f"def080: {passed}/{total} PASS")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())