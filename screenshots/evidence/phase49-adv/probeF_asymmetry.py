"""ADV-49 probe F: asymmetric archive-member validation in build_release.py.

- Linux tar traversal / symlink -> must fail (uses full member.name).
- Windows ZIP nested member 'sub/pd-ollama-bridge.exe' -> basename satisfies the
  required set even though the exe is NOT at the archive root (basename check).
- Zip-slip member accepted (probe B) reconfirmed here for completeness.
"""
from __future__ import annotations

import io
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
sys.path.insert(0, str(ROOT))
from tools import build_release as br  # noqa: E402

work = Path(tempfile.mkdtemp(prefix="adv49-f-"))


def make_tar(name: str, members: list[tuple[str, bytes, int, int]]) -> Path:
    p = work / name
    with tarfile.open(p, "w:gz") as tf:
        for mname, data, mode, ttype in members:
            info = tarfile.TarInfo(mname)
            info.size = len(data)
            info.mode = mode
            info.type = ttype
            tf.addfile(info, io.BytesIO(data))
    return p


print("== Linux tar traversal member ==")
trav = make_tar(
    "trav.tar.gz",
    [
        ("../etc/pd-ollama-bridge", b"x", 0o755, tarfile.REGTYPE),
        ("QUICKSTART.md", b"x", 0o644, tarfile.REGTYPE),
        ("LICENSE", b"x", 0o644, tarfile.REGTYPE),
        ("THIRD_PARTY_NOTICES.txt", b"x", 0o644, tarfile.REGTYPE),
    ],
)
try:
    br.inspect_linux_tar(trav, "1.0.0", [])
    print("RESULT: ACCEPTED traversal tar (BUG)")
except SystemExit as e:
    print("RESULT: rejected traversal tar (exit", e.code, ")")

print("\n== Linux tar symlink member ==")
sym = make_tar(
    "sym.tar.gz",
    [
        ("pd-ollama-bridge", b"x", 0o755, tarfile.REGTYPE),
        ("QUICKSTART.md", b"x", 0o644, tarfile.REGTYPE),
        ("LICENSE", b"x", 0o644, tarfile.REGTYPE),
        ("THIRD_PARTY_NOTICES.txt", b"x", 0o644, tarfile.REGTYPE),
        ("evil-link", b"x", 0o777, tarfile.SYMTYPE),
    ],
)
try:
    br.inspect_linux_tar(sym, "1.0.0", [])
    print("RESULT: ACCEPTED symlink tar (BUG)")
except SystemExit as e:
    print("RESULT: rejected symlink tar (exit", e.code, ")")

print("\n== Windows ZIP nested member satisfies required set ==")
nested = work / "nested.zip"
with zipfile.ZipFile(nested, "w") as zf:
    zf.writestr("sub/pd-ollama-bridge.exe", b"MZ")
    zf.writestr("QUICKSTART.md", b"x")
    zf.writestr("LICENSE", b"x")
    zf.writestr("THIRD_PARTY_NOTICES.txt", b"x")
print("members:", zipfile.ZipFile(nested).namelist())
try:
    br.inspect_windows_zip(nested, "1.0.0", [])
    print("RESULT: ACCEPTED a ZIP whose EXE is nested under sub/ (exit 0)")
except SystemExit as e:
    print("RESULT: rejected nested ZIP (exit", e.code, ")")

print("\n== manifest copies byte-identical? ==")
a = (ROOT / "dist" / "releases" / "1.0.0" / "release-manifest.json").read_bytes()
b = (ROOT / "frontend" / "public" / "releases" / "release-manifest.json").read_bytes()
print("dist == frontend/public:", a == b)
print("frontend manifest dirty flag:", __import__("json").loads(b).get("dirty"))
