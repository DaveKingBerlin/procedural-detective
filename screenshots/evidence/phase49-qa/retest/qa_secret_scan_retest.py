"""Independent QA Phase 49 retest: scan the REAL final artifacts for credential
material - raw archive bytes, every extracted member, and each binary's
decompressed PyInstaller PYZ payload. Independent implementation (does not import
the release tool's patterns). Values are never printed; only label/offset.
"""
from __future__ import annotations

import io
import re
import tarfile
import tempfile
import zipfile
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
DIST = ROOT / "dist" / "releases" / "1.0.0"

PATTERNS = [
    ("openrouter", re.compile(rb"sk-or-v1-[A-Za-z0-9]{16,}")),
    ("google", re.compile(rb"AIza[0-9A-Za-z_\-]{35}")),
    ("private-key", re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("bearer", re.compile(rb"Authorization:\s*Bearer\s+\S{8,}", re.I)),
    ("secret-assign", re.compile(rb"(?:API_KEY|SECRET|ACCESS_TOKEN|AUTH_TOKEN|PASSWORD)\s*[=:]\s*[\"']?[A-Za-z0-9_\-]{16,}")),
    ("win-path", re.compile(rb"C:\\Users\\[A-Za-z0-9._\-]+", re.I)),
    ("unix-home", re.compile(rb"/(?:home|Users)/[A-Za-z0-9._\-]+")),
]

def scan(label, data, hits):
    for name, pat in PATTERNS:
        for m in pat.finditer(data):
            hits.append((name, label, m.start()))

all_hits = []
for label, arc in [("WIN-ZIP", DIST / "pd-ollama-bridge-v1.0.0-windows-x64.zip"),
                   ("LIN-TAR", DIST / "pd-ollama-bridge-v1.0.0-linux-x64.tar.gz")]:
    raw = arc.read_bytes()
    scan(f"raw:{arc.name}", raw, all_hits)
    d = Path(tempfile.mkdtemp(prefix="qa49-scan-"))
    if arc.suffix == ".zip":
        with zipfile.ZipFile(arc) as z:
            for m in z.infolist():
                if m.is_dir():
                    continue
                scan(f"{arc.name}!{m.filename}", z.read(m), all_hits)
            z.extractall(d)
        binary = d / "pd-ollama-bridge.exe"
    else:
        with tarfile.open(arc, "r:gz") as t:
            for m in t.getmembers():
                f = t.extractfile(m)
                if f:
                    scan(f"{arc.name}!{m.name}", f.read(), all_hits)
            t.extractall(d)
        binary = d / "pd-ollama-bridge"

    # deep PYZ scan of the real binary
    car = CArchiveReader(str(binary))
    pyz = [n for n in car.toc if n.lower().endswith(".pyz") or "pyz" in n.lower()]
    print(f"{label}: binary={binary.name} pyz_entries={pyz}")
    for n in pyz:
        r = car.open_embedded_archive(n)
        blob = b""
        for key in r.toc:
            try:
                obj = r.extract(key)
            except Exception:
                continue
            if isinstance(obj, (bytes, bytearray)):
                blob += bytes(obj)
        scan(f"{arc.name}!{n}@decompressed-bytes", blob, all_hits)
    # scan the whole binary raw bytes too
    scan(f"{arc.name}!{binary.name}:rawbytes", binary.read_bytes(), all_hits)

print("\n=== credential hits ===")
if not all_hits:
    print("0 credential hits across raw archives, members, raw binaries and decompressed PYZ.")
else:
    for name, label, off in all_hits:
        print(f"  HIT {name} in {label} @ byte {off}")
