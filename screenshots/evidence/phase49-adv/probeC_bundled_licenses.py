"""ADV-49 probe C: enumerate bundled third-party packages in the released
one-file binaries vs bridge/THIRD_PARTY_NOTICES.txt (licensing completeness).

Uses PyInstaller's own CArchiveReader/ZlibArchiveReader (already installed on
this Dev-Box) to list *.dist-info packages and top-level PYZ module roots.
No product code modified.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

ROOT = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
sys.path.insert(0, str(ROOT))

# The notices claim to bundle exactly these distributions.
CLAIMED = {"websockets", "httpx", "httpcore", "certifi", "pyinstaller"}
IMPLICIT = {"python", "cpython"}  # CPython is claimed separately

for exe in [
    ROOT / "dist" / "releases" / "1.0.0" / "pd-ollama-bridge-v1.0.0-windows-x64.zip",
    ROOT / "dist" / "releases" / "1.0.0" / "pd-ollama-bridge-v1.0.0-linux-x64.tar.gz",
]:
    print("=" * 72)
    print(exe.name)
    print("=" * 72)
    if exe.suffix == ".zip":
        import tempfile, zipfile
        d = Path(tempfile.mkdtemp(prefix="adv49-c-"))
        with zipfile.ZipFile(exe) as zf:
            zf.extract("pd-ollama-bridge.exe", d)
        binary = d / "pd-ollama-bridge.exe"
    else:
        import tarfile, tempfile
        d = Path(tempfile.mkdtemp(prefix="adv49-c-"))
        with tarfile.open(exe, "r:gz") as tf:
            tf.extractall(d)
        binary = d / "pd-ollama-bridge"

    car = CArchiveReader(str(binary))
    names = list(car.toc)
    dists = sorted({n.split("\\")[0].split("/")[0]
                    for n in names if ".dist-info" in n})
    dists = sorted({p.replace(".dist-info", "") for n in names
                    for p in [n.replace("\\", "/").split("/")[0]]
                    if ".dist-info" in n})
    print("dist-info packages bundled:", dists)

    mod_roots = set()
    for name in names:
        low = name.lower()
        if low.endswith(".pyz") or "pyz" in low:
            reader = car.open_embedded_archive(name)
            if isinstance(reader, ZlibArchiveReader):
                for mod in reader.toc:
                    mod_roots.add(mod.split(".")[0])
    roots = sorted(r for r in mod_roots if not r.startswith("_"))
    print("PYZ top-level module roots:", roots)
    print()
    missing = sorted(set(dists) - CLAIMED - IMPLICIT)
    print("dist-info NOT named in THIRD_PARTY_NOTICES:", missing)
