"""PHASE 48 QA RETEST — end-to-end proof of the ADV-48-01 backend fix.

Drives the LIVE hermetic backend (GENERATION_PROVIDER=fake) through the normal
``POST /api/v1/cases`` path with three prompts that differ ONLY in the victim
line's trailing Phase-47 profile parenthetical:

  A) ``Victim: sarah_miller`` (name-only baseline)
  B) ``Victim: sarah_miller (Female, 42, accountant)`` (the Phase-48 required
     parenthetical form; pre-fix this polluted the victim lock and FAILED)
  C) ``Victim: sarah_miller (Female, 42, accountant)`` + full canonical shape

All three must reach the SAME status as the name-only baseline (PUBLISHED)
against the fixed fake golden fixture — i.e. the parenthetical is profile
metadata and no longer changes the locked victim identity.

Run (backend already listening on :8000):
    python e2e/probes/qa_p48_retest_provider.py
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def _req(method: str, path: str, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
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


def submit(prompt: str) -> dict:
    token = new_session()
    status, body = _req(
        "POST",
        "/api/v1/cases",
        token,
        {"prompt": prompt, "difficulty": "medium", "generationProvider": "fake"},
    )
    return {
        "http_status": status,
        "status": body.get("status"),
        "failureCode": body.get("failureCode"),
        "caseId": body.get("caseId"),
    }


def main() -> int:
    out = {
        "A_name_only": submit("Victim: sarah_miller\nMurderer: thomas_reed"),
        "B_parenthetical": submit(
            "Victim: sarah_miller (Female, 42, accountant)\nMurderer: thomas_reed"
        ),
        "C_parenthetical_display_name": submit(
            "Victim: Sarah Miller (Female, 42, accountant)\nMurderer: Thomas Reed"
        ),
    }
    out["same_status_A_B"] = out["A_name_only"]["status"] == out["B_parenthetical"]["status"]
    out["same_status_A_C"] = out["A_name_only"]["status"] == out["C_parenthetical_display_name"]["status"]
    out["result"] = (
        "PASS"
        if out["same_status_A_B"] and out["same_status_A_C"] and out["A_name_only"]["status"] == "PUBLISHED"
        else "FAIL"
    )
    print(json.dumps(out, indent=2))
    return 0 if out["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
