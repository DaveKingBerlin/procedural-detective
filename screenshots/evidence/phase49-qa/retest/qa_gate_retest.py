"""Independent QA Phase 49 retest: exercise the REAL release gate
(tools/build_release.py --scan-only) against hostile archives.

For each planted vector we assert (a) non-zero exit and (b) the planted secret
value is NOT present anywhere in stdout/stderr. A clean control must PASS.
Read-only w.r.t. product code; writes only under a temp dir.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import zlib
import zipfile
import tarfile
import io
from pathlib import Path

ROOT = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
GATE = ROOT / "tools" / "build_release.py"
TMP = Path(tempfile.mkdtemp(prefix="qa49-gate-"))

SECRET_OR = "sk-or-v1-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"  # fake OpenRouter
SECRET_BEARER = "Authorization: Bearer sk-live-DEADBEEF12345678"

WIN_MEMBERS = ["pd-ollama-bridge.exe", "QUICKSTART.md", "LICENSE", "THIRD_PARTY_NOTICES.txt"]
LIN_MEMBERS = ["pd-ollama-bridge", "QUICKSTART.md", "LICENSE", "THIRD_PARTY_NOTICES.txt"]


def make_zip(path: Path, members, *, comment=b"", trailing=b"", binary=b"hello world"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for m in members:
            payload = binary if m in ("pd-ollama-bridge.exe", "pd-ollama-bridge") else b"benign doc"
            z.writestr(m, payload)
        if comment:
            z.comment = comment
    data = buf.getvalue()
    if trailing:
        data += trailing
    path.write_bytes(data)


def make_tar(path: Path, members, *, binary: bytes = b"hello world", mode=0o755, extra=None):
    with tarfile.open(path, "w:gz") as t:
        for m in members:
            payload = binary if m in ("pd-ollama-bridge", "pd-ollama-bridge.exe") else b"benign doc"
            info = tarfile.TarInfo(m)
            info.size = len(payload)
            info.mode = mode if m == "pd-ollama-bridge" else 0o644
            t.addfile(info, io.BytesIO(payload))
        if extra is not None:
            extra(t)


def run_gate(args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GATE), *args], capture_output=True, text=True)


results = []


def check(label, proc, *, expect_fail, secret=None):
    out = proc.stdout + proc.stderr
    leaked = secret is not None and secret in out
    ok = (proc.returncode != 0) if expect_fail else (proc.returncode == 0)
    status = "PASS" if (ok and not leaked) else "FAIL"
    results.append((label, status, proc.returncode, leaked))
    print(f"[{status}] {label}: exit={proc.returncode} expect_fail={expect_fail} secret_leaked={leaked}")
    if status == "FAIL":
        print("---- output ----")
        print(out)
        print("----------------")


# 1 control (clean windows zip)
p = TMP / "clean.zip"; make_zip(p, WIN_MEMBERS)
check("control clean windows zip", run_gate(["--scan-only", "--windows-zip", str(p)]), expect_fail=False)

# 2 zip comment
p = TMP / "comment.zip"; make_zip(p, WIN_MEMBERS, comment=SECRET_OR.encode())
check("secret in ZIP comment", run_gate(["--scan-only", "--windows-zip", str(p)]), expect_fail=True, secret=SECRET_OR)

# 3 post-EOCD trailing bytes
p = TMP / "posteocd.zip"; make_zip(p, WIN_MEMBERS, trailing=b"\nOPENROUTER " + SECRET_OR.encode())
check("secret after ZIP EOCD", run_gate(["--scan-only", "--windows-zip", str(p)]), expect_fail=True, secret=SECRET_OR)

# 4 bounded-zlib member (ADV-49-001 class)
p = TMP / "zlibmember.zip"; make_zip(p, WIN_MEMBERS, binary=zlib.compress(SECRET_OR.encode() * 4))
check("zlib-compressed member secret", run_gate(["--scan-only", "--windows-zip", str(p)]), expect_fail=True, secret=SECRET_OR)

# 5 nested member
p = TMP / "nested.zip"; make_zip(p, ["sub/pd-ollama-bridge.exe", "QUICKSTART.md", "LICENSE", "THIRD_PARTY_NOTICES.txt"])
check("nested windows member", run_gate(["--scan-only", "--windows-zip", str(p)]), expect_fail=True)

# 6 traversal member
p = TMP / "traversal.zip"; make_zip(p, ["../pd-ollama-bridge.exe", "QUICKSTART.md", "LICENSE", "THIRD_PARTY_NOTICES.txt"])
check("traversal windows member", run_gate(["--scan-only", "--windows-zip", str(p)]), expect_fail=True)

# 7 extra member
p = TMP / "extra.zip"; make_zip(p, WIN_MEMBERS + ["SECRET.txt"])
check("extra windows member", run_gate(["--scan-only", "--windows-zip", str(p)]), expect_fail=True)

# 8 secret in a doc member
p = TMP / "docsecret.zip"
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("pd-ollama-bridge.exe", b"hello world")
    z.writestr("QUICKSTART.md", SECRET_BEARER)
    z.writestr("LICENSE", b"benign")
    z.writestr("THIRD_PARTY_NOTICES.txt", b"benign")
p.write_bytes(buf.getvalue())
check("bearer in doc member", run_gate(["--scan-only", "--windows-zip", str(p)]), expect_fail=True, secret=SECRET_BEARER)

# 9 clean linux tar
p = TMP / "clean.tar.gz"; make_tar(p, LIN_MEMBERS)
check("control clean linux tar", run_gate(["--scan-only", "--linux-tar", str(p)]), expect_fail=False)

# 10 linux tar traversal
p = TMP / "traversal.tar.gz"; make_tar(p, ["../pd-ollama-bridge", "QUICKSTART.md", "LICENSE", "THIRD_PARTY_NOTICES.txt"])
check("traversal linux member", run_gate(["--scan-only", "--linux-tar", str(p)]), expect_fail=True)

# 11 linux tar symlink
def add_sym(t):
    info = tarfile.TarInfo("evil-link"); info.type = tarfile.SYMTYPE; info.linkname = "/etc/passwd"; t.addfile(info)
p = TMP / "symlink.tar.gz"; make_tar(p, LIN_MEMBERS, extra=add_sym)
check("symlink linux member", run_gate(["--scan-only", "--linux-tar", str(p)]), expect_fail=True)

# 12 linux tar zlib member secret
p = TMP / "zliblin.tar.gz"; make_tar(p, LIN_MEMBERS, binary=zlib.compress(SECRET_OR.encode() * 4))
check("zlib linux member secret", run_gate(["--scan-only", "--linux-tar", str(p)]), expect_fail=True, secret=SECRET_OR)

print()
fails = [r for r in results if r[1] == "FAIL"]
print(f"SUMMARY: {len(results) - len(fails)}/{len(results)} vectors behaved correctly")
if fails:
    print("FAILURES:", fails)
    sys.exit(1)
print("ALL GATE VECTORS PASS")
