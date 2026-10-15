# Phase 43-Fix1 / DEF-074A — QA evidence report

Independent QA for the ASGI receive-semantics fix in
`backend/app/main.py` `RequestBodySizeMiddleware.replayed_receive`.
Branch `phase43-fix1/request-body-receive-semantics`, baseline `main` `597f28d`.
QA altered no product source; this directory is QA-owned evidence.

## Fix under test

```python
async def replayed_receive():
    if messages:
        return messages.popleft()
    return await receive()
```

## 1. Full backend suite (independent)

```
python -m pytest --basetemp="C:\Temp\procedural-detective-pytest-qa-fix1" -p no:cacheprovider tests
1 failed, 3302 passed, 1 skipped, 107 warnings in 1104.79s (0:18:24)
```

- Sole failure: `tests/test_phase21b_demo_truthfulness.py::test_ollama_probe_hang_dto_via_bounded_seam`
  (`assert elapsed < 1.0` -> `2.687`). Pre-existing: independently reproduced on a
  `597f28d` worktree (`assert 3.235 < 1.0`; 1 failed in 5.58s). Test file last
  touched `c57778f` (Phase 25).
- Sole skip: `tests/test_phase21c_backup_production.py:201` (Windows-only POSIX skip).
- `REQUIREMENTS.md` sha256 `ba3dfec2ca08069bef21137286def9e6e9c45aaab80cdea424400c522a909678` (unchanged).

## 2. Independent probe (`independent_probe.py`)

7 checks; run against fixed tree and baseline `597f28d` via `PD_BACKEND`:

- FIXED: ALL 7 PASS (2nd receive == real `http.disconnect`; listener stops after
  2 receives; heartbeat alive; chunked/oversized/CL/JSON invariants green).
- BASELINE `597f28d`: 5 checks FAIL (2nd receive == synthetic `http.request`;
  listener hit the 200,000 cap and never saw a disconnect).
- Developer test file: 5 passed fixed; 2 failed + 3 passed baseline.
- Focused fix1 + `test_phase5_defect_regressions.py` (DE-048 suite): 27 passed.

## 3. Production-topology acceptance (HTTPS/Caddy) — PASS

Command:
`docker compose -f docker-compose.prod.yml -f docker-compose.lan.yml -f docker-compose.debug.yml up --build -d`
(`CADDY_DOMAIN=enshrouded-server`; Caddy container not recreated -> TLS/volumes preserved).

PRE-FIX live (old image):
- `docker stats` backend CPU **100.04%**; container **unhealthy, FailingStreak 145**.
- `curl -k https://enshrouded-server/api/v1/health` -> `000` after 8.007 s.

POST-FIX live:
- deployed `replayed_receive` confirmed fixed; backend `healthy`; CPU **0.15%**
  (Caddy 0.04%); `failingStreak=0`.
- `acceptance-evidence.json` (Playwright, cold context, local `ignoreHTTPSErrors`):
  - `/health` 200 / 47 ms; session 201; case 201 PUBLISHED; playthrough 201;
  - GET investigation 200 / 36 ms (9 objects, 1 witness);
  - POST `{"interaction":""}` on `apartment_door` 200 / 31 ms (neutral inspection);
  - witness interview 200 / 35 ms (grounded statement + discovery);
  - 8 unique lazy `/assets/*` FileResponses, all 200; 0 failed/pending requests;
  - 21/21 concurrent `/health` samples 200 (min 5 / median 21 / max 139 ms);
  - 0 page console errors.

## 4. Root-cause verification on the DEPLOYED starlette

- dev tree: starlette 1.6.0 (`FileResponse.__call__` has no `await receive()`).
- deployed image: starlette 1.8.0 (`FileResponse.__call__` runs a
  `while True: await receive()` disconnect loop for ASGI spec_version < 2.4;
  uvicorn emits `2.3`).
- `container_realfile_probe.py` against the real deployed FileResponse:
  pre-fix **200,001** receive calls, disconnect never seen, heartbeat 338
  (starved); fixed **2** calls, real disconnect, 0.0034 s.

This refutes adversary `ADV-43FIX1-01` for the production topology (it reviewed
the dev-tree 1.6.0). Residual dev/prod dependency divergence filed as INFO
`DEF-074B`.

## Files

- `independent_probe.py` — QA ASGI probe (fixed vs baseline).
- `container_realfile_probe.py` — real deployed FileResponse pre/post fix.
- `acceptance.cjs` — Playwright HTTPS/Caddy acceptance over the Dev-Box LAN TLS.
- `acceptance-evidence.json` — machine-readable acceptance record.
- `spa-scene.png` — cold-context SPA screenshot.