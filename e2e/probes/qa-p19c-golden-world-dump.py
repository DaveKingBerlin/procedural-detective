"""QA-owned: dump the golden fake-world scene objects to identify which
interactables carry evidence vs decorative interactions (Phase 19C browser
targeting). Read-only against the live hermetic fake stack."""

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"


def req(method, path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw.decode(errors="replace")[:300]}


st, s = req("POST", "/sessions/anonymous")
st, c = req("POST", "/cases", token=s["anonymousSessionToken"],
            body={"prompt": "Victim: sarah_miller\nMurderer: thomas_reed\nMotive: cover_up_embezzlement\nWeapon: kitchen_knife\nTime: 2026-09-11T22:17:00+02:00\nWitness: emily_reed\n", "difficulty": "medium"})
st, p = req("POST", "/cases/" + c["caseId"] + "/versions/1/playthroughs", token=c["creatorAccessToken"])
st, b = req("GET", "/playthroughs/" + p["playthroughId"] + "/investigation", token=p["playthroughAccessToken"])
print(json.dumps(b["playerKnowledge"], indent=1, sort_keys=True))
print("--- scene objects ---")
for o in b["scene"]["worldObjects"]:
    print(json.dumps(o, indent=1, sort_keys=True))
    print("-----")
print("scene location:", b["scene"]["location"])
print("environment:", b["scene"].get("environmentId"), b["scene"].get("environmentVersion"))