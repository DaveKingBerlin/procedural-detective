"""QA-owned Phase 19B REAL-HOST per-run probe (QA; .rad/roles/qa.md).

Runs EXACTLY ONE real case generation against the running real backend
(GENERATION_PROVIDER=ollama, local 127.0.0.1, model llama3.2:3b) via the
public API and records the structured outcome. The phase explicitly forbids
repeated runs to force luck — this probe must be invoked EXACTLY once per
example (Easy / Medium / Hard).

Flow (public API only, stdlib):
  POST /api/v1/sessions/anonymous                     -> anonymousSessionToken
  POST /api/v1/cases          {prompt, difficulty}    -> 201 CaseStartedDTO
  GET  /api/v1/generations/{generationId}  (Bearer creatorAccessToken)
        poll until PUBLISHED or terminal FAILED

Result JSON written to --result path (scratch dir under %TEMP%):
  label, prompt, caseId, generationId, generationAttemptId,
  creatorAccessToken, status (final), failureCode, durationSec.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BACKEND = "http://127.0.0.1:8000"
TERMINAL = {"PUBLISHED", "FAILED"}
# POST /cases is SYNCHRONOUS server-side (the run completes inside the
# request), so the create call itself can take minutes on a CPU-only local
# Ollama box. The read timeout must exceed the configured generation deadline.
_TIMEOUT_READ = 750.0
POLL_INTERVAL = 4.0
HARD_DEADLINE = 660.0  # >= the 600 s generation deadline + poll margin


def _req(method: str, path: str, body=None, token: str | None = None, timeout: float = 120.0):
    url = BACKEND + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw)
            except (ValueError, json.JSONDecodeError):
                return resp.status, {"_raw": raw.decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            return e.code, {"_raw": raw.decode("utf-8", "replace")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--difficulty", default="standard")
    args = ap.parse_args()

    result = {"label": args.label, "prompt": args.prompt}

    st, sess = _req("POST", "/api/v1/sessions/anonymous")
    if st != 201:
        result["error"] = f"session create failed {st}: {sess}"
        print(json.dumps(result, indent=2))
        with open(args.result, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        return 1
    anon_token = sess["anonymousSessionToken"]
    result["anonymousSessionToken"] = anon_token

    body = {"prompt": args.prompt, "difficulty": args.difficulty}
    st, case = _req("POST", "/api/v1/cases", body=body, token=anon_token, timeout=_TIMEOUT_READ)
    if st != 201:
        result["error"] = f"case create failed {st}: {case}"
        print(json.dumps(result, indent=2))
        with open(args.result, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        return 1

    result["caseId"] = case["caseId"]
    result["generationId"] = case["generationId"]
    result["generationAttemptId"] = case["generationAttemptId"]
    result["creatorAccessToken"] = case["creatorAccessToken"]
    result["initialStatus"] = case["status"]
    result["initialFailureCode"] = case.get("failureCode")

    token = case["creatorAccessToken"]
    gen_id = case["generationId"]
    t0 = time.monotonic()
    final = None
    while time.monotonic() - t0 < HARD_DEADLINE:
        st, prog = _req("GET", f"/api/v1/generations/{gen_id}", token=token)
        if st != 200:
            result["error"] = f"poll {st}: {prog}"
            print(json.dumps(result, indent=2))
            with open(args.result, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
            return 1
        final = prog
        if prog.get("status") in TERMINAL:
            break
        time.sleep(POLL_INTERVAL)

    if final is None:
        result["error"] = "timed out before terminal state (host probe deadline)"
        print(json.dumps(result, indent=2))
        result["status"] = "TIMED_OUT"
        with open(args.result, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        return 1

    result["status"] = final["status"]
    result["progress"] = final["progress"]
    result["stage"] = final.get("stage")
    result["failureCode"] = final.get("failureCode")
    result["durationSec"] = round(time.monotonic() - t0, 2)

    print(json.dumps(result, indent=2))
    with open(args.result, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
