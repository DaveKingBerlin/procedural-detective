"""QA-owned Phase 20 PD-SEC-02 live abuse-control probe (hermetic fake stack).

Drives REAL uvicorn instances over real HTTP (no TestClient) to prove the
public-admission abuse controls exactly as an attacker would see them:

  Backend R1 (:8010)  TRUST_PROXY=false  ANON per-IP/10min=4
                                        GENERATION per-IP/hour=2
  Backend R2 (:8011)  TRUST_PROXY=true   ANON per-IP/10min=2 (forwarded identity)
  Backend R3 (:8012)  TRUST_PROXY=false  ANON per-IP/10min=200 (generation-cap
                                        probe: GENERATION per-IP/hour=2)

Probes:
  1. mint > per-IP anonymous sessions from one peer -> 429 TOO_MANY_REQUESTS
     with a sanitized envelope (no internals: no per_ip/global/window/limiter/
     quota/reservation markers, no stack/URL/path).
  2. spoofed X-Forwarded-For must NOT change the rate-limit identity when
     TRUST_PROXY=false (peer-only): after the bucket is spent, ANY forwarded
     value still answers 429.
  3. hostile XFF cannot bypass the generation per-IP cap either (trust off).
  4. TRUST_PROXY=true honors the LEFT-MOST forwarded identity: identity
     A spends its window, identity B is unaffected, missing header falls
     back to the socket peer.
  5. failureCode/production surfaces stay sanitized on 429s and envelopes.

Rolling-window/time-advance/concurrency determinism are covered by the
shipped ManualClock unit suite (backend/tests/
test_phase20_security_ratelimit.py) re-run independently; this probe is the
live-wire counterpart.

Run:  python e2e/probes/qa-p20-ratelimit.py   (exit 0 = all passed)
"""

from __future__ import annotations

import sys

import httpx

R1 = "http://localhost:8010/api/v1"
R2 = "http://localhost:8011/api/v1"
R3 = "http://localhost:8012/api/v1"
GOLDEN_PROMPT = (
    "Victim: sarah_miller\nMurderer: thomas_reed\nMotive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\nTime: 2026-09-11T22:17:00+02:00\nWitness: emily_reed\n"
)
FORBIDDEN_INTERNALS = (
    "per_ip", "global", "window", "limiter", "quota", "reservation",
    "Traceback", "stack", "127.0.0.1", "localhost", "sqlite", "/api/",
)

checks = 0
failures: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(f"{label} {detail}".strip())


def assert_sanitized(text: str, label: str) -> None:
    low = text.lower()
    for marker in FORBIDDEN_INTERNALS:
        ok(marker not in low, f"{label}: no {marker!r} in body", text[:200])


def main() -> int:
    r1 = httpx.Client(base_url=R1, timeout=60.0)
    r2 = httpx.Client(base_url=R2, timeout=60.0)
    try:
        # ---- R1 trust OFF --------------------------------------------
        # 1a. anonymous session per-IP window (limit 4)
        statuses = [r1.post("/sessions/anonymous").status_code for _ in range(5)]
        ok(statuses == [201, 201, 201, 201, 429], "R1 anon 5th -> 429", str(statuses))
        denied = r1.post("/sessions/anonymous")
        body = denied.json()
        ok(denied.status_code == 429, "R1 anon 6th -> 429", str(denied.status_code))
        ok(body["error"]["code"] == "TOO_MANY_REQUESTS", "R1 429 code", str(body))
        assert_sanitized(denied.text, "R1 429 sanitized")
        ok(denied.headers.get("cache-control") == "no-store", "R1 429 no-store",
           str(denied.headers.get("cache-control")))

        # 2. XFF spoof must NOT change identity when trust is OFF
        for xff in ("203.0.113.66", "198.51.100.99"):
            res = r1.post("/sessions/anonymous", headers={"X-Forwarded-For": xff})
            ok(res.status_code == 429, f"R1 XFF {xff} still 429 (peer-only)",
               str(res.status_code))

        # 3. GENERATION per-IP cap (R3): two publishes, then the third
        #    generation attempt is 429 even with a FRESH session, and a
        #    hostile X-Forwarded-For cannot bypass it (trust OFF).
        r3 = httpx.Client(base_url=R3, timeout=60.0)
        try:
            tokens = []
            for _ in range(4):
                tokens.append(r3.post("/sessions/anonymous").json()["anonymousSessionToken"])
            results = []
            for index in range(4):
                headers = {"Authorization": f"Bearer {tokens[index]}"}
                if index == 3:  # hostile XFF on the fourth attempt
                    headers["X-Forwarded-For"] = "203.0.113.66"
                results.append(
                    r3.post("/cases", json={"prompt": GOLDEN_PROMPT}, headers=headers)
                )
            codes = [r.status_code for r in results]
            ok(codes == [201, 201, 429, 429], "R3 gen caps: 201,201,429,429", str(codes))
            ok(results[0].json()["status"] == "PUBLISHED", "R3 g1 PUBLISHED")
            ok(results[1].json()["status"] == "PUBLISHED", "R3 g2 PUBLISHED")
            ok(results[2].json()["error"]["code"] == "TOO_MANY_REQUESTS",
               "R3 gen 3rd -> TOO_MANY_REQUESTS", str(results[2].json()))
            assert_sanitized(results[2].text, "R3 gen 429 sanitized")
            ok(results[3].status_code == 429,
               "R3 gen 4th (fresh session + hostile XFF) STILL 429",
               str(results[3].status_code))
        finally:
            r3.close()

        # ---- R2 trust ON ----------------------------------------------
        # 4. forwarded identity honored; second identity unaffected
        a1 = r2.post("/sessions/anonymous", headers={"X-Forwarded-For": "198.51.100.1"})
        a2 = r2.post("/sessions/anonymous", headers={"X-Forwarded-For": "198.51.100.1"})
        ok(a1.status_code == 201 and a2.status_code == 201,
           "R2 identity A: 2x201", f"{a1.status_code},{a2.status_code}")
        a3 = r2.post("/sessions/anonymous", headers={"X-Forwarded-For": "198.51.100.1"})
        ok(a3.status_code == 429, "R2 identity A: 3rd -> 429", str(a3.status_code))
        b1 = r2.post("/sessions/anonymous", headers={"X-Forwarded-For": "198.51.100.2"})
        ok(b1.status_code == 201, "R2 identity B unaffected by A's quota",
           str(b1.status_code))
        chain = r2.post(
            "/sessions/anonymous",
            headers={"X-Forwarded-For": "198.51.100.9, 10.0.0.5"},
        )
        ok(chain.status_code == 201, "R2 chain uses LEFT-MOST (198.51.100.9)",
           str(chain.status_code))
        chain2 = r2.post(
            "/sessions/anonymous",
            headers={"X-Forwarded-For": "198.51.100.9, 10.0.0.5"},
        )
        ok(chain2.status_code == 429, "R2 chain 198.51.100.9 spent",
           str(chain2.status_code))
        bare = r2.post("/sessions/anonymous")  # no header -> socket peer
        ok(bare.status_code == 201, "R2 missing header -> peer identity",
           str(bare.status_code))
        # the 429 has no internals either
        assert_sanitized(a3.text, "R2 429 sanitized")

        # 5. R1 404-on-route-gone stays sanitized (cross-check envelope shape)
        gone = r1.post(
            "/playthroughs/nope/evidence/x/discover",
            headers={"X-Forwarded-For": "203.0.113.66"},
        )
        ok(gone.status_code == 404, "R1 removed discover route 404", str(gone.status_code))
        assert_sanitized(gone.text, "R1 404 sanitized")

        # 6. normal flow usable on the MAIN stack (generous defaults) — via
        #    the shared base URL over the :8000 backend.
        main = httpx.Client(base_url="http://localhost:8000/api/v1", timeout=60.0)
        try:
            for difficulty in ("easy", "medium", "hard"):
                token = main.post("/sessions/anonymous").json()["anonymousSessionToken"]
                res = main.post(
                    "/cases",
                    json={"prompt": GOLDEN_PROMPT, "difficulty": difficulty},
                    headers={"Authorization": f"Bearer {token}"},
                )
                ok(res.status_code == 201 and res.json()["status"] == "PUBLISHED",
                   f"main {difficulty} publishes under default limits",
                   f"{res.status_code} {res.text[:120]}")
        finally:
            main.close()
    finally:
        r1.close()
        r2.close()

    if failures:
        print(f"FAILED: {len(failures)} checks\n  " + "\n  ".join(failures[:20]))
        return 1
    print(f"ALL CHECKS PASSED ({checks}/{checks})")
    return 0


if __name__ == "__main__":
    sys.exit(main())