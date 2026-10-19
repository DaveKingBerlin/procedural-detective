"""Plant a fake OpenRouter credential as the ZIP comment + a post-EOCD overlay in
a COPY of the real Windows archive's structure, then let the caller run the real
gate against it. Only the planted value is checked for leakage by the caller.
"""
import io
import zipfile
from pathlib import Path

out = Path(r"C:\Users\Fujitsu\AppData\Local\Temp\opencode\qa49b_extract\planted_marker.zip")
SECRET = "sk-or-v1-PLANTEDmarker0123456789abcdefABCDEF"
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("pd-ollama-bridge.exe", b"hello world")
    z.writestr("QUICKSTART.md", b"benign")
    z.writestr("LICENSE", b"benign")
    z.writestr("THIRD_PARTY_NOTICES.txt", b"benign")
    z.comment = ("OPENROUTER " + SECRET).encode()
data = buf.getvalue() + b"\n--trailing-- " + SECRET.encode()
out.write_bytes(data)
print("planted:", out)
print("secret:", SECRET)
