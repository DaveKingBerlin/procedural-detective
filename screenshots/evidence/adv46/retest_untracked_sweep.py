"""Phase 46 retest: sweep TRACKED + UNTRACKED worktree files for the 7
pre-redaction app bearer-token values (counts only; no value printed).

Only the 7 ASSOCIATION-matched (real app field) values are used — the
validation-code noise is excluded. Run from repo root.
"""
from __future__ import annotations

import hashlib
import re
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

_RUN = re.compile(r"[A-Za-z0-9_\-]{40,}")
_ASSOC = release_check._APP_BEARER_ASSOC_RE

HEAD_FILES = [
    "screenshots/evidence/p45fix-qa/acceptance/p45-easy-acceptance-evidence.json",
    "screenshots/evidence/p45fix-qa/acceptance/p45-medium-acceptance-evidence.json",
    "screenshots/evidence/p45fix-qa/acceptance/p45-hard-acceptance-evidence.json",
    "screenshots/evidence/p45fix-qa/api-failure.txt",
]


def git(*args: str) -> str:
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.stdout


def head(rel: str) -> str:
    return git("show", f"HEAD:{rel}")


def extract() -> tuple[list[str], list[str]]:
    real, noise = [], []
    for rel in HEAD_FILES:
        text = head(rel)
        for m in _ASSOC.finditer(text):
            mm = _RUN.search(m.group(0))
            if mm:
                real.append(mm.group(0))
    seen: dict[str, None] = {}
    for v in real:
        seen.setdefault(v, None)
    return list(seen), noise


def fp(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()[:10]


def main() -> None:
    values, _ = extract()
    print("real app bearer-token values extracted:", len(values))
    print("lengths:", sorted({len(v) for v in values}))

    tracked = [p for p in git("ls-files").splitlines() if p.strip()]
    untracked = [p for p in git("ls-files", "--others",
                                "--exclude-standard").splitlines() if p.strip()]
    print("tracked files:", len(tracked), "| untracked files:", len(untracked))

    def hits(rel: str, v: str) -> bool:
        try:
            return v in (REPO / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False

    total_tr = total_un = 0
    for idx, v in enumerate(values):
        tr = [r for r in tracked if hits(r, v)]
        un = [r for r in untracked if hits(r, v)]
        total_tr += len(tr)
        total_un += len(un)
        print(f"  value[{idx}] len={len(v)} fp={fp(v)} "
              f"tracked_hits={len(tr)} untracked_hits={len(un)}")
        for r in (tr + un)[:5]:
            print("       hit:", r)
    print("TOTAL tracked hits:", total_tr)
    print("TOTAL untracked hits:", total_un)

    # explicit: the new guard test must not carry any of the 7 values
    guard = REPO / "tests" / "test_phase46_release_guards.py"
    text = guard.read_text(encoding="utf-8")
    print("guard test contains any real value:",
          any(v in text for v in values))
    # and no 40+ run in the guard test source outside sanctioned synthetic?
    runs = _RUN.findall(text)
    print("guard test 40+char urlsafe runs:", len(runs))


if __name__ == "__main__":
    main()
