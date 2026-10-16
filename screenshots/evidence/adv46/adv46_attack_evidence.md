# Phase 46 adversarial evidence (hermetic)

Role: adversarial reviewer. Branch: `phase46/submission-hardening` (uncommitted).
Environment: Windows, Python 3.12.10, Docker 29.6.1 / Compose v5.2.0.
**Zero network.** Only local, client-side `docker compose ... config --format json`
renders and local file scans. No token value is printed anywhere in this file.

## A. Network boundary — real renders of the shipped chain

`docker compose -f docker-compose.yml -f docker-compose.prod.yml config --format json`
(merged):

```text
caddy:                 ports=[80, 443]
procedural-detective:  ports=[8000]   <-- base dev :8000 inherited (merge is UNSAFE)
```

`docker compose -f docker-compose.prod.yml config --format json` (standalone prod):

```text
caddy:                 ports=[80, 443]
procedural-detective:  ports=None  expose=[8000]   <-- Docker-internal only
```

Script assembly with a PATH shim (`docker` -> records argv):

```text
build-docker.sh dev     -> docker compose -f docker-compose.yml build        rc=0
build-docker.sh netcup  -> docker compose -f docker-compose.prod.yml build   rc=0
build-docker.sh prod    -> docker compose -f docker-compose.prod.yml build   rc=0
start-server.sh netcup  -> docker compose -f docker-compose.prod.yml up -d
                        -> docker compose -f docker-compose.prod.yml ps      rc=0
build-docker.sh bogus   -> usage, rc=2
start-server.sh         -> usage, rc=2      start-server.sh a b -> usage, rc=2
failing `docker` shim   -> up/build failure => rc=3, `ps` NOT printed
```

`-f` beats env/discovery (temp project with `COMPOSE_FILE=base:prod`,
`COMPOSE_PROFILES=evil`, a `docker-compose.override.yml`, and a `.env`):

```text
docker compose -f docker-compose.prod.yml --project-directory . config
  -> only caddy 80/443 + backend expose 8000; NO override file, NO profile service
```

## B. Merge-guard scenarios (`check_prod_merge_effective_config`, real renders)

```text
S1 prod backend ports: ["8000:8000"]      MERGE=FAIL (prod publishes backend host port)
S2 prod NEW service publishing 8000       MERGE=report(0 fails)   PROD-GATE=FAIL (non-caddy port)
S3 prod backend network_mode: host        MERGE=report(0 fails)   PROD-GATE=FAIL (network_mode host)
S4 prod backend service renamed           MERGE=FAIL closed       PROD-GATE=FAIL closed
S6 prod caddy extra 8000:8000             MERGE=FAIL (not exactly 80/443)
S7 prod backend port via ${PD_EXTRA_PORT} MERGE=FAIL (interpolated 8000:8000)
malformed/absent compose                  fail closed / skip
```

S2/S3 are gaps in the dedicated merge guard; `check_prod_effective_config`
(also in `run_all`) fails them, so the overall release gate is not bypassed.

## C. `scan_app_bearer_tokens` evasion matrix (synthetic 43-char token)

```text
FLAGGED  01 key+quoted value same line (canonical JSON)
FLAGGED  14 localStorage.setItem("pd_playthrough_token", "<43>")
FLAGGED  16 YAML  creatorAccessToken: "<43>"
FLAGGED  18 JSON  "creatorAccessToken": "<43>"
evaded   02 "creatorAccessToken":\n  "<43>"   (valid JSON; line-by-line scan)
evaded   03 creatorAccessToken: <43>          (unquoted)
evaded   04 creatorAccessToken=<43>           (unquoted)
evaded   05 lowercase key
evaded   06/07/08 field name not in list (sessionToken / access_token / token)
evaded   09 nested key carrying value
evaded   10 leading space inside the quoted value
evaded   11 quoted value prefixed with "Bearer "
evaded   12 \u-escaped value
evaded   13 localStorage['pd_playthrough_token'] = "<43>"   (bracket access)
evaded   15 value split across two lines
re-serialized with json.dumps (compact or indent=2): shape 02 IS caught
```

## D. Token hygiene / redaction

- 3 redacted acceptance JSONs still parse (`json.load` OK) — redaction did not corrupt JSON.
- Sweep of every tracked file for all 7 original pre-redaction token values
  (extracted from `git show HEAD:<file>`): **0 tracked files contain any original value**.
- `.dockerignore` text-check + a git-ignore simulation confirm `screenshots/`,
  `screenshots/evidence/`, `htmlcov/`, `coverage/`, `.coverage` exclude QA/coverage
  while runtime COPY inputs (assets, backend/app, docker/entrypoint.sh, frontend
  source, compose/fake-worlds) stay in the context.
- The untracked new test `tests/test_phase46_release_guards.py` reconstructs one
  original redacted token via `_fold(partA, partB)` (43 chars, matches an original
  value); the new scan does not flag it. See ADV-46-01.

## E. Release gate / regression (local)

```text
python -m tools.release_check --allow-hosted-placeholders   -> ALL 21 checks OK, exit 0
python -m tools.prod_preflight --allow-local                -> ALL 7 findings OK, exit 0
python -m pytest tests/test_phase46_release_guards.py       -> 40 passed
python -m pytest tests/test_gitlab_ci_lint.py tests/test_generate_adapters.py -> 45 passed, 2 skipped
python -m pytest <phase44/45 playability+world-compose>     -> 127 passed
git diff --check                                             -> rc 0
python tools/generate_adapters.py --check                    -> OK (fingerprint bdceb825721cd46f)
python -m tools.generate_ci_fake_world --check               -> OK
REQUIREMENTS.md SHA-256 = BA3DFEC2...A909678 (unchanged vs Phase 44/45)

ENVIRONMENT-BLOCKED (pre-existing, NOT a Phase 46 regression):
tests/test_phase24a_fp_docker_fixes.py::test_section12_* (2) fail in this
working tree because the operator-local untracked `.env` injects OLLAMA_MODEL /
OLLAMA_BASE_URL into the rendered service env, and a combined root+backend pytest
run additionally has backend/tests/conftest.py export GENERATION_PROVIDER=fake
(shell env wins over --env-file). Neither is touched by Phase 46.
```
