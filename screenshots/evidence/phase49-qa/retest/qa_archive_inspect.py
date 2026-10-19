"""Independent QA Phase 49 final-retest archive inspection.

Lists members (raw names + normalized), modes, sizes, detects nested/traversal/
absolute/symlink members, and extracts to a target dir. Read-only w.r.t. product.
"""
import sys
import os
import zipfile
import tarfile

WIN = "dist/releases/1.0.0/pd-ollama-bridge-v1.0.0-windows-x64.zip"
LIN = "dist/releases/1.0.0/pd-ollama-bridge-v1.0.0-linux-x64.tar.gz"

def is_unsafe(name: str) -> str | None:
    n = name.replace("\\", "/")
    if n.startswith("/") or (len(n) > 1 and n[1] == ":"):
        return "absolute"
    parts = n.split("/")
    if ".." in parts:
        return "traversal"
    if n.endswith("/"):
        return "directory-entry"
    return None

print("=== WINDOWS ZIP ===")
with zipfile.ZipFile(WIN) as z:
    infos = z.infolist()
    print(f"member_count={len(infos)}")
    for i in infos:
        unsafe = is_unsafe(i.filename)
        print(f"  name={i.filename!r} size={i.file_size} compressed={i.compress_size} "
              f"crc={i.CRC:08x} unsafe={unsafe} nested={'/' in i.filename.strip('/')}")
    print(f"zip_comment_len={len(z.comment)}")

print("\n=== LINUX TAR.GZ ===")
with tarfile.open(LIN, "r:gz") as t:
    members = t.getmembers()
    print(f"member_count={len(members)}")
    for m in members:
        unsafe = is_unsafe(m.name)
        print(f"  name={m.name!r} type={m.type!r} size={m.size} mode={oct(m.mode)} "
              f"unsafe={unsafe} nested={'/' in m.name.strip('/')}")

# Extract
win_out = sys.argv[1] if len(sys.argv) > 1 else "win"
lin_out = sys.argv[2] if len(sys.argv) > 2 else "lin"
os.makedirs(win_out, exist_ok=True)
os.makedirs(lin_out, exist_ok=True)
with zipfile.ZipFile(WIN) as z:
    z.extractall(win_out)
with tarfile.open(LIN, "r:gz") as t:
    t.extractall(lin_out)
print(f"\nextracted win -> {win_out}")
print(f"extracted lin -> {lin_out}")
