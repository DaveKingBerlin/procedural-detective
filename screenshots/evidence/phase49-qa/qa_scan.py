#!/usr/bin/env python3
"""Independent Phase 49 QA secret/integrity scanner.

QA-owned tool. Never prints a matched secret value -- only label/file/offset.
Scans EXTRACTED archive member contents AND the raw archive bytes (high-signal
printable strings) for credential and marker patterns.
"""
from __future__ import annotations

import re
import sys
import tarfile
import zipfile
from pathlib import Path

# Independent pattern set (high-signal credential material).
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

PRINTABLE = re.compile(rb"[\x20-\x7e]{8,}")


def scan(label: str, data: bytes, findings: list) -> None:
    for pat_label, pat in CREDENTIALS:
        for m in pat.finditer(data):
            findings.append(("credential", pat_label, label, m.start()))
    for pat_label, pat in MARKERS:
        for m in pat.finditer(data):
            findings.append(("marker", pat_label, label, m.start()))


def main() -> int:
    findings: list = []
    for arg in sys.argv[1:]:
        path = Path(arg)
        print(f"== {path.name} ({path.stat().st_size} bytes)")
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    data = z.read(info)
                    scan(f"{path.name}!{info.filename}", data, findings)
                    for s in PRINTABLE.findall(data):
                        scan(f"{path.name}!{info.filename}:strings", s, findings)
        elif path.name.endswith(".tar.gz"):
            with tarfile.open(path, "r:gz") as t:
                for m in t.getmembers():
                    if not m.isfile():
                        continue
                    f = t.extractfile(m)
                    data = f.read() if f else b""
                    scan(f"{path.name}!{m.name}", data, findings)
                    for s in PRINTABLE.findall(data):
                        scan(f"{path.name}!{m.name}:strings", s, findings)
        else:
            data = path.read_bytes()
            scan(path.name, data, findings)
            for s in PRINTABLE.findall(data):
                scan(f"{path.name}:strings", s, findings)

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
