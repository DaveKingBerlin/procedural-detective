#!/usr/bin/env python3
"""QA: build a tampered Windows ZIP (canonical name, exactly the 4 required
members) with a fake OpenRouter-shaped secret planted in QUICKSTART.md, to prove
tools/build_release.py fails closed and never prints the secret value."""
from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

SRC = Path("dist/releases/1.0.0/pd-ollama-bridge-v1.0.0-windows-x64.zip")
DST_DIR = Path(sys.argv[1])
FAKE_SECRET = "sk-or-v1-" + "DEADBEEF" * 4  # matches openrouter-key pattern, fake

DST_DIR.mkdir(parents=True, exist_ok=True)
dst = DST_DIR / SRC.name

with zipfile.ZipFile(SRC) as zin:
    members = [i for i in zin.infolist() if not i.is_dir()]
    names = [Path(i.filename).name for i in members]
    print("source members:", names)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in members:
            data = zin.read(info)
            name = Path(info.filename).name
            if name == "QUICKSTART.md":
                data = data + (
                    "\n\nFAKE QA INJECTION: API_KEY=" + FAKE_SECRET + "\n"
                ).encode()
                print("planted fake secret in QUICKSTART.md")
            zout.writestr(name, data)

print("wrote", dst, dst.stat().st_size, "bytes")
print("FAKE_SECRET length", len(FAKE_SECRET))
