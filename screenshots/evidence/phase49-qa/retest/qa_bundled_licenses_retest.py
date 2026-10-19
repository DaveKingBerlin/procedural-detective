"""Independent QA Phase 49 retest: enumerate bundled distributions + versions in
each released one-file binary and compare against the shipped notices.

Uses PyInstaller's CArchiveReader to read each embedded *.dist-info/METADATA.
Read-only; no product code touched.
"""
from __future__ import annotations

import io
import re
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

ROOT = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
DIST = ROOT / "dist" / "releases" / "1.0.0"

# Component -> (claimed version windows, claimed version linux) parsed from the
# shipped THIRD_PARTY_NOTICES.txt. Kept here only to print a diff; the notice
# file is also re-read below.
notices = (ROOT / "bridge" / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")

def dist_versions(binary: Path) -> dict[str, str]:
    car = CArchiveReader(str(binary))
    names = list(car.toc)
    out: dict[str, str] = {}
    for n in names:
        norm = n.replace("\\", "/")
        m = re.match(r"^([^/]+)-[\d][^/]*\.dist-info/METADATA$", norm)
        if not m:
            continue
        dist = m.group(1)
        try:
            raw = car.extract(n)
        except Exception:
            continue
        if isinstance(raw, tuple):
            raw = raw[1]
        text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        nm = re.search(r"^Name:\s*(.+)$", text, re.M)
        ver = re.search(r"^Version:\s*(.+)$", text, re.M)
        key = (nm.group(1).strip() if nm else dist)
        out[key] = ver.group(1).strip() if ver else "?"
    return out, names

def pyz_roots(car, names) -> list[str]:
    roots: set[str] = set()
    for n in names:
        if n.lower().endswith(".pyz") or "pyz" in n.lower():
            try:
                r = car.open_embedded_archive(n)
            except Exception:
                continue
            if isinstance(r, ZlibArchiveReader):
                for mod in r.toc:
                    roots.add(mod.split(".")[0])
    return sorted(r for r in roots if not r.startswith("_"))

for label, arc in [("WINDOWS", DIST / "pd-ollama-bridge-v1.0.0-windows-x64.zip"),
                   ("LINUX", DIST / "pd-ollama-bridge-v1.0.0-linux-x64.tar.gz")]:
    print("=" * 72)
    print(f"{label}: {arc.name}")
    print("=" * 72)
    d = Path(tempfile.mkdtemp(prefix="qa49-lic-"))
    if arc.suffix == ".zip":
        with zipfile.ZipFile(arc) as zf:
            zf.extract("pd-ollama-bridge.exe", d)
        binary = d / "pd-ollama-bridge.exe"
    else:
        with tarfile.open(arc, "r:gz") as tf:
            tf.extractall(d)
        binary = d / "pd-ollama-bridge"
    car = CArchiveReader(str(binary))
    versions, names = dist_versions(binary)
    for k in sorted(versions):
        print(f"  dist-info: {k} == {versions[k]}")
    print("  PYZ roots:", ", ".join(pyz_roots(car, names)))
    # native libs of interest
    natives = sorted({n for n in names if re.search(r"libcrypto|libssl|libffi|VCRUNTIME", n, re.I)})
    print("  native libs:", natives)
    print()

print("=== NOTICE CLAIMS (bridge/THIRD_PARTY_NOTICES.txt) ===")
for line in notices.splitlines():
    if "Version bundled" in line or "()" in line and "License" not in line:
        print(" ", line.strip())
