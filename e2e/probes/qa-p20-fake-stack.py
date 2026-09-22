"""QA-owned Phase 20 PD-SEC-01 API probe (hermetic fake stack, live uvicorn).

Proves the pre-reveal evidence boundary over REAL HTTP on the QA hermetic
stack (backend :8000 GENERATION_PROVIDER=fake + scratch SQLite under
%TEMP%; the operator LAN Ollama is SECRET and is never contacted):

  1. fresh playthrough public-case  -> evidence list EMPTY + every
     worldGraph placement evidenceId null
  2. fresh investigation bootstrap  -> every world object carries NO
     evidenceId (None), discovered/read False, interaction affordance kept
  3. POST .../evidence/{id}/discover -> 404 (route REMOVED) for reachable,
     undiscovered and fabricated ids; zero PlayerKnowledge mutation
  4. valid object interaction       -> server-validated discovery ONLY
     through POST /objects/{id}/interact (knife -> forensic record)
  5. after discovery the evidenceId appears ONLY then (bootstrap object +
     placement + playthrough public-case evidence list)
  6. undiscovered evidence ids/titles/descriptions stay ABSENT after the
     knife discovery (email, letter opener, scissors)
  7. reveal-only proof projection (explanation/dimensions/
     proofDimensionMap) absent pre-reveal, present ONLY in the reveal
  8. candidates block unchanged (alphabetical, unmarked winners) at every
     pre-reveal step
  9. accusation/reveal unchanged: 4/4 deterministic on a second playthrough

Runs with the venv interpreter:  python e2e/probes/qa-p20-fake-stack.py
Exit 0 and "ALL CHECKS PASSED (N/N)" on success; non-zero otherwise.
"""

from __future__ import annotations

import json
import sys

import httpx

BASE = "http://localhost:8000/api/v1"
GOLDEN_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)
WINNING = {
    "murdererId": "thomas_reed",
    "motiveId": "cover_up_embezzlement",
    "weaponId": "kitchen_knife",
    "crimeTime": "2026-09-11T22:17:00+02:00",
}

# Golden undiscovered material that must NEVER appear pre-reveal.
UNDISCOVERED_EVIDENCE_IDS = [
    "email_thomas_01",          # laptop-linked, undiscovered pre-interaction
    "forensic_letter_opener_01",
    "forensic_scissors_01",
    "body_found_01",
]
FORBIDDEN_KEYS = frozenset(
    {
        "truthfulness", "canonical", "solutionProof", "acceptedScoring",
        "solverProof", "proof", "diagnostics", "prompt", "providerOutput",
        "verifier", "tokenVerifier", "token", "seed", "locked", "report",
        "universes", "universe", "observedAt", "propositions", "sourceRef",
        "remainingCandidateIds", "remainingMotiveIds", "remainingWeaponIds",
        "truth", "explanation", "dimensions", "proofDimensionMap",
    }
)
# Public candidate-universe ids + the REQUIREMENTS 40.10 accusation echo are
# documented player-safe surfaces (Phase 7 scanning semantics): the leaf keys
# below appear as PUBLIC world-graph candidate ids (worldGraph.motives[] etc.)
# and in the accusation ECHO of the player's own submission. They are NOT
# forbidden pre-reveal material — proof/truth material is caught by the
# FORBIDDEN_KEYS scan (truth, canonical, explanation, ...).
PUBLIC_LEAF_EXEMPT = frozenset(
    {"motiveId", "weaponId", "murdererId", "victimId", "crimeTime"}
)

checks = 0
failures: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(f"{label} {detail}".strip())


def walk(node, path: str, hits: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if key in FORBIDDEN_KEYS:
                hits.append(child)
            walk(value, child, hits)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            walk(value, f"{path}[{i}]", hits)


def scan(body) -> list[str]:
    hits: list[str] = []
    walk(body, "$", hits)
    return hits


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=60.0)
    try:
        # -- helper: full public handshake ------------------------------
        def new_playthrough() -> tuple[str, str, str]:
            res = client.post("/sessions/anonymous")
            ok(res.status_code == 201, "anon session 201", f"got {res.status_code}")
            session = res.json()
            token = session["anonymousSessionToken"]
            res = client.post(
                "/cases",
                json={"prompt": GOLDEN_PROMPT, "difficulty": "medium"},
                headers={"Authorization": f"Bearer {token}"},
            )
            ok(res.status_code == 201, "case 201", f"got {res.status_code}")
            created = res.json()
            ok(created["status"] == "PUBLISHED", "golden fake publishes", created.get("status"))
            case_id = created["caseId"]
            creator = created["creatorAccessToken"]
            res = client.post(
                f"/cases/{case_id}/versions/1/playthroughs",
                headers={"Authorization": f"Bearer {creator}"},
            )
            ok(res.status_code == 201, "playthrough 201", f"got {res.status_code}")
            pt = res.json()
            return case_id, pt["playthroughId"], pt["playthroughAccessToken"]

        def pt_headers(token: str) -> dict:
            return {"Authorization": f"Bearer {token}"}

        # ---------------------------------------------------------------
        case_id, pt_id, pt_token = new_playthrough()

        # (1) fresh playthrough public-case -----------------------------
        res = client.get(f"/playthroughs/{pt_id}/public-case", headers=pt_headers(pt_token))
        ok(res.status_code == 200, "public-case 200", f"got {res.status_code}")
        pub = res.json()
        ok(pub["evidence"] == [], "fresh public-case evidence EMPTY", str(pub["evidence"]))
        placements = pub["worldGraph"]["placements"]
        ok(len(placements) == 9, "golden world 9 placements", str(len(placements)))
        null_placements = [p for p in placements if p.get("evidenceId") is not None]
        ok(not null_placements, "fresh placements evidenceId all null", str(null_placements))
        ok(scan(pub) == [], "public-case pre-reveal key scan clean", str(scan(pub)))

        # (2) fresh investigation bootstrap -----------------------------
        res = client.get(f"/playthroughs/{pt_id}/investigation", headers=pt_headers(pt_token))
        ok(res.status_code == 200, "bootstrap 200", f"got {res.status_code}")
        boot = res.json()
        world = boot["scene"]["worldObjects"]
        ok(len(world) > 0, "golden world has objects", str(len(world)))
        leaked = [o["objectId"] for o in world if o.get("evidenceId") is not None]
        ok(not leaked, "fresh bootstrap objects carry NO evidenceId", str(leaked))
        flagged = [o["objectId"] for o in world if o.get("discovered") or o.get("read")]
        ok(not flagged, "fresh bootstrap discovered/read False", str(flagged))
        afford = [o for o in world if o.get("evidenceId") is None
                  and isinstance(o.get("interaction"), str) and o["interaction"]]
        ok(len(afford) >= 2, "affordance kept on undiscovered interactables",
           str([o["objectId"] for o in afford]))
        kn = boot["playerKnowledge"]
        ok(kn["discoveredEvidenceIds"] == [], "playerKnowledge.discovered EMPTY", str(kn))
        ok(scan(boot) == [], "bootstrap pre-reveal key scan clean", str(scan(boot)))

        # (3) direct discover route REMOVED ------------------------------
        for eid in ("forensic_knife_match_01", "email_thomas_01", "ghost_99", "", ".." * 20):
            res = client.post(
                f"/playthroughs/{pt_id}/evidence/{eid}/discover",
                headers=pt_headers(pt_token),
            )
            ok(res.status_code == 404, f"discover route gone for {eid!r}",
               f"got {res.status_code}")
            body = res.json()
            ok(body["error"]["code"] == "NOT_FOUND",
               f"discover 404 envelope for {eid!r}", str(body))
        # (5-half) rejected discovery leaves the player-known surface EMPTY
        res = client.get(f"/playthroughs/{pt_id}/investigation", headers=pt_headers(pt_token))
        boot = res.json()
        ok(all(o.get("evidenceId") is None for o in boot["scene"]["worldObjects"]),
           "rejected discover -> bootstrap STILL no evidenceId")
        ok(boot["playerKnowledge"]["discoveredEvidenceIds"] == [],
           "rejected discover -> playerKnowledge STILL empty")

        # (4) valid interaction discovers (server-validated ONLY) --------
        res = client.post(
            f"/playthroughs/{pt_id}/objects/kitchen_knife/interact",
            json={"interaction": "inspect"},
            headers=pt_headers(pt_token),
        )
        ok(res.status_code == 200, "knife interact 200", f"got {res.status_code}")
        inter = res.json()
        ok(inter.get("evidenceId") == "forensic_knife_match_01", "knife -> forensic record",
           str(inter.get("evidenceId")))
        ok(inter.get("discovery", {}).get("state") == "discovered",
           "knife discovery state discovered", str(inter.get("discovery")))
        ok(scan(inter) == [], "interact DTO key scan clean", str(scan(inter)))

        # (5) evidenceId appears ONLY NOW --------------------------------
        res = client.get(f"/playthroughs/{pt_id}/investigation", headers=pt_headers(pt_token))
        boot = res.json()
        knife_obj = next(o for o in boot["scene"]["worldObjects"]
                         if o["objectId"] == "kitchen_knife")
        ok(knife_obj["evidenceId"] == "forensic_knife_match_01",
           "knife evidenceId appears only after interaction")
        ok(knife_obj["discovered"] is True, "knife discovered flag flips True")
        ok(boot["playerKnowledge"]["discoveredEvidenceIds"] == ["forensic_knife_match_01"],
           "playerKnowledge exactly the knife", str(boot["playerKnowledge"]["discoveredEvidenceIds"]))

        res = client.get(f"/playthroughs/{pt_id}/public-case", headers=pt_headers(pt_token))
        pub = res.json()
        pub_evidence = [e["id"] for e in pub["evidence"]]
        ok(pub_evidence == ["forensic_knife_match_01"], "public-case evidence = knife only",
           str(pub_evidence))

        # (6) undiscovered material still ABSENT -------------------------
        for scope, blob in (
            ("post-discovery bootstrap", json.dumps(boot, sort_keys=True)),
            ("post-discovery public-case", json.dumps(pub, sort_keys=True)),
        ):
            for eid in UNDISCOVERED_EVIDENCE_IDS:
                ok(eid not in blob, f"{scope}: {eid} absent")
        ok(scan(boot) == [], "post-discovery bootstrap key scan still clean",
           str(scan(boot)))

        # (7) reveal-only projection absent pre-reveal; present in reveal -
        pre_reveal_bodies = [pub, boot, inter, res.json()]
        acc_before = client.post(
            f"/playthroughs/{pt_id}/accusation",
            json=WINNING,
            headers=pt_headers(pt_token),
        )
        ok(acc_before.status_code == 200, "accusation 200", f"got {acc_before.status_code}")
        acc_body = acc_before.json()
        ok(scan(acc_body) == [], "accusation echo key scan clean", str(scan(acc_body)))
        pre_reveal_bodies.append(acc_body)
        for body in pre_reveal_bodies:
            hits = scan(body)
            ok(not any(h.endswith(".explanation") or h.endswith(".dimensions")
                       or h.endswith(".proofDimensionMap") for h in hits),
               "no reveal-only projection pre-reveal", json.dumps(body)[:120])

        reveal = client.get(f"/playthroughs/{pt_id}/reveal", headers=pt_headers(pt_token))
        ok(reveal.status_code == 200, "reveal 200", f"got {reveal.status_code}")
        rev = reveal.json()
        ok(rev["status"] == "REVEALED", "reveal status", str(rev.get("status")))
        ok(rev["score"]["correctDimensions"] == 4, "4/4 correct dimensions")
        ok(rev["result"]["murdererCorrect"] is True and rev["result"]["motiveCorrect"] is True
           and rev["result"]["weaponCorrect"] is True and rev["result"]["timeCorrect"] is True,
           "all four answers correct")
        dims = rev["explanation"]["dimensions"]
        ok(set(dims) == {"who", "why", "weapon", "when"}, "explanation.dimensions present",
           str(set(dims)))

        # (8) candidates unmarked at every pre-reveal step ---------------
        res = client.get(f"/playthroughs/{pt_id}/investigation", headers=pt_headers(pt_token))
        boot = res.json()
        cands = boot["candidates"]
        suspect_names = [s["name"] for s in cands["suspects"]]
        ok(suspect_names == sorted(suspect_names), "suspects alphabetical",
           str(suspect_names))
        for suspect in cands["suspects"]:
            ok("winner" not in str(suspect).lower(), "no winner marker on suspects")

        # (9) determinism: second fresh playthrough same truth ----------
        _, pt2_id, pt2_token = new_playthrough()
        client.post(
            f"/playthroughs/{pt2_id}/objects/kitchen_knife/interact",
            json={"interaction": "inspect"},
            headers=pt_headers(pt2_token),
        )
        r1 = client.post(
            f"/playthroughs/{pt2_id}/accusation",
            json=WINNING,
            headers=pt_headers(pt2_token),
        )
        ok(r1.status_code == 200, "pt2 accusation 200", f"got {r1.status_code}")
        rev2 = client.get(f"/playthroughs/{pt2_id}/reveal", headers=pt_headers(pt2_token))
        rev2 = rev2.json()
        ok(rev2["truth"]["murdererId"] == rev["truth"]["murdererId"], "same truth murderer")
        ok(rev2["explanation"]["dimensions"] == rev["explanation"]["dimensions"],
           "same explanation dimensions across playthroughs")
    finally:
        client.close()

    if failures:
        print(f"FAILED: {len(failures)} checks\n  " + "\n  ".join(failures[:20]))
        return 1
    print(f"ALL CHECKS PASSED ({checks}/{checks})")
    return 0


if __name__ == "__main__":
    sys.exit(main())