"""ADV-49 probe B: ZIP path-traversal member basename bypass + raw binary scan.

1. build_release.py's `_reject_unsafe_member` is applied to `Path(name).name`
   (the BASENAME) for the Windows ZIP, so a member literally named
   '..\\..\\pd-ollama-bridge.exe' passes the traversal check.
2. Independent raw scan of the real artifacts / extracted binaries for
   credential and dev-path markers (strings-level).
"""
from __future__ import annotations

import re
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
sys.path.insert(0, str(ROOT))
from tools import build_release as br  # noqa: E402

print("=" * 70)
print("PART 1 - traversal member basename bypass")
print("=" * 70)

work = Path(tempfile.mkdtemp(prefix="adv49-b-"))
hostile = work / "traversal.zip"
with zipfile.ZipFile(hostile, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("..\\..\\..\\Windows\\Temp\\pd-ollama-bridge.exe", b"MZ evil")
    zf.writestr("QUICKSTART.md", b"qs")
    zf.writestr("LICENSE", b"MIT")
    zf.writestr("THIRD_PARTY_NOTICES.txt", b"n")
print("members:", zipfile.ZipFile(hostile).namelist())

findings: list = []
try:
    br.inspect_windows_zip(hostile, "1.0.0", findings)
    print("RESULT: inspect_windows_zip ACCEPTED the traversal ZIP (exit 0)")
except SystemExit as exc:
    print("RESULT: inspect_windows_zip rejected it (exit", exc.code, ")")

# Prove it can actually escape on naive extraction (zipfile.extractall).
ex = work / "extract"
ex.mkdir()
with zipfile.ZipFile(hostile) as zf:
    zf.extractall(ex)
escape = (ex.parent / "Windows" / "Temp" / "pd-ollama-bridge.exe")
print("naive extractall wrote OUTSIDE target dir:", escape.exists(), "->", escape)

print()
print("=" * 70)
print("PART 2 - raw scan of real released archives / members")
print("=" * 70)

CRED = [
    ("openrouter", re.compile(rb"sk-or-v1-[A-Za-z0-9]{16,}")),
    ("google-key", re.compile(rb"AIza[0-9A-Za-z_\-]{35}")),
    ("win-path", re.compile(rb"C:\\Users\\[A-Za-z0-9._\-]+")),
    ("unix-home", re.compile(rb"(?:/home|/Users)/[A-Za-z0-9._\-]+")),
    ("privkey", re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("auth-bearer", re.compile(rb"Authorization:\s*Bearer\s+\S{8,}", re.I)),
]
MARKERS = [b"OPENROUTER", b"GEMINI", b"API_KEY", b".env"]

def scan(label, data):
    for name, pat in CRED:
        for m in pat.finditer(data):
            print(f"  CRED[{name}] {label} @ {m.start()}")
    for mk in MARKERS:
        if mk in data:
            print(f"  marker[{mk.decode()}] {label}")

dist = ROOT / "dist" / "releases" / "1.0.0"
for fn in ["pd-ollama-bridge-v1.0.0-windows-x64.zip",
           "pd-ollama-bridge-v1.0.0-linux-x64.tar.gz"]:
    p = dist / fn
    raw = p.read_bytes()
    scan(f"raw:{fn}", raw)
    if fn.endswith(".zip"):
        with zipfile.ZipFile(p) as zf:
            for m in zf.infolist():
                scan(f"{fn}!{m.filename}", zf.read(m))
    else:
        import tarfile
        with tarfile.open(p, "r:gz") as tf:
            for m in tf.getmembers():
                if m.isfile():
                    scan(f"{fn}!{m.name}", tf.extractfile(m).read())
print("scan complete")
