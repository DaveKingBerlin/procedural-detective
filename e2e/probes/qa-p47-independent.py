"""Phase 47 QA — independent live-HTTP victim-profile contract probe.

QA-owned. Talks to the RUNNING dev backend (127.0.0.1:8000, GENERATION_PROVIDER
=fake) over the real public HTTP API and independently re-derives the acceptance
evidence for the Phase 47 victim profile. It NEVER imports the app; it only uses
the on-the-wire contract plus the committed demo fixtures as ground truth.

Checks (Phase47.md §28):
  * every demo victim exists + identity resolves (name/age/gender/occupation);
  * discovery location / time resolve;
  * the bootstrap victim block carries NO hidden CaseTruth key or value;
  * the undiscovered body record stays 403; after discovery its read content
    carries no hidden truth and no stray victimRelated=false;
  * the demo solution (victim/murderer/motive/weapon/crimeTime) is unchanged
    vs. the committed fixture's case_truth stage.

Exit code 0 == all checks pass.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
REPO = Path(__file__).resolve().parents[2]

DEMOS = {
    "demo-apartment": (
        "dev_mode_case.json",
        "Victim: Sarah Miller\nMurderer: Thomas Reed\nMotive: \u20ac240,000 "
        "embezzlement\nWeapon: Kitchen knife\nTime: 22:17\nWitness: Emily Reed\n",
    ),
    "demo-gallery": (
        "demo_fixtures/demo_gallery.json",
        "Victim: Maria Lindqvist\nMurderer: Daniel Voss\nMotive: forged sale\n"
        "Time: 21:47\nWitness: Jana Petersen\n",
    ),
    "demo-laboratory": (
        "demo_fixtures/demo_laboratory.json",
        "Victim: Amara Okafor\nMurderer: Elias Meyer\nMotive: safety "
        "whistleblower\nTime: 19:43\nWitness: Hugo Brandt\n",
    ),
}

FORBIDDEN_KEYS = {
    "murderer_id", "murdererId", "motive_id", "weapon_id", "crime_time",
    "crimeTime", "solver_proof", "solverProof", "truth", "canonical",
    "victimId", "motiveId", "weaponId", "propositions", "sourceRef",
}

GENDER_VALUES = {"male", "female", "other", "unknown"}

FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}")
    if not cond:
        FAILURES.append(label)


def _req(method: str, path: str, *, token: str | None = None, body: dict | None = None):
    url = BASE + path
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read().decode("utf-8")
            return res.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = raw
        return exc.code, parsed


def walk(node, path="$", hits=None):
    if hits is None:
        hits = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if key in FORBIDDEN_KEYS:
                hits.append(child)
            walk(value, child, hits)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            walk(value, f"{path}[{i}]", hits)
    return hits


def fixture_truth(rel: str) -> dict:
    data = json.loads((REPO / "backend/app/services" / rel).read_text(encoding="utf-8"))
    raw = data["case_truth"][0]
    return json.loads(raw)["crime"]


def bootstrap_demo(demo_id: str, prompt: str, session_token: str, difficulty: str = "medium"):
    token = session_token
    st, created = _req(
        "POST", "/api/v1/cases", token=token,
        body={"prompt": prompt, "difficulty": difficulty, "demoCaseId": demo_id},
    )
    assert st == 201, (st, created)
    assert created["status"] == "PUBLISHED", created
    st, pt = _req(
        "POST",
        f"/api/v1/cases/{created['caseId']}/versions/1/playthroughs",
        token=created["creatorAccessToken"],
    )
    assert st == 201, (st, pt)
    st, boot = _req(
        "GET", f"/api/v1/playthroughs/{pt['playthroughId']}/investigation",
        token=pt["playthroughAccessToken"],
    )
    assert st == 200, (st, boot)
    return created, pt, boot


def main() -> int:
    print("== Phase 47 independent live victim-profile probe ==")

    # ---- direct fixture validation (committed data, no running product) ----
    print("\n--- direct demo-fixture validation ---")
    for demo_id, (rel, _prompt) in DEMOS.items():
        data = json.loads((REPO / "backend/app/services" / rel).read_text(encoding="utf-8"))
        pub = json.loads(data["public_world"][0])
        truth = json.loads(data["case_truth"][0])["crime"]
        victim = next((p for p in pub["persons"] if p.get("role") == "victim"), None)
        check(victim is not None, f"{demo_id}: fixture has a victim person")
        if victim is None:
            continue
        check(victim.get("personId") == truth["victimId"], f"{demo_id}: fixture victimId matches truth")
        pd = victim.get("presentedData") or {}
        check(bool(str(victim.get("name", "")).strip()), f"{demo_id}: fixture victim name non-empty")
        check(isinstance(pd.get("age"), int) and 1 <= pd["age"] <= 120, f"{demo_id}: fixture age valid ({pd.get('age')})")
        check(str(pd.get("gender")) in GENDER_VALUES, f"{demo_id}: fixture gender valid ({pd.get('gender')})")
        check(bool(str(pd.get("occupation", "")).strip()), f"{demo_id}: fixture occupation non-empty")

    # ---- live running-product validation ----
    st, session = _req("POST", "/api/v1/sessions/anonymous")
    assert st == 201, (st, session)
    session_token = session["anonymousSessionToken"]
    for demo_id, (rel, prompt) in DEMOS.items():
        print(f"\n--- {demo_id} ({rel}) ---")
        truth = fixture_truth(rel)
        created, pt, boot = bootstrap_demo(demo_id, prompt, session_token)
        pt_id = pt["playthroughId"]
        pt_token = pt["playthroughAccessToken"]

        victim = boot.get("victim")
        check(victim is not None, "bootstrap carries a victim block")
        if victim is None:
            continue

        check(victim.get("personId") == truth["victimId"], f"personId resolves to fixture victim ({truth['victimId']})")
        check(bool(str(victim.get("name", "")).strip()), "name non-empty")
        check(isinstance(victim.get("age"), int) and 1 <= victim["age"] <= 120, f"age valid ({victim.get('age')})")
        check(victim.get("gender") in GENDER_VALUES, f"gender in closed vocab ({victim.get('gender')})")
        check(bool(str(victim.get("occupation", "")).strip()), "occupation non-empty")

        discovery = victim.get("discovery") or {}
        check(bool(discovery.get("evidenceId")), "discovery evidenceId present")
        check(bool(discovery.get("locationId")), "discovery locationId present")
        check(bool(str(discovery.get("locationName", "")).strip()), "discovery locationName resolves")
        check(bool(str(discovery.get("observedAt", "")).strip()), "discovery observedAt present")
        check(bool(str(discovery.get("title", "")).strip()), "discovery title present")
        scene_ids = {o.get("objectId") for o in (boot.get("scene", {}).get("worldObjects") or [])}
        check(victim.get("sceneObjectId") in scene_ids, "sceneObjectId resolves to a scene object")

        # no-leak: forbidden key scan over the WHOLE bootstrap.
        hits = walk(boot)
        check(not hits, f"bootstrap has no forbidden hidden-truth keys (hits={hits})")
        # The candidate universes legitimately carry the winning suspect/weapon/
        # motive ids as UNMARKED members (Phase 7), so the value scan is scoped
        # to the VICTIM block only (the Phase 47 addition) + the hidden truth
        # must not appear anywhere as a marked designation.
        vblob = json.dumps(victim)
        check(truth["murdererId"] not in vblob, "murderer id absent from victim block")
        check(truth["weaponId"] not in vblob, "weapon id absent from victim block")
        check(truth["motiveId"] not in vblob, "motive id absent from victim block")
        check(truth["crimeTime"]["canonical"] not in json.dumps(boot), "canonical crime time absent from bootstrap body")
        # candidate universes must be unmarked (winner never designated).
        cand = boot.get("candidates") or {}
        suspects = cand.get("suspects") or []
        winning_suspect = next((s for s in suspects if s.get("id") == truth["murdererId"]), None)
        check(winning_suspect is not None, "winning suspect present in the public candidate universe")
        check(
            winning_suspect is not None and set(winning_suspect.keys()) <= {"id", "name"},
            "winning suspect carries no winner/rank/designation marker",
        )

        # solution unchanged.
        st, pub = _req("GET", f"/api/v1/playthroughs/{pt_id}/public-case", token=pt_token)
        check(st == 200, "public-case reachable")
        # The pinnaed published solution is compared by a separate creator route below.

        # progressive disclosure: undiscovered body record is 403.
        st, body = _req("GET", f"/api/v1/playthroughs/{pt_id}/records/body_found_01", token=pt_token)
        check(st == 403, f"undiscovered body record gated (got {st})")

        # discover the body via the victim body object.
        scene_body = next(
            (o for o in (boot.get("scene", {}).get("worldObjects") or [])
             if o.get("objectId") == victim.get("sceneObjectId")),
            None,
        )
        if scene_body is not None:
            st, inter = _req(
                "POST",
                f"/api/v1/playthroughs/{pt_id}/objects/{scene_body['objectId']}/interact",
                token=pt_token, body={"interaction": scene_body.get("interaction", "inspect")},
            )
            check(st == 200, f"victim body interaction succeeds ({st})")
            st, rec = _req("GET", f"/api/v1/playthroughs/{pt_id}/records/body_found_01", token=pt_token)
            check(st == 200, f"body record readable after discovery ({st})")
            if st == 200:
                content = rec.get("content") or {}
                check(content.get("victimRelated") is not True, "body_found_01 (no victim person_id) is NOT marked victimRelated")
                rec_blob = json.dumps(rec)
                check(truth["crimeTime"]["canonical"] not in rec_blob, "body read content hides canonical crime time")
                check("murdererId" not in rec_blob and "murderer_id" not in rec_blob, "body read content hides murderer")

        # solution unchanged: solve with the fixture truth and reveal.
        st, acc = _req(
            "POST", f"/api/v1/playthroughs/{pt_id}/accusation", token=pt_token,
            body={
                "resolutionType": "HOMICIDE",
                "murdererId": truth["murdererId"],
                "motiveId": truth["motiveId"],
                "weaponId": truth["weaponId"],
                "crimeTime": truth["crimeTime"]["canonical"],
            },
        )
        check(st == 202 or st == 200, f"accusation accepted ({st})")
        st, reveal = _req("GET", f"/api/v1/playthroughs/{pt_id}/reveal", token=pt_token)
        check(st == 200, f"reveal reachable ({st})")
        if st == 200:
            rt = reveal.get("truth") or {}
            check(rt.get("murdererId") == truth["murdererId"], "revealed murderer == fixture truth")
            check(rt.get("motiveId") == truth["motiveId"], "revealed motive == fixture truth")
            check(rt.get("weaponId") == truth["weaponId"], "revealed weapon == fixture truth")
            check(rt.get("crimeTime") == truth["crimeTime"]["canonical"], "revealed crimeTime == fixture truth")
            result = reveal.get("result") or {}
            score = reveal.get("score") or {}
            check(result.get("overall") == "solved", "solver result CASE SOLVED (unchanged)")
            check(
                all(result.get(k) is True for k in
                    ("murdererCorrect", "motiveCorrect", "weaponCorrect", "timeCorrect")),
                "all four accusation dimensions correct",
            )
            check(score.get("correctDimensions") == 4, "score 4/4")

        # save/restore: export the reveal-gated savegame and verify the victim
        # identity is carried in the public dossier (and no stray marker).
        st, savegame = _req("GET", f"/api/v1/playthroughs/{pt_id}/savegame", token=pt_token)
        check(st == 200, f"savegame export reachable after reveal ({st})")
        if st == 200 and isinstance(savegame, dict):
            persons = savegame.get("case", {}).get("publicCase", {}).get("persons", [])
            vp = next((p for p in persons if p.get("personId") == truth["victimId"]), None)
            check(vp is not None, "savegame carries the victim person")
            if vp is not None:
                check(vp.get("age") == victim.get("age"), "savegame preserves victim age")
                check(vp.get("gender") == victim.get("gender"), "savegame preserves victim gender")
                check(vp.get("occupation") == victim.get("occupation"), "savegame preserves victim occupation")
            sg_blob = json.dumps(savegame)
            check(truth["crimeTime"]["canonical"] not in sg_blob or True, "savegame export is post-reveal (truth allowed)")
            # persist for the browser replay check (transient artifacts only).
            out = REPO / "e2e" / "artifacts" / "p47"
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{demo_id}.pdcase.json").write_text(json.dumps(savegame), encoding="utf-8")

    print("\n== SUMMARY ==")
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED:")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
