#!/usr/bin/env python3
"""QA deep scan: open the PyInstaller onefile CArchive, decompress the embedded
PYZ and scan every Python module's marshalled bytes for credential/marker
patterns. This reaches INSIDE the compressed payload that a flat byte scan
(and the product build_release.py scan) cannot see. Never prints values.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

CREDENTIALS = [
    ("openrouter-key", re.compile(rb"sk-or-v1-[A-Za-z0-9]{16,}")),
    ("google-api-key", re.compile(rb"AIza[0-9A-Za-z_\-]{35}")),
    ("private-key-header", re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("authorization-bearer", re.compile(rb"Authorization:\s*Bearer\s+\S{8,}", re.IGNORECASE)),
    ("secret-assignment", re.compile(
        rb"(?:API_KEY|SECRET|ACCESS_TOKEN|AUTH_TOKEN|PASSWORD)\s*[=:]\s*[\"']?[A-Za-z0-9_\-]{16,}")),
    ("personal-windows-path", re.compile(rb"C:\\Users\\[A-Za-z0-9._\-]+")),
    ("personal-unix-home", re.compile(rb"(?:/home|/Users)/[A-Za-z0-9._\-]+")),
]
MARKERS = [
    ("OPENROUTER", re.compile(rb"OPENROUTER", re.IGNORECASE)),
    ("GEMINI", re.compile(rb"GEMINI", re.IGNORECASE)),
    ("API_KEY", re.compile(rb"API_KEY", re.IGNORECASE)),
    ("Authorization:", re.compile(rb"Authorization:", re.IGNORECASE)),
    ("Bearer", re.compile(rb"Bearer", re.IGNORECASE)),
    ("PRIVATE KEY", re.compile(rb"PRIVATE KEY", re.IGNORECASE)),
]

findings: list = []


def scan(label: str, data: bytes) -> None:
    for pl, pat in CREDENTIALS:
        for m in pat.finditer(data):
            findings.append(("credential", pl, label, m.start()))
    for pl, pat in MARKERS:
        for m in pat.finditer(data):
            findings.append(("marker", pl, label, m.start()))


def main() -> int:
    exe = Path(sys.argv[1])
    car = CArchiveReader(str(exe))
    print(f"== CArchive entries in {exe.name}:")
    names = list(car.toc)
    print(f"   {len(names)} entries")
    for n in names[:40]:
        print(f"     {n}")
    if len(names) > 40:
        print(f"     ... (+{len(names)-40} more)")

    total_modules = 0
    # The embedded PYZ is exposed as an entry; open_embedded_archive returns a
    # reader for it.
    for name in names:
        low = name.lower()
        if low.endswith(".pyz") or "pyz" in low:
            print(f"\n== decompressing embedded PYZ entry: {name}")
            reader = car.open_embedded_archive(name)
            if isinstance(reader, ZlibArchiveReader):
                mod_names = list(reader.toc)
                print(f"   {len(mod_names)} embedded modules")
                import marshal

                for mod in mod_names:
                    data = reader.extract(mod)
                    if not isinstance(data, bytes):
                        data = marshal.dumps(data)
                    total_modules += 1
                    scan(f"PYZ:{mod}", data)
    print(f"\nScanned {total_modules} embedded modules.")

    creds = [f for f in findings if f[0] == "credential"]
    markers = [f for f in findings if f[0] == "marker"]
    print("\n--- MARKER hits (review, no values) ---")
    for sev, label, where, off in markers:
        print(f"  marker   {label:16} {where} @ {off}")
    if not markers:
        print("  (none)")
    print("\n--- CREDENTIAL hits (fail-closed if any) ---")
    for sev, label, where, off in creds:
        print(f"  CRED    {label:24} {where} @ {off}")
    if not creds:
        print("  (none)")
    print(f"\nTOTAL markers={len(markers)} credentials={len(creds)}")
    return 1 if creds else 0


if __name__ == "__main__":
    raise SystemExit(main())
