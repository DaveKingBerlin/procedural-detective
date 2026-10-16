"""Phase 46 retest: provenance of the extracted pre-redaction strings.

Prints only masked context (never a value): length + sha256 prefix + the
surrounding field/key. Run from repo root.
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

REDACTED_HEAD_FILES = [
    "screenshots/evidence/p45fix-qa/acceptance/p45-easy-acceptance-evidence.json",
    "screenshots/evidence/p45fix-qa/acceptance/p45-medium-acceptance-evidence.json",
    "screenshots/evidence/p45fix-qa/acceptance/p45-hard-acceptance-evidence.json",
    "screenshots/evidence/p45fix-qa/api-failure.txt",
]


def _git_show(rel: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(REPO), "show", f"HEAD:{rel}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return r.stdout if r.returncode == 0 else ""


def fp(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()[:10]


def main() -> None:
    for rel in REDACTED_HEAD_FILES:
        text = _git_show(rel)
        print("### HEAD source:", rel, "bytes=", len(text))
        assoc = sorted({m.group(0)[-99:] for m in _ASSOC.finditer(text)})
        # association-matched raw substrings, but print only masked descriptors
        for m in _ASSOC.finditer(text):
            v = _RUN.search(m.group(0))
            if not v:
                continue
            val = v.group(0)
            print("  assoc field:", m.group("field"), "| value len",
                  len(val), "fp", fp(val))
        if rel.endswith(".txt"):
            print("  -- catch-all runs in .txt --")
            for m in _RUN.finditer(text):
                val = m.group(0)
                s = max(0, m.start() - 25)
                ctx = text[s:m.start()].replace("\n", "\\n")[-25:]
                print("   run len", len(val), "fp", fp(val),
                      "| preceding ctx:", repr(ctx))

    # Now identify which tracked files hold the 6/10/14-hit values.
    # Rebuild the same ordered value list as the sweep probe.
    vals: list[str] = []
    for rel in REDACTED_HEAD_FILES:
        text = _git_show(rel)
        for m in _ASSOC.finditer(text):
            mm = _RUN.search(m.group(0))
            if mm:
                vals.append(mm.group(0))
        if rel.endswith(".txt"):
            for m in _RUN.finditer(text):
                vals.append(m.group(0))
    seen: dict[str, None] = {}
    for v in vals:
        seen.setdefault(v, None)
    vals = list(seen)

    tracked = release_check.git_tracked_files(REPO) or []
    print("\n### tracked-file hits per value (masked)")
    for idx, v in enumerate(vals):
        files = []
        for rel in tracked:
            try:
                text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if v in text:
                files.append(rel)
        if files:
            print(f"  value[{idx}] len={len(v)} fp={fp(v)} hits={len(files)}")
            for f in files[:8]:
                print("     ", f)


if __name__ == "__main__":
    main()
