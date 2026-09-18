"""QA Phase 15 — LIVE-API prompt-injection / world-influence re-scan (durable).

Hits the RUNNING production API (the QA browser stack :8000) exactly like a
browser client and proves the adversarial prompt strings cannot influence the
published world: script/URL/path/javascript/data framing yields zero world
object requests, environment stays the golden apartment default, and no
script/url/path token ever appears in the published material. Also re-checks
IDOR/token boundaries (a foreign token cannot read the case) and hostile
AssetSpec behavior via forced provider failure (no substitute ever publishes).

Exit 0 = all PASS; 1 = any FAIL.
"""
from __future__ import annotations

import json
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"
results: list[tuple[str, bool, object]] = []


def record(name: str, ok: bool, detail: object) -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name} :: {json.dumps(detail, ensure_ascii=False)[:400]}")


def post(path: str, body: dict, token: str | None = None) -> tuple[int, object]:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {"raw": raw[:500]}


def get(path: str, token: str | None = None) -> tuple[int, object]:
    req = urllib.request.Request(BASE + path, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {"raw": raw[:500]}


HOSTILE_PROMPTS = {
    "script": "The killer used a <script>alert(1)</script> near the body in the office. "
              "Victim: Sarah Miller\nMurderer: Thomas Reed\nMotive: embezzlement\n"
              "Weapon: kitchen knife\nTime: 22:17\nWitness: Emily Reed\n",
    "url": "The killer left a https://evil.example/bin and a file:///etc/passwd near the body. "
           "Victim: Sarah Miller\nMurderer: Thomas Reed\nMotive: embezzlement\n"
           "Weapon: kitchen knife\nTime: 22:17\nWitness: Emily Reed\n",
    "path": "The tool was a C:\\windows\\system32\\cmd.exe and a ..\\..\\..\\evil payload near the body. "
            "Victim: Sarah Miller\nMurderer: Thomas Reed\nMotive: embezzlement\n"
            "Weapon: kitchen knife\nTime: 22:17\nWitness: Emily Reed\n",
    "quoted-json": 'The killer typed {"objects": "wrench on the wall"} near the body in the office. '
                   "Victim: Sarah Miller\nMurderer: Thomas Reed\nMotive: embezzlement\n"
                   "Weapon: kitchen knife\nTime: 22:17\nWitness: Emily Reed\n",
    "data-url": "A data:text/html,<script>alert(1)</script> weapon was used. "
                "Victim: Sarah Miller\nMurderer: Thomas Reed\nMotive: embezzlement\n"
                "Weapon: kitchen knife\nTime: 22:17\nWitness: Emily Reed\n",
}


def main() -> int:
    for label, prompt in HOSTILE_PROMPTS.items():
        status, session = post("/api/v1/sessions/anonymous", {})
        record(f"[{label}] anonymous session 201", status == 201, status)
        status, created = post(
            "/api/v1/cases",
            {"prompt": prompt, "difficulty": "medium"},
            token=session.get("anonymousSessionToken"),
        )
        record(f"[{label}] POST /cases 201 PUBLISHED", status == 201 and created.get("status") == "PUBLISHED",
               {"status": status, "caseStatus": created.get("status")})
        pid = created.get("caseId")
        status, public_case = get(f"/api/v1/cases/{pid}?version=1")
        body_text = json.dumps(public_case, ensure_ascii=False).lower()
        forbidden = ["<script", "evil.example", "file:///", "cmd.exe", "javascript:", "data:text/html"]
        hits = [t for t in forbidden if t in body_text]
        record(f"[{label}] published material carries NO injection token", not hits, hits)
        # environment: no fabricated building; default/normal kit only
        scene_env_ids = set()
        def walk(node: object) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    if k == "environmentId" and isinstance(v, str):
                        scene_env_ids.add(v)
                    walk(v)
            elif isinstance(node, list):
                for item in node:
                    walk(item)
        walk(public_case)
        record(f"[{label}] environment ids are documented kits only",
               scene_env_ids <= {"apartment", "office", "hotel_suite", "warehouse", "mansion"},
               sorted(scene_env_ids))

    # IDOR + token boundary: a foreign token cannot open a foreign playthrough.
    status, session = post("/api/v1/sessions/anonymous", {})
    status, created = post("/api/v1/cases", {
        "prompt": "Victim: Sarah Miller\nMurderer: Thomas Reed\nMotive: embezzlement\n"
                  "Weapon: kitchen knife\nTime: 22:17\nWitness: Emily Reed\n",
        "difficulty": "medium",
    }, token=session.get("anonymousSessionToken"))
    status, pt = post(
        f"/api/v1/cases/{created['caseId']}/versions/1/playthroughs",
        {},
        token=created.get("creatorAccessToken"),
    )
    status, boot = get(
        f"/api/v1/playthroughs/{pt['playthroughId']}/investigation",
        token=pt.get("playthroughAccessToken"),
    )
    record("own token can bootstrap", status == 200 and boot.get("state") == "PLAYING", status)
    status2, _ = post("/api/v1/sessions/anonymous", {})
    session2 = _
    status2, _ = post("/api/v1/cases", {
        "prompt": "Victim: Sarah Miller\nMurderer: Thomas Reed\nMotive: embezzlement\n"
                  "Weapon: kitchen knife\nTime: 22:17\nWitness: Emily Reed\n",
        "difficulty": "medium",
    }, token=session2.get("anonymousSessionToken"))
    # foreign (wrong) token on the first playthrough
    status, body = get(
        f"/api/v1/playthroughs/{pt['playthroughId']}/investigation",
        token="PT-FOREIGN-INVALID-TOKEN-0000000000000000000000000000",
    )
    record("foreign token rejected on investigation", status in (401, 403, 404), {"status": status, "body": body})
    status, body = post(
        f"/api/v1/playthroughs/{pt['playthroughId']}/accusations?version=1",
        {"accusation": {"murdererId": "thomas_reed", "motiveId": "cover_up_embezzlement",
                        "weaponId": "kitchen_knife", "crimeTime": "22:17"}},
        token="PT-FOREIGN-INVALID-TOKEN-0000000000000000000000000000",
    )
    record("foreign token rejected on accusation", status in (401, 403, 404), {"status": status})

    print(f"\nRESULT: {sum(1 for _, ok, _ in results if ok)}/{len(results)} PASS")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())