"""ADV-49 probe A: does tools/build_release.py's secret-scan gate fail open?

Constructs a syntactically-valid Linux tar.gz and a Windows ZIP whose
pd-ollama-bridge.exe member is ZLIB-COMPRESSED credential material (mimicking
PyInstaller's compressed PYZ payload), plus (a) a zip archive COMMENT carrying a
secret and (b) secret bytes appended AFTER the zip EOCD record. Runs the real
release assembly and reports exit code + whether the credential scan saw it.

No product code is modified. Throwaway probe.
"""
from __future__ import annotations

import io
import sys
import tarfile
import tempfile
import zipfile
import zlib
from pathlib import Path

ROOT = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
sys.path.insert(0, str(ROOT))
from tools import build_release as br  # noqa: E402

FAKE_OPENROUTER = b"sk-or-v1-" + b"A" * 32
FAKE_GEMINI = b"AIza" + b"B" * 35
FAKE_WINPATH = b"C:\\Users\\FujitsuSECRETPATH\\bridge.toml"
SECRET_PAYLOAD = FAKE_OPENROUTER + b"\n" + FAKE_GEMINI + b"\n" + FAKE_WINPATH

work = Path(tempfile.mkdtemp(prefix="adv49-a-"))
print("workdir:", work)

# ---- valid Linux tar.gz (required members, exec bit) ----------------------
tar_path = work / "pd-ollama-bridge-v1.0.0-linux-x64.tar.gz"
with tarfile.open(tar_path, "w:gz") as tf:
    for name, data, mode in [
        ("pd-ollama-bridge", b"#!/bin/sh\necho hi\n", 0o755),
        ("QUICKSTART.md", b"qs\n", 0o644),
        ("LICENSE", b"MIT\n", 0o644),
        ("THIRD_PARTY_NOTICES.txt", b"notices\n", 0o644),
    ]:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = mode
        info.type = tarfile.REGTYPE
        tf.addfile(info, io.BytesIO(data))

# ---- hostile Windows ZIP --------------------------------------------------
zip_path = work / "pd-ollama-bridge-v1.0.0-windows-x64.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("pd-ollama-bridge.exe", zlib.compress(SECRET_PAYLOAD))
    zf.writestr("QUICKSTART.md", b"qs\n")
    zf.writestr("LICENSE", b"MIT\n")
    zf.writestr("THIRD_PARTY_NOTICES.txt", b"notices\n")
    zf.comment = FAKE_OPENROUTER
with zip_path.open("ab") as fh:
    fh.write(b"\nTRAILING-OVERLAY " + FAKE_GEMINI + b"\n")

print("zip bytes:", zip_path.stat().st_size, "tar bytes:", tar_path.stat().st_size)

# ---- run the real assembly -------------------------------------------------
out_dir = work / "out"
argv = [
    "--windows-zip", str(zip_path),
    "--linux-tar", str(tar_path),
    "--out", str(out_dir),
    "--manifest-out", str(work / "release-manifest.json"),
    "--allow-dirty",
    "--released-at", "2026-01-01T00:00:00Z",
]
print("\n--- build_release.main argv ---")
print(" ".join(argv))
try:
    rc = br.main(argv)
except SystemExit as exc:
    rc = exc.code
print("\nEXIT CODE:", rc)
print("manifest written:", (work / "release-manifest.json").exists())
print("SHA256SUMS written:", (out_dir / "SHA256SUMS.txt").exists())
print("WORKDIR_FOR_EVIDENCE:", work)
