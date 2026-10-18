"""PHASE 48 — QA provider-independence + fake-provider acceptance probe.

Drives the PUBLIC API against a running hermetic backend
(ENV_FILE=os.devnull, GENERATION_PROVIDER=fake, DATABASE_URL=<scratch sqlite>).

Independent of the developer's tests. Reports, for:
  A) the QA-generated random prompt (seed 12345, Hard) via the NORMAL
     POST /api/v1/cases path with generation_provider="fake";
  B) a control prompt whose locked entities MATCH the fixed dev golden case
     (victim=sarah_miller, murderer=thomas_reed) via the same path.

Run:  python e2e/probes/qa-p48-provider-independence.py
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"

# QA independent generator output for seed=12345, difficulty=hard (see
# screenshots/evidence/phase48/independent-generator-verification.json).
RANDOM_PROMPT = "\n".join(
    [
        "Victim: Emma Lehmann (Female, 74, nurse)",
        "Murderer: Petra Braun",
        "Motive: fear of dismissal",
        "Weapon: nightstand",
        "Time: 02:43",
        "Witness: Hugo Dietrich",
        "Location: warehouse loading bay",
    ]
)

CONTROL_PROMPT = "Victim: sarah_miller\nMurderer: thomas_reed\n"


def _req(method: str, path: str, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {"raw": raw}
        return exc.code, parsed


def new_session() -> str:
    status, body = _req("POST", "/api/v1/sessions/anonymous")
    assert status == 201, (status, body)
    return body["anonymousSessionToken"]


def main() -> int:
    out: dict = {}
    token = new_session()

    status, body = _req(
        "POST",
        "/api/v1/cases",
        token,
        {"prompt": RANDOM_PROMPT, "difficulty": "hard", "generationProvider": "fake"},
    )
    out["random_prompt"] = {
        "http_status": status,
        "status": body.get("status"),
        "failureCode": body.get("failureCode"),
        "caseId": body.get("caseId"),
        "body_keys": sorted(body.keys()),
    }

    token2 = new_session()
    status2, body2 = _req(
        "POST",
        "/api/v1/cases",
        token2,
        {"prompt": CONTROL_PROMPT, "difficulty": "medium", "generationProvider": "fake"},
    )
    out["control_golden_prompt"] = {
        "http_status": status2,
        "status": body2.get("status"),
        "failureCode": body2.get("failureCode"),
        "caseId": body2.get("caseId"),
    }

    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
