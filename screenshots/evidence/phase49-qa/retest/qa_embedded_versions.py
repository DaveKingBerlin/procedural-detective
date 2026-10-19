"""Independent QA Phase 49 retest: recover the exact bundled versions of the
packages that do NOT ship a *.dist-info directory, by unmarshalling the embedded
PYZ module code objects and walking co_consts for version-like strings.
"""
from __future__ import annotations

import marshal
import re
import tarfile
import tempfile
import zipfile
from pathlib import Path
from types import CodeType

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

ROOT = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
DIST = ROOT / "dist" / "releases" / "1.0.0"
TARGETS = ["certifi", "httpx", "httpcore", "idna", "h11", "anyio", "typing_extensions", "websockets", "click", "pygments", "packaging"]
VER = re.compile(r"^\d+(\.\d+){1,3}([.\-+][0-9A-Za-z.]+)?$")


def walk_consts(code, depth=0):
    yield code.co_consts
    for c in code.co_consts:
        if isinstance(c, CodeType):
            yield from walk_consts(c, depth + 1)


def find_versions(code):
    found = set()
    for consts in walk_consts(code):
        for c in consts:
            if isinstance(c, str) and VER.match(c.strip()):
                found.add(c.strip())
    return found


def find_version_tuples(code):
    """Detect ``(0, 16, 0)``-style tuples (plus optional str/extra) as versions."""
    found = set()
    for consts in walk_consts(code):
        for c in consts:
            if (isinstance(c, tuple) and 2 <= len(c) <= 5
                    and all(isinstance(x, int) and 0 <= x < 1000 for x in c)
                    and any(isinstance(x, int) and x > 0 for x in c)):
                # require at least major.minor.patch-ish shape
                if len(c) >= 3:
                    found.add(tuple(c))
    return found


def _as_code(obj):
    if isinstance(obj, CodeType):
        return obj
    if isinstance(obj, (bytes, bytearray)):
        try:
            return marshal.loads(bytes(obj))
        except Exception:
            return None
    return None


for label, arc in [("WINDOWS", DIST / "pd-ollama-bridge-v1.0.0-windows-x64.zip"),
                   ("LINUX", DIST / "pd-ollama-bridge-v1.0.0-linux-x64.tar.gz")]:
    print("=" * 72)
    print(f"{label}: {arc.name}")
    print("=" * 72)
    d = Path(tempfile.mkdtemp(prefix="qa49-ver-"))
    if arc.suffix == ".zip":
        with zipfile.ZipFile(arc) as zf:
            zf.extract("pd-ollama-bridge.exe", d)
        binary = d / "pd-ollama-bridge.exe"
    else:
        with tarfile.open(arc, "r:gz") as tf:
            tf.extractall(d)
        binary = d / "pd-ollama-bridge"
    car = CArchiveReader(str(binary))
    pyz = [n for n in car.toc if n.lower().endswith(".pyz") or "pyz" in n.lower()]
    reader = car.open_embedded_archive(pyz[0])
    for pkg in TARGETS:
        entries = [k for k in reader.toc if k == pkg or k.startswith(pkg + ".")]
        if not entries:
            print(f"  {pkg}: NOT in PYZ")
            continue
        vers = set()
        tuples = set()
        for entry in entries:
            code = _as_code(reader.extract(entry))
            if code is None:
                continue
            vers |= find_versions(code)
            tuples |= find_version_tuples(code)
        tup = sorted(".".join(str(x) for x in t) for t in tuples)
        print(f"  {pkg}: string-versions={sorted(vers)} tuple-versions={tup}")
    print()
