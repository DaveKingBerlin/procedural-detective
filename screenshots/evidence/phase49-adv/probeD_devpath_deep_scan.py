"""ADV-49 probe D: developer path / username leakage inside the compressed
PyInstaller payload (which the flat build_release.py scan cannot see).

Scans every decompressed PYZ module of both released binaries for the build
box's absolute path / username and generic developer-path markers. Never prints
matched values.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

ROOT = Path(r"C:\Users\Fujitsu\coding\procedural-detective")

PATTERNS = [
    ("C:\\Users\\ literal", b"C:\\Users"),
    ("Fujitsu username", b"Fujitsu"),
    ("coding\\procedural literal", b"coding\\procedural"),
    ("procedural-detective literal", b"procedural-detective"),
    ("Fujitsu forward", b"/home/"),
]


def binary_from(archive: Path) -> Path:
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="adv49-d-"))
    if archive.suffix == ".zip":
        import zipfile
        with zipfile.ZipFile(archive) as zf:
            zf.extract("pd-ollama-bridge.exe", d)
        return d / "pd-ollama-bridge.exe"
    import tarfile
    with tarfile.open(archive, "r:gz") as tf:
        for m in tf.getmembers():
            if m.name == "pd-ollama-bridge":
                tf.extract(m, d, filter="data")
        return d / "pd-ollama-bridge"


for archive in [
    ROOT / "dist" / "releases" / "1.0.0" / "pd-ollama-bridge-v1.0.0-windows-x64.zip",
    ROOT / "dist" / "releases" / "1.0.0" / "pd-ollama-bridge-v1.0.0-linux-x64.tar.gz",
]:
    b = binary_from(archive)
    car = CArchiveReader(str(b))
    print("=" * 72)
    print(archive.name, "->", b.name)
    hits = 0
    for name in list(car.toc):
        if "pyz" not in name.lower():
            continue
        reader = car.open_embedded_archive(name)
        if not isinstance(reader, ZlibArchiveReader):
            continue
        for mod in reader.toc:
            data = reader.extract(mod)
            if not isinstance(data, bytes):
                import marshal
                data = marshal.dumps(data)
            for label, pat in PATTERNS:
                if pat in data:
                    print(f"  HIT {label!r} in module {mod}")
                    hits += 1
    print("total dev-path hits:", hits)
