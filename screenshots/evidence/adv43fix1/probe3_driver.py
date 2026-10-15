"""Driver: runs probe3_child for each (response, mode) under a hard timeout."""
import subprocess
import sys
from pathlib import Path

CHILD = str(Path(__file__).with_name("probe3_child.py"))
TIMEOUT = 8

for label in ("FileResponse", "StreamingResponse"):
    for mode in ("fixed", "old"):
        print(f"--- {mode} {label} ---", flush=True)
        try:
            r = subprocess.run([sys.executable, CHILD, label, mode],
                               capture_output=True, text=True, timeout=TIMEOUT)
            out = (r.stdout or "") + (r.stderr or "")
            for line in out.splitlines():
                if line.startswith(("START", "COMPLETED")):
                    print("   ", line)
            print(f"    exit={r.returncode}")
        except subprocess.TimeoutExpired:
            print(f"    TIMEOUT after {TIMEOUT}s -> event loop starved "
                  f"(no response, hot loop)")