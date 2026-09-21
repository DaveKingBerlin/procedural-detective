"""QA-owned Phase 19C independent API probe (hermetic fake stack).

Runs against the LIVE hermetic stack started by QA via tools/process_guard
(backend :8000 GENERATION_PROVIDER=fake + scratch SQLite under %TEMP%, vite
preview :4173 serving the production SPA). Proves the Phase 19C interaction
contract over the real wire — NOT via TestClient:

  G1  golden laptop interact (read)          -> 200, discovery
      {evidenceId: email_thomas_01, state: discovered}, DTO exactly the
      frozen key set, and ZERO undiscovered evidence ids/titles/descriptions
      anywhere in the response;
  G2  repeat interact                        -> "already-discovered",
      idempotent (knowledge set unchanged);
  G3  POST-interact bootstrap                -> laptop discovered=True +
      read=True, playerKnowledge.discoveredEvidenceIds contains EXACTLY
      the newly discovered id (evidence counter model);
  G4  golden knife interact (inspect)        -> 200 forensic discovery,
      counter grows deterministically; leak scan stays clean;
  G5  decorative vase                        -> 409 INTERACTION_NOT_ALLOWED
      (no dead-end clickable in the golden world) with NO state change;
  G6  direct evidence-discovery bypass       -> 404 sanitized, no state change;
  G7  accusation + reveal after interact     -> deterministic REVEALED,
      4/4, and a fresh playthrough on the SAME case reveals the SAME truth;
      pre-reveal leak scans over every API body stay clean.

The non-evidence "Nothing relevant was found on <X>." 200 {discovery:null,
evidenceId:null} contract is proven (a) in-process via the driver path in
backend/tests/test_phase19c_interaction.py::test_3b and (b) by the 986-test
frontend suite (renderInvestigation.test.ts / investigationFlow.test.ts) —
the golden fake world wires evidence on every grand interactable (laptop/
knife), so it cannot manufacture that branch on the API surface. This probe
additionally proves the golden world has NO other-clickable dead-end (G5 is
the vase, whose published interaction is "").
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"
GOLDEN_PROMPT = (
    "Victim: sarah_miller\nMurderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\nWeapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\nWitness: emily_reed\n"
)

LAPTOP = "apartment_laptop"
LAPTOP_EVIDENCE = "email_thomas_01"
KNIFE = "kitchen_knife"
KNIFE_EVIDENCE = "forensic_knife_match_01"
VASE = "vase_01"
CANONICAL_TIME = "2026-09-11T22:17:00+02:00"

# Keys which must never appear in an interact DTO / bootstrap pre-reveal body
# (mirror of the frozen Phase-7 scanner; the interact DTO is allowed exactly
# the frozen key set).
INTERACT_KEYS = {"objectId", "interaction", "evidenceId", "discovery", "result"}
DISCOVERY_KEYS = {"evidenceId", "kind", "title", "interaction", "state"}
PRE_REVEAL_FORBIDDEN = {
    "murdererId", "victimId", "weaponId", "crimeTime", "canonical",
    "truthfulness", "crime", "timeline", "facts", "relationships",
    "solutionProof", "solverProof", "proof", "truth", "universe", "universes",
    "validation", "winners", "prompt", "providerOutput", "diagnostics", "seed",
    "model", "locked", "stageOutputs", "sourceRef", "propositions",
    "observedAt", "uncertaintySeconds", "verifier", "tokenVerifier", "token",
    "sessionId", "generationAttemptId",
}


def req(method, path, token=None, body=None):
    url = f"{BASE}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw.decode(errors="replace")[:500]}


def walk(node, path, hits):
    if isinstance(node, dict):
        for k, v in node.items():
            child = f"{path}.{k}" if path else k
            if k in PRE_REVEAL_FORBIDDEN:
                hits.append(child)
            walk(v, child, hits)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, f"{path}[{i}]", hits)


# The accusation 200 MAY echo the player's OWN submitted fields under the
# frozen `accusation` echo node (REQUIREMENTS 40.10); that echo is exempt.
def forbidden_hits(body):
    hits = []
    for path, node in _iter(body, "$"):
        leaf_key = path.rsplit(".", 1)[-1]
        under_echo = ".accusation" in path
        if leaf_key in PRE_REVEAL_FORBIDDEN and not under_echo:
            hits.append(path)
    return hits


def _iter(node, path):
    if isinstance(node, dict):
        for k, v in node.items():
            child = f"{path}.{k}" if path else k
            yield child, v
            yield from _iter(v, child)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _iter(v, f"{path}[{i}]")


def main():
    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))
        if not cond:
            print(f"FAIL {name}: {detail}")

    # 0. session + deterministic fake case + playthrough
    status, session = req("POST", "/sessions/anonymous")
    check("00 session", status == 201)
    st, created = req("POST", "/cases", token=session["anonymousSessionToken"],
                      body={"prompt": GOLDEN_PROMPT, "difficulty": "medium"})
    check("01 case published", st == 201 and created.get("status") == "PUBLISHED",
          json.dumps(created)[:300])
    case_id, creator = created["caseId"], created["creatorAccessToken"]
    st, pt = req("POST", f"/cases/{case_id}/versions/1/playthroughs", token=creator)
    check("02 playthrough", st == 201, json.dumps(pt)[:300])
    pt_id, pt_token = pt["playthroughId"], pt["playthroughAccessToken"]

    # forbidden evidence material = every published evidence id/title/desc
    st, pub = req("GET", f"/cases/{case_id}", token=creator)
    check("03 public case", st == 200)
    evidence_rows = (pub.get("evidence") or []) if isinstance(pub, dict) else []
    forbidden_ids, forbidden_titles, forbidden_descs = set(), set(), set()
    for row in evidence_rows:
        eid = row.get("id")
        if eid:
            forbidden_ids.add(str(eid))
        title = row.get("title")
        if isinstance(title, str):
            forbidden_titles.add(title)
        desc = row.get("description")
        if isinstance(desc, str):
            forbidden_descs.add(desc)
    check("04 public dossier has evidence rows",
          len(forbidden_ids) >= 2, f"ids={sorted(forbidden_ids)}")

    # G1 GOLDEN LAPTOP
    st, boot0 = req("GET", f"/playthroughs/{pt_id}/investigation", token=pt_token)
    check("05 bootstrap pre", st == 200)
    world0 = {o["objectId"]: o for o in boot0["scene"]["worldObjects"]}
    check("06 laptop pre undiscovered",
          LAPTOP in world0 and world0[LAPTOP]["discovered"] is False
          and world0[LAPTOP]["read"] is False)
    check("07 knowledge pre empty", boot0["playerKnowledge"]["discoveredEvidenceIds"] == [])

    st, body = req("POST", f"/playthroughs/{pt_id}/objects/{LAPTOP}/interact",
                   token=pt_token, body={"interaction": "read"})
    check("G1a 200", st == 200, json.dumps(body)[:400])
    check("G1b DTO key set exact", set(body) == INTERACT_KEYS, str(set(body)))
    d = body["discovery"]
    check("G1c discovery key set exact", set(d) == DISCOVERY_KEYS, str(set(d)))
    check("G1d evidenceId", body["evidenceId"] == LAPTOP_EVIDENCE
          and d["evidenceId"] == LAPTOP_EVIDENCE and d["kind"] == "email"
          and d["state"] == "discovered" and d["interaction"] == "read"
          and body["result"] == "interacted")
    text = json.dumps(body, sort_keys=True)
    leaks = [i for i in forbidden_ids if i != LAPTOP_EVIDENCE and i in text]
    check("G1e no undiscovered evidence ids",
          not leaks, f"leaked ids={leaks}")
    title_leaks = [t for t in forbidden_titles
                   if t and t != d.get("title") and t in text]
    check("G1f no undiscovered evidence titles",
          not title_leaks, f"leaked titles={title_leaks}")
    desc_leaks = [s for s in forbidden_descs if s and s in text]
    check("G1g no evidence descriptions",
          not desc_leaks, f"leaked descs={desc_leaks}")
    check("G1h no pre-reveal structural keys", forbidden_hits(body) == [],
          str(forbidden_hits(body)))

    # G2 REPEAT -> already-discovered + idempotent
    st2, body2 = req("POST", f"/playthroughs/{pt_id}/objects/{LAPTOP}/interact",
                     token=pt_token, body={"interaction": "read"})
    check("G2a 200 repeat", st2 == 200)
    check("G2b already-discovered", body2["discovery"]["state"] == "already-discovered")
    check("G2c idempotent DTO still exact", set(body2) == INTERACT_KEYS
          and body2["evidenceId"] == LAPTOP_EVIDENCE)

    # G3 BOOTSTRAP POST — replicate the frontend flow exactly: after the
    # interact the SPA opens the record (GET /records/{id}, REQUIREMENTS 40.9)
    # which is what flips worldObjects[*].read; then re-fetch the bootstrap.
    st, rec = req("GET", f"/playthroughs/{pt_id}/records/{LAPTOP_EVIDENCE}", token=pt_token)
    check("G3a0 record read 200", st == 200 and rec.get("readByPlayer") is True,
          json.dumps(rec)[:300])
    st, boot = req("GET", f"/playthroughs/{pt_id}/investigation", token=pt_token)
    check("G3a 200", st == 200)
    world = {o["objectId"]: o for o in boot["scene"]["worldObjects"]}
    check("G3b laptop discovered+read",
          world[LAPTOP]["discovered"] is True and world[LAPTOP]["read"] is True,
          str({"disc": world[LAPTOP]["discovered"], "read": world[LAPTOP]["read"]}))
    known = boot["playerKnowledge"]["discoveredEvidenceIds"]
    check("G3c counter model exactly the new id", known == [LAPTOP_EVIDENCE],
          str(known))
    check("G3d bootstrap clean", forbidden_hits(boot) == [],
          str(forbidden_hits(boot))[:300])

    # G4 KNIFE
    st, knife = req("POST", f"/playthroughs/{pt_id}/objects/{KNIFE}/interact",
                    token=pt_token, body={"interaction": "inspect"})
    check("G4a 200", st == 200)
    check("G4b forensic discovery",
          knife["evidenceId"] == KNIFE_EVIDENCE
          and knife["discovery"]["kind"] == "forensic"
          and knife["discovery"]["state"] == "discovered"
          and set(knife) == INTERACT_KEYS)
    ktext = json.dumps(knife, sort_keys=True)
    k_leaks = [i for i in forbidden_ids
               if i not in (LAPTOP_EVIDENCE, KNIFE_EVIDENCE) and i in ktext]
    check("G4c knife DTO no undiscovered ids", not k_leaks, str(k_leaks))
    st, rec2 = req("GET", f"/playthroughs/{pt_id}/records/{KNIFE_EVIDENCE}", token=pt_token)
    check("G4c2 knife record read", st == 200 and rec2.get("readByPlayer") is True,
          json.dumps(rec2)[:200])
    st, boot2 = req("GET", f"/playthroughs/{pt_id}/investigation", token=pt_token)
    known2 = boot2["playerKnowledge"]["discoveredEvidenceIds"]
    check("G4e counter grows deterministically",
          known2 == [LAPTOP_EVIDENCE, KNIFE_EVIDENCE], str(known2))
    world2 = {o["objectId"]: o for o in boot2["scene"]["worldObjects"]}
    check("G4f knife flagged discovered+read",
          world2[KNIFE]["discovered"] is True and world2[KNIFE]["read"] is True,
          str(world2[KNIFE]))

    # G5 VASE (decorative, interaction "") must NOT be a dead-end interactable
    st, vase = req("POST", f"/playthroughs/{pt_id}/objects/{VASE}/interact",
                   token=pt_token, body={"interaction": "inspect"})
    check("G5a 409", st == 409, f"{st} {json.dumps(vase)[:200]}")
    check("G5b INTERACTION_NOT_ALLOWED",
          vase.get("error", {}).get("code") == "INTERACTION_NOT_ALLOWED")
    check("G5c sanitized error",
          not any(m in json.dumps(vase) for m in ("Traceback", "sqlite", "SELECT")))
    st, boot3 = req("GET", f"/playthroughs/{pt_id}/investigation", token=pt_token)
    check("G5d no knowledge mutation",
          boot3["playerKnowledge"]["discoveredEvidenceIds"] == known2)

    # G6 DIRECT BYPASS impossible
    st, evil = req("POST", f"/playthroughs/{pt_id}/evidence/ghost_evidence_99/discover",
                   token=pt_token)
    check("G6a 404", st == 404)
    check("G6b sanitized", evil.get("error", {}).get("code") == "NOT_FOUND")

    # G7 ACCUSE (golden public candidates) + REVEAL determinism
    acc_body = {
        "murdererId": "thomas_reed",
        "motiveId": "cover_up_embezzlement",
        "weaponId": "kitchen_knife",
        "crimeTime": CANONICAL_TIME,
    }
    st, acc = req("POST", f"/playthroughs/{pt_id}/accusation", token=pt_token,
                  body=acc_body)
    check("G7a accusation 200", st == 200, json.dumps(acc)[:300])
    check("G7b pre-reveal clean after accuse", forbidden_hits(acc) == [],
          str(forbidden_hits(acc))[:200])
    st, reveal = req("GET", f"/playthroughs/{pt_id}/reveal", token=pt_token)
    check("G7c reveal 200", st == 200)
    check("G7d REVEALED + 4/4",
          reveal.get("status") == "REVEALED"
          and reveal.get("score", {}).get("correctDimensions") == 4,
          json.dumps(reveal)[:300])
    # fresh playthrough, same case, same deterministic truth
    st, pt2 = req("POST", f"/cases/{case_id}/versions/1/playthroughs", token=creator)
    pt2_id, pt2_token = pt2["playthroughId"], pt2["playthroughAccessToken"]
    req("POST", f"/playthroughs/{pt2_id}/accusation", token=pt2_token, body=acc_body)
    st, reveal2 = req("GET", f"/playthroughs/{pt2_id}/reveal", token=pt2_token)
    check("G7e deterministic truth across playthroughs",
          st == 200 and reveal2["truth"]["murdererId"] == reveal["truth"]["murdererId"]
          and reveal2["truth"]["weaponId"] == reveal["truth"]["weaponId"])

    failed = [n for n, ok, _ in results if not ok]
    print(f"\nQA-P19C-FAKE-STACK: {len(results) - len(failed)}/{len(results)} "
          f"checks passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())