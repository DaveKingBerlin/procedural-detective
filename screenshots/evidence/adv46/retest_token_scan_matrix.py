"""Phase 46 ADV-46-01/02 retest: token-scan evasion matrix (hermetic).

Synthetic token only (never a real value, never printed). For each shape we
report CAUGHT / EVADED.  Run from repo root.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
while not (REPO / "tools" / "release_check.py").is_file():
    if REPO.parent == REPO:
        raise SystemExit("repo root not found")
    REPO = REPO.parent
sys.path.insert(0, str(REPO))
from tools import release_check  # noqa: E402

T = "A" * 43                       # synthetic 43-char urlsafe token (runtime only)
U = "".join("\\u%04x" % ord(c) for c in T)   # \u-escaped form of T
S = T[:20] + "\n" + T[20:]         # T split across two physical lines


def shapes() -> list[tuple[str, str]]:
    return [
        ("01 canonical compact JSON", '{"creatorAccessToken":"' + T + '"}'),
        ("02 pretty JSON value on NEXT line",
         '"creatorAccessToken":\n    "' + T + '",'),
        ("03 re-serialized indent=2 JSON",
         '{\n  "creatorAccessToken": "' + T + '"\n}'),
        ("04 unquoted colon", "creatorAccessToken: " + T),
        ("05 unquoted equals", "creatorAccessToken=" + T),
        ("06 lowercase key same line",
         '{"creatoraccesstoken":"' + T + '"}'),
        ("07 UPPERCASE key",
         '{"CREATORACCESSTOKEN":"' + T + '"}'),
        ("08 playthroughAccessToken",
         '{"playthroughAccessToken":"' + T + '"}'),
        ("09 anonymousSessionToken",
         '{"anonymousSessionToken":"' + T + '"}'),
        ("10 localStorage setItem",
         'localStorage.setItem("pd_playthrough_token", "' + T + '")'),
        ("11 bracket access double quotes",
         'obj["pd_playthrough_token"] = "' + T + '"'),
        ("12 bracket access single quotes",
         "obj['pd_playthrough_token'] = '" + T + "'"),
        ("13 nested keys",
         '{"outer":{"inner":{"creatorAccessToken":"' + T + '"}}}'),
        ("14 Bearer prefix inside quoted value",
         '{"creatorAccessToken":"Bearer ' + T + '"}'),
        ("15 Bearer prefix w/ space after Bearer",
         '{"creatorAccessToken":"Bearer  ' + T + '"}'),
        ("16 \\u-escaped value", '{"creatorAccessToken":"' + U + '"}'),
        ("17 value split across two lines",
         '"creatorAccessToken": "' + S + '"'),
        ("18 leading space inside quoted value",
         '{"creatorAccessToken": " ' + T + '"}'),
        ("19 space-separated after colon only",
         '"creatorAccessToken":' + T),
        ("20 mixed-case field w/ newline",
         '"CrEaToRaCcEsStOkEn":\n"' + T + '"'),
        ("21 comma separator (setItem comma)",
         'setItem(pd_playthrough_token, "' + T + '")'),
        ("22 arrow separator",
         'creatorAccessToken => "' + T + '"'),
        ("23 double-quoted key bracket",
         'value["creatorAccessToken"]="' + T + '"'),
        ("24 no separator: whitespace only",
         '"creatorAccessToken" "' + T + '"'),
        ("25 token as YAML block scalar next line",
         "creatorAccessToken: >\n  " + T),
    ]


def run() -> None:
    caught = []
    evaded = []
    for label, content in shapes():
        tmp = Path(tempfile.mkdtemp(prefix="tok-", dir=Path(__file__).parent))
        p = tmp / "evidence"
        p.mkdir(parents=True, exist_ok=True)
        (p / "ev.txt").write_text(content, encoding="utf-8")
        findings = release_check.scan_app_bearer_tokens(tmp, ["evidence/ev.txt"])
        fails = [f for f in findings if f.severity == "fail"]
        for f in findings:
            assert T not in f.message, "VALUE LEAK in finding message!"
        if fails:
            caught.append(label)
        else:
            evaded.append(label)
        shutil.rmtree(tmp, ignore_errors=True)

    print("TOTAL", len(shapes()))
    print("CAUGHT", len(caught))
    for label in caught:
        print("  +", label)
    print("EVADED", len(evaded))
    for label in evaded:
        print("  -", label)


if __name__ == "__main__":
    run()
