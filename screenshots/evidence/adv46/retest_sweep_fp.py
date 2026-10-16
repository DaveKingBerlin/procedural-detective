"""Phase 46 retest: false positives, committed-tree scan, and the
pre-redaction value sweep (counts only; NO value is ever printed).

Run from repo root.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
while not (REPO / "tools" / "release_check.py").is_file():
    if REPO.parent == REPO:
        raise SystemExit("repo root not found")
    REPO = REPO.parent
sys.path.insert(0, str(REPO))
from tools import release_check  # noqa: E402

T = "A" * 43

# ---------------------------------------------------------------- false positives
LEGIT = [
    ("var reference setItem", 'localStorage.setItem("pd_playthrough_token", token);'),
    ("var reference bracket", 'const t = sess["pd_playthrough_token"];'),
    ("var reference field", "const t = sess.anonymousSessionToken;"),
    ("short placeholder", '{"creatorAccessToken": "placeholder-only"}'),
    ("built value", 'const built = "A".repeat(43);'),
    ("built value use", 'localStorage.setItem("pd_playthrough_token", built);'),
    ("type decl", "creatorAccessToken: str"),
    ("field in schema", '    "creatorAccessToken": {"type": "string"},'),
    ("getItem", 'localStorage.getItem("pd_playthrough_token")'),
    ("f-string use", 'f"{body[\'playthroughAccessToken\']}"'),
    ("long identifier no value", "def anonymousSessionToken_for(case_id):"),
    ("base64-looking non-token", "some_hash = 'deadbeef' * 8"),
]


def false_positive_check() -> None:
    print("== FALSE-POSITIVE CHECK ==")
    for label, content in LEGIT:
        tmp = Path(tempfile.mkdtemp(prefix="fp-", dir=Path(__file__).parent))
        p = tmp / "src"
        p.mkdir(parents=True, exist_ok=True)
        (p / "x.py").write_text(content, encoding="utf-8")
        findings = release_check.scan_app_bearer_tokens(tmp, ["src/x.py"])
        fails = [f for f in findings if f.severity == "fail"]
        print(("  FAIL!" if fails else "  ok   "), label,
              (" -> " + fails[0].message) if fails else "")
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- committed tree
def committed_tree_check() -> None:
    print("\n== COMMITTED TRACKED-TREE SCAN ==")
    tracked = release_check.git_tracked_files(REPO)
    print("  tracked files:", len(tracked) if tracked else None)
    findings = release_check.scan_app_bearer_tokens(REPO, tracked)
    fails = [f for f in findings if f.severity == "fail"]
    print("  fails:", len(fails))
    for f in fails:
        print("   ", f.render())


# ---------------------------------------------------------------- value sweep
_RUN = re.compile(r"[A-Za-z0-9_\-]{40,}")
_APP_ASSOC = release_check._APP_BEARER_ASSOC_RE

REDACTED_HEAD_FILES = [
    "screenshots/evidence/p45fix-qa/acceptance/p45-easy-acceptance-evidence.json",
    "screenshots/evidence/p45fix-qa/acceptance/p45-medium-acceptance-evidence.json",
    "screenshots/evidence/p45fix-qa/acceptance/p45-hard-acceptance-evidence.json",
    "screenshots/evidence/p45fix-qa/api-failure.txt",
]


def _git_show(rel: str) -> str | None:
    r = subprocess.run(
        ["git", "-C", str(REPO), "show", f"HEAD:{rel}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return r.stdout if r.returncode == 0 else None


def extract_values() -> list[str]:
    vals: list[str] = []
    for rel in REDACTED_HEAD_FILES:
        text = _git_show(rel)
        if not text:
            continue
        # field-name-associated values (the app's own bearer-token fields)
        for m in _APP_ASSOC.finditer(text):
            v = m.group(0)
            mm = _RUN.search(v)
            if mm:
                vals.append(mm.group(0))
        # catch-all for api-failure.txt style dumps
        if rel.endswith(".txt"):
            for m in _RUN.finditer(text):
                vals.append(m.group(0))
    # de-duplicate, keep order
    seen: dict[str, None] = {}
    for v in vals:
        if v not in seen:
            seen[v] = None
    return list(seen)


def sweep() -> None:
    print("\n== PRE-REDACTION VALUE SWEEP (counts only) ==")
    values = extract_values()
    print("  distinct extracted values:", len(values))
    print("  lengths:", sorted({len(v) for v in values}))
    tracked = release_check.git_tracked_files(REPO) or []
    hits_total = 0
    # sanity: the extraction regex must hit the HEAD (pre-redaction) content
    head_hits = sum(
        1 for v in values if any(v in (_git_show(rel) or "")
                                for rel in REDACTED_HEAD_FILES)
    )
    print("  values present in HEAD source files (sanity):", head_hits)
    for idx, v in enumerate(values):
        hits = 0
        for rel in tracked:
            path = (REPO / rel)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if v in text:
                hits += 1
        hits_total += hits
        print(f"  value[{idx}] len={len(v)} tracked-file hits={hits}")
    print("  TOTAL tracked-file hits across all values:", hits_total)


if __name__ == "__main__":
    false_positive_check()
    committed_tree_check()
    sweep()
