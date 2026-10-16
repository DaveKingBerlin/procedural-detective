"""Phase 46 retest: would the strengthened scan catch the EXACT pre-redaction
leak shapes? Writes HEAD (pre-redaction) content to an isolated temp tree under
the real (non-sanctioned) evidence path and runs scan_app_bearer_tokens.
Prints only CAUGHT/EVADED + field names; never a value.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
while not (REPO / "tools" / "release_check.py").is_file():
    if REPO.parent == REPO:
        raise SystemExit("repo root not found")
    REPO = REPO.parent
sys.path.insert(0, str(REPO))
from tools import release_check  # noqa: E402

TMP = Path(r"C:\Users\Fujitsu\AppData\Local\Temp\opencode\pd46adv2\leaktest")

HEAD_FILES = [
    "screenshots/evidence/p45fix-qa/acceptance/p45-easy-acceptance-evidence.json",
    "screenshots/evidence/p45fix-qa/acceptance/p45-medium-acceptance-evidence.json",
    "screenshots/evidence/p45fix-qa/acceptance/p45-hard-acceptance-evidence.json",
    "screenshots/evidence/p45fix-qa/api-failure.txt",
]


def head(rel: str) -> str:
    r = subprocess.run(["git", "-C", str(REPO), "show", f"HEAD:{rel}"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return r.stdout


def main() -> None:
    shutil.rmtree(TMP, ignore_errors=True)
    tracked: list[str] = []
    for rel in HEAD_FILES:
        text = head(rel)
        p = TMP / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        tracked.append(rel)
    findings = release_check.scan_app_bearer_tokens(TMP, tracked)
    fails = [f for f in findings if f.severity == "fail"]
    print("pre-redaction evidence files:", len(tracked))
    print("scan FAILS (must be > 0):", len(fails))
    for f in fails:
        # message contains file:line + field name only (no value)
        print("  ", f.message)
    print("ok-finding present:", any(f.severity == "ok" for f in findings))
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
