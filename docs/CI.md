# Procedural Detective — GitLab CI/CD & GitLab Runner

Phase 24 adds containerized development, a hermetic GitLab pipeline and an
optional real-Ollama acceptance gate. This document is the operator guide for
the pipeline, the runner and the environment profiles.

> **Normal pipeline is hermetic.** Ordinary pushes and merge requests use
> `GENERATION_PROVIDER=fake` + `ENABLE_BRIDGE=false`; they need NO Ollama
> reachability, NO RTX machine, NO private tunnel (§32).

---

## 1. Pipeline overview

| Stage | Jobs | What they gate |
| --- | --- | --- |
| `validate` | `validate` | `git diff --check`, `tools.release_check`, `tools.catalog_report`, adapter drift, REQUIREMENTS.md sha256, CI fake-world drift |
| `test` | `backend-tests`, `root-tests`, `frontend-tests`, `bridge-tests` | hermetic Python suites + Vitest |
| `build` | `frontend-build` | TypeScript typecheck + production Vite build |
| `docker` | `docker-build` | reproducible image (`docker compose build`) |
| `smoke` | `docker-smoke`, `prod-like-smoke` | rendered-compose gate, deterministic REST smoke, Caddy-edge local smoke |
| `real-ai` | `real-ollama-regression` | operator-triggered real-Ollama acceptance (§33-§36) |

Stages complete **only the real repository commands** from the checkout —
there is no synthetic script, no hidden environment.

The pipeline is defined in `.gitlab-ci.yml`; safe structural linting of that
file runs in the root test suite (`tests/test_gitlab_ci_lint.py`) so a
pipeline shape regression fails hermetically even without GitLab.

## 2. Triggers

`workflow: rules` accepts:

- branch push (`$CI_COMMIT_BRANCH`),
- merge request (`merge_request_event`),
- scheduled pipelines (`schedule`),
- manual web trigger (`web`).

Push+MR duplication is avoided by the single `workflow:` block (no separate
job-level push rules). A merge request pipeline runs all hermetic jobs; the
real-AI job additionally offers the schedule/manual path.

## 3. Runner registration

Use a **dedicated Linux host/VM** (never a shared generic runner) with a
trusted **shell executor** — job code has direct access to the machine, so the
runner must be operator-owned.

```bash
# Register the normal CI runner (tag: docker) — runs EVERY hermetic job,
# including validate (DEF-003: validate is docker-tagged; with
# --run-untagged=false an untagged job would never be picked up).
gitlab-runner register \
  --url https://gitlab.example.com \
  --token <registration-token> \
  --executor shell \
  --tag-list docker \
  --run-untagged=false

# Register the real-AI runner (tag: real-ollama; same trust model)
gitlab-runner register \
  --url https://gitlab.example.com \
  --token <registration-token> \
  --executor shell \
  --tag-list real-ollama \
  --run-untagged=false
```

Host prerequisites (the runner user's `PATH`):

- Docker Engine
- Docker Compose plugin (`docker compose`)
- Python 3.12 (`python3`, `pip`)
- Node matching `frontend/package.json` (project uses a controlled version)
- Git

Exact job → tag mapping (every ACTIVE job in `.gitlab-ci.yml` declares one of
these tags, so no job stays pending on a correctly registered runner):

| Tag | Jobs |
| --- | --- |
| `docker` | `validate`, `backend-tests`, `root-tests`, `frontend-tests`, `bridge-tests`, `frontend-build`, `docker-build`, `docker-smoke`, `prod-like-smoke` |
| `real-ollama` | `real-ollama-regression` |

The `docker` runner covers all hermetic work (repo commands + Docker); the
`real-ollama` runner additionally has private network access to the real
Ollama host. Normal CI never requires Ollama reachability.

Runner restart acceptance (§52): after a host reboot the runner must
reconnect, pick up the next pipeline, and Docker must work — there is no
hidden shell/session dependency in the jobs.

## 4. Configuration and environment profiles

Configuration stays environment-driven. The compose files render values from
the current shell + the optional `.env`, exactly like local/Docker
development. The documented profiles (§8) live in `compose/profiles/`
(these basenames deliberately avoid the `.env.*` family the release gate
reserves for operator-local secret files):

| Profile | File | Provider | Bridge | Use |
| --- | --- | --- | --- | --- |
| local deterministic dev | `compose/profiles/dev.env` | `fake` | off | local `docker compose up --build` |
| CI deterministic | `compose/profiles/ci.env` | `fake` | off | every push/MR pipeline + docker-smoke |
| production-like local smoke | `compose/profiles/prod-like.env` | `fake` | off | Caddy-edge local smoke (§39) |
| real-Ollama acceptance | `compose/profiles/ollama.env` | `ollama` | off | operator-triggered real AI |
| production deployment | `.env.production.example` | operator | off | real deployment |

Invocation matrix:

```bash
# local deterministic development
docker compose --env-file compose/profiles/dev.env up --build

# CI deterministic smoke (exactly what the pipeline does)
docker compose -f docker-compose.yml -f docker-compose.ci.yml \
  --env-file compose/profiles/ci.env -p "pd-ci-<pipeline>-<job>" up -d --build
python -m tools.ci_wait_ready --base-url http://127.0.0.1:8000
python -m tools.docker_smoke --base-url http://127.0.0.1:8000

# production-like local smoke
docker compose --env-file compose/profiles/prod-like.env \
  -f docker-compose.prod.yml up --build -d
python -m tools.prod_preflight --allow-local
```

**Never silently switch provider modes** (§8). If `GENERATION_PROVIDER=ollama`
and Ollama is unavailable the product fails truthfully and typed; the
`real_ollama_regression` runner fails the same way (§34). There is no
fake/cassette/mock fallback anywhere in the pipeline.

## 5. GitLab CI/CD variables

Private values are stored as GitLab CI/CD variables (masked and protected
where applicable). They exist only in the runner environment — never in the
repository, logs or artifacts:

| Variable | Purpose |
| --- | --- |
| `OLLAMA_BASE_URL` | private endpoint of the real Ollama host |
| `OLLAMA_MODEL` | e.g. `hermes3:8b` |
| `CADDY_DOMAIN` | public domain for a real deployment |
| `CADDY_EMAIL` | certificate email for a real deployment |

**No job ever echoes or prints these values.** The real-Ollama job's
`GENERATION_PROVIDER=ollama` comes from a job-level variable; the secrets come
from the CI/CD variable store. If a schedule runs without them, the job FAILS
TRUTHFULLY (never runs the fake path).

## 6. Jobs in detail

### validate
Fast gates before any expensive work (§20):
`git diff --check`, `python -m tools.release_check
--allow-hosted-placeholders`, `python -m tools.catalog_report`, `python
tools/generate_adapters.py --check`, the REQUIREMENTS.md sha256 guard and the
CI fake-world drift guard. Runs on the `docker`-tagged runner (repo commands
only) — see §3 for the exact job → tag mapping.

### backend / root / frontend / bridge tests
Hermetic Python suites with isolated writable tmp. On manual Windows runs the
documented workaround `--basetemp=C:\Temp\pd-pytest-backend` applies (§21);
Linux CI uses the runner workspace tmp dir. The `backend-tests` job also emits
a pytest junit XML (`-o junit_family=legacy --junitxml=...` under
`$CI_PROJECT_DIR/.tmp/`, kept as the job artifact and exposed to GitLab's
junit test reports) — the deterministic report the job actually produces
(DEF-002: a `.pytest_cache` nodeids artifact resolved to the wrong directory).
Frontend uses `npm ci` (no global npm packages), typecheck and build under a
controlled Node version.

### docker-build
`docker version` / `docker compose version`, rendered `docker compose config`
for both profiles (canonical timeout-envelope gate — shell overrides cannot
bypass it), then `docker compose build`. The image is reproducible from a
clean checkout: the frontend is built inside a Node stage, no local
`node_modules`/`frontend/dist`/venv/db dependency.

### docker-smoke
Boots the CI deterministic stack under the **unique per-job project**
`pd-ci-$CI_PIPELINE_ID-$CI_JOB_ID` (§7/§50), waits on `/api/v1/readiness`
(bounded `tools.ci_wait_ready` — no arbitrary sleeps), then runs the full
deterministic journey (`python -m tools.docker_smoke`): session -> generation
-> PUBLISHED -> public case -> playthrough -> evidence discovery -> Activity
Log (15-20 rows, compact rendering) -> witness -> accusation -> reveal ->
bridge-disabled. `after_script` saves bounded logs and runs
`down -v --remove-orphans` for ONLY this project. Cleanup **never** uses
`docker system prune -af` and never touches foreign resources.

### prod-like-smoke
Production compose on a local/test `CADDY_DOMAIN=localhost`, `docker compose
config` render, `python -m tools.prod_preflight --allow-local` ($39), a
Caddy-edge HTTPS health probe, bounded logs, project-scoped cleanup.

### real-ollama-regression
`python -m tools.real_ollama_regression --matrix` — schedule + manual only
(§33). Runs the §35 lightweight matrix (Easy x1, Medium x1, procedural
arbitrary-object x1, Activity Log x1) through the real provider, reports only
sanitized structural/timing diagnostics, FAILS truthfully when Ollama is
unreachable.

## 7. Rendered-compose validation + timeout envelope (§9/§38)

Docker and GitLab reuse the **canonical Phase 19J-RI2 timeout envelope**
(`backend/app/core/timeout_envelope.py`) — never a CI-only copy. The
production preflight (`tools.prod_preflight`) and the new CI compose gate
(`tools.release_check.check_ci_compose_config`) both render `docker compose
config` (the shell/`.env`/`--env-file` interpolation is authoritative) and
validate the rendered `CASE_GENERATION_DEADLINE_SECONDS` /
`OLLAMA_TIMEOUT_SECONDS` through the same pure validator the runtime uses, so
a shell override that evades a gate also changes the render being validated.

## 8. Real-AI acceptance and release evidence

- Nightly/scheduled: `--matrix` (§35) — per item PUBLISHED/FAILED, failed
  stage, typed code, provider calls, repairs, duration.
- Full release gate: `python -m tools.real_ollama_regression --release-gate
  --max-generations 10 --witness-report ./witness-report.json
  --browser-report ./browser-report.json` (§36) — >=10 real generations,
  >=90% published, 0 incorrect / 0 partial publications, Activity Log live
  validation, AND the operator-provided witness + browser journey evidence.
  The gate FAILS (`gatePassed: false`, exit 1) unless BOTH journey report
  files are supplied, parse as JSON, and indicate a passing journey
  (`ok: true`). An operator who knowingly accepts missing journey evidence
  must pass `--allow-skip-journeys` — the skipped journeys are still reported
  in the JSON under `operatorJourneys` (DEF-007; never fabricated).

**Risk-based real-AI surfacing** (§56): a real-AI validation should be
included in a release whenever the diff touches `backend/app/generation/`,
`backend/app/services/ollama_driver.py`, `backend/app/domain/activity_log.py`,
provider adapters, structured schemas, the timeout envelope, or
`RemoteClientProvider`. The pipeline real-AI job stays manual/schedule so an
ordinary push never blocks on model availability.

## 9. Security and artifacts (§27/§45/§46/§47)

- Release gate fails on committed `.env`, private LAN IPs, Ollama URLs,
  tokens, private keys, passwords (credential scan, §46).
- `.dockerignore` excludes `.env*` (keeps `.env.example`), logs, databases,
  QA/probe scratch and test trees — so operator secrets never reach a builder
  or build cache, and the final image has no test scratch/private provider
  configuration (§47).
- Pipeline artifacts: bounded diagnostics only — test reports,
  `docker-smoke.log`, `docker-smoke-report.json`, release-check output. Never
  `.env`, endpoints, tokens, databases, CaseTruth, prompts or provider output.

## 10. Troubleshooting

- **Pipeline created but no Docker job runs**: check the runner has tag
  `docker` and the shell executor PATH has `docker` + Docker Compose plugin.
- **docker-smoke times out at readiness**: check `docker compose logs` for
  migration/startup failures (entrypoint runs `alembic upgrade head` before
  uvicorn), `docker compose ps` for the healthcheck state.
- **Prod-like preflight rejects CADDY_DOMAIN**: that is by design — the local
  profile uses `localhost` and `--allow-local`; a real release needs a public
  `CADDY_DOMAIN`.
- **real-ollama job fails without a traceback**: that is the typed, truthful
  config/probe failure. Verify `OLLAMA_BASE_URL` / `OLLAMA_MODEL` variables
  exist on the `real-ollama` runner and the private endpoint is reachable from
  that host only.