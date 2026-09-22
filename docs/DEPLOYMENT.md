# Procedural Detective — Production Deployment

Phase 20 security-hardened deployment (PD-SEC-03/04/05/06). **Public hosting
is HTTPS-only**; the FastAPI/uvicorn backend is **private-network-only** and
the browser talks to the API **same-origin** over the TLS edge. Local
development still uses the simple `docker compose up --build` flow on `:8000`.

```
Internet
   |  HTTPS :443   (HTTP :80 -> HTTPS redirect)
   v
TLS reverse proxy / platform ingress      <- Caddy (shipped) or your provider
   |  private HTTP, compose network only
   v
Uvicorn :8000  (private; NO host port published in production)
```

`Uvicorn is not publicly reachable in production` — the production profile
publishes **only** the TLS edge; `:8000` is never exposed to the host or the
Internet (PD-SEC-03 §10/§10.2).

## 1. Two deployment profiles

| Profile | Command | Public exposure |
| --- | --- | --- |
| **Development** | `docker compose up --build` | `http://localhost:8000` (uvicorn published — local only) |
| **Production** | `docker compose -f docker-compose.prod.yml up --build -d` | HTTPS `:443` + HTTP→HTTPS `:80` via Caddy; backend private only |

The development flow is unchanged: `docker compose up --build` still serves
the SPA + API from one container on `:8000` for local work. The production
file (`docker-compose.prod.yml`) uses the **same image** but publishes no
backend port at all.

## 2. Production architecture (HTTPS-only, same-origin)

- **TLS edge (shipped):** `docker/docker-compose.prod.yml` starts a minimal
  Caddy 2 container (`caddy:2-alpine`) that fronts the private backend:
  - automatic HTTPS (managed certificates via Let's Encrypt/ZeroSSL for a
    public `CADDY_DOMAIN`; Caddy's internal CA for `localhost` smoke tests);
  - HTTP → HTTPS redirect;
  - `Strict-Transport-Security` at the **HTTPS boundary only**, **no preload**
    by default (PD-SEC-03 §10.3 — do not enable preload blindly);
  - request body limit (last-resort guard above the backend's own
    `MAX_REQUEST_BODY_SIZE`), request-header/body read timeouts and a max
    header size, and an upstream cap above
    `CASE_GENERATION_DEADLINE_SECONDS` so long generations are never cut;
  - proxies **only** to the private backend service (`procedural-detective:8000`).
  Config: `docker/Caddyfile` (§4). No uvicorn port is published.
- **Platform ingress (alternative):** if your hosting provider terminates TLS
  itself, start only the backend service — it is already private-only:
  `docker compose -f docker-compose.prod.yml up -d procedural-detective`.
  The ingress forwards HTTPS to the container on the private network and must
  be configured with HTTP→HTTPS redirect + HSTS at its own boundary.
- **Same-origin API (PD-SEC-04):** the production frontend calls the API at a
  **relative** `https://<host>/api/v1/...` — there is **no absolute public API
  URL**. The backend serves the SPA and the API from one origin, so CORS is
  moot and `VITE_API_BASE_URL` must **not** be pointed at
  `http://localhost:8000` in a public build (§10). Private/playthrough
  responses remain `Cache-Control: no-store` regardless.

## 3. Environment variables

Canonical names (REQUIREMENTS §45), exact one-per-setting. Full list with
defaults: `.env.example`.

| Variable | Purpose | Container default |
| --- | --- | --- |
| `DATABASE_URL` | SQLAlchemy URL. Production points at the durable volume: `sqlite:////data/procedural_detective.db`. | `sqlite:////data/procedural_detective.db` |
| `ENVIRONMENT` | Deployment mode. `production` is the prod-profile default; dev defaults to `development`. | `production` (prod compose) / `development` (dev) |
| `PD_DEV_TRACE` | Developer trace. **Must be `false` in production** — the prod profile forces it `false` (see §12). | `false` |
| `TRUST_PROXY` | Honor proxy-forwarded client IPs (`X-Forwarded-For` etc.) ONLY when `true` and the TLS edge is the defined trusted proxy (§7). The APP is the sole authority — uvicorn itself always runs with `--no-proxy-headers`, so forwarded headers are never double-processed. | `false` (dev) / `true` (prod compose with Caddy) |
| `GENERATION_PROVIDER` | `fake` (deterministic demo, default) or `live` / `ollama` (opt-in). | `fake` |
| `LLM_API_KEY` | Live-mode credential. **Never committed.** | unset |
| `LLM_MODEL` | Live-mode model name (e.g. `gpt-4.1`). Required for live. | unset |
| `LIVE_PROVIDER_URL` | Live-mode HTTPS endpoint. Rejected unless `https://`. | unset |
| `OLLAMA_BASE_URL` | Local-AI operator endpoint. Only loopback/private/LAN hosts accepted; must remain **private** (§13). | `http://127.0.0.1:11434` |
| `CORS_ALLOWED_ORIGINS` | Comma-separated allowlist. `*`/`null` rejected. Same-origin serving makes it moot; set it to the deployed UI origin only when the UI is hosted separately. | empty (same-origin) |
| `STATIC_DIR` | Built-frontend directory served by the backend (SPA at `/` + SPA fallback). | `/app/static` |
| `API_HOST` / `API_PORT` | uvicorn bind inside the container. | `0.0.0.0` / `8000` (never published in prod) |
| `…TTL_SECONDS`, `MAX_*` | Token/quota TTLs and generation budgets (see `.env.example`; rate limiting in §7). | see `.env.example` |
| `CADDY_DOMAIN` / `CADDY_EMAIL` | Production TLS edge host / ACME contact (`docker-compose.prod.yml`). | `localhost` / unset |

## 4. Caddy TLS edge (`docker/Caddyfile` + `docker-compose.prod.yml`)

```bash
# Local smoke (localhost internal CA — use -k / trust the local root once):
docker compose -f docker-compose.prod.yml up --build -d
curl -k https://localhost/api/v1/health
curl -I -k https://localhost/          # 200 + Strict-Transport-Security

# Real hosting: point CADDY_DOMAIN at the public host in .env / environment.
CADDY_DOMAIN=detective.example.com
CADDY_EMAIL=you@example.com
docker compose -f docker-compose.prod.yml up --build -d
```

The Caddyfile is deliberately minimal (PD-SEC-03 §10.1, "do not over-engineer"):
auto-HTTPS, HTTP→HTTPS redirect, HSTS (no preload), request-body/header-size
limits, read/idle timeouts, and a proxy to the private `procedural-detective`
service. It never exposes a uvicorn port.

## 5. Health / readiness probes

| Endpoint | Meaning |
| --- | --- |
| `GET /api/v1/health` | Liveness: fixed body, **never touches the database**. Used by the docker HEALTHCHECK. |
| `GET /api/v1/readiness` | Readiness: real `SELECT 1` **and** Alembic at head. `503 NOT_READY` envelope otherwise. |

In production, probe them through the TLS edge (`https://<host>/api/v1/health`).
The docker HEALTHCHECK inside the backend keeps probing loopback within the
private network.

## 6. Migration on startup (unchanged)

The entrypoint runs `alembic upgrade head` exactly once before uvicorn starts
(`docker/entrypoint.sh` → `backend/app/startup.py`). `DATABASE_URL` pointing
at a private volume path is enforced; a misconfigured container exits non-zero
with a sanitized message and never serves an unready app.

## 7. Rate limiting & trusted proxy (PD-SEC-02, §27)

Public generators are bounded at multiple layers (per-session budget, per-IP
budget and global ceilings — canonical names in `.env.example`, operator-tuned
starting values: session per-window budget, global per-window quota, and
`MAX_CONCURRENT_GENERATIONS_GLOBAL`). Behavior:

- **Anonymous-session admission** and **generation** are rate-limited with
  rolling windows that recover without a process restart.

**Forwarded-header authority — the app is the SOLE authority.** uvicorn is
launched with `--no-proxy-headers` on every shipped launch path (the documented
dev command, `scripts/start-demo.ps1`, and `docker/entrypoint.sh` inside the
container image), so uvicorn NEVER rewrites `request.client` from forwarded
headers before the app runs. This is load-bearing (DEF-094): uvicorn's platform
default is `--proxy-headers` (trusting loopback `127.0.0.1`), which replaces
`request.client` from a spoofed `X-Forwarded-For` BEFORE the ASGI app is
invoked — with `TRUST_PROXY=false` each spoofed value would then mint its own
per-IP budget. Keep `--no-proxy-headers` on every uvicorn launch line whenever
`TRUST_PROXY=false`; the app additionally logs a one-time operator warning when
it still sees an `X-Forwarded-For` header while proxy trust is off.

- **`TRUST_PROXY=false` (the default, and every dev/private launch):**
  `X-Forwarded-For` / `Forwarded` / `X-Real-IP` are **ignored** — the rate-limit
  identity is always the direct socket peer. A hostile forwarded header can
  never change the identity (the DEF-094 spoof-rotation vector is closed).
- **`TRUST_PROXY=true` (prod profile default, and ONLY behind the Caddy edge):**
  the app honors the **left-most** entry of `X-Forwarded-For` as the original
  client — a single trusted-proxy chain where each hop appends the previous hop
  (left = original client). Caddy sets the header on its private-network hop to
  the backend; uvicorn (`--no-proxy-headers`) does not double-process it. The
  shipped `docker/Caddyfile` is unchanged.
- If you swap the edge for a different ingress or add untrusted hops, keep
  `TRUST_PROXY=false` unless you can name the single trusted reverse proxy
  exactly (a deployment with multiple untrusted hops must keep it false — the
  peer address of the trusted edge is the only safe identity).

**Authoritative production path (Phase 21 P-01).** The shipped Caddy-first
profile (`docker-compose.prod.yml`, full stack) is the **AUTHORITATIVE
supported production path** and the ONLY supported way to enable
`TRUST_PROXY=true`. An **alternate platform ingress** is supported only as a
`TRUST_PROXY=false` deployment (`docker compose -f docker-compose.prod.yml up
-d procedural-detective`): the ingress MUST default to `TRUST_PROXY=false` and
it may be enabled **only after the actual ingress has been verified to**:

1. strip or overwrite hostile `X-Forwarded-For` before the request reaches the
   app (the app sees only the ingress's own value);
2. provide a **known hop structure** (you can name every hop that touches the
   header); and
3. **preserve the correct client IP** end to end.

`TRUST_PROXY=true` is **NOT portable between ingress providers** — the trust
boundary is per-ingress and must be re-proven whenever the edge changes. The
automated contract test `backend/tests/test_phase21_deployment.py` pins the
DOCUMENTED contract on a forged multi-hop `X-Forwarded-For` header: with
`TRUST_PROXY=false` the header is ignored (the direct socket peer is the
identity); with `TRUST_PROXY=true` the left-most entry is honored — which is
safe ONLY because the shipped Caddy edge overwrites the header on its
private-network hop.

## 8. Privacy & retention

Generated cases persist **raw prompt, public case, hidden truth, generation
metadata and player state** in the private SQLite volume. Read the policy:

> **Retention:** data is retained for the duration of the hackathon/demo
> period and is then eligible for operator deletion. The build does **NOT**
> delete automatically. Operator deletion + backup procedures:
> `docs/PRIVACY.md`.

The SQLite volume is reachable only on the private network; it is never
exposed to the edge or to browsers.

## 9. Production CORS (same-origin)

Same-origin serving makes CORS moot: the SPA and the API share one origin and
the browser never issues same-origin preflights. If the UI is ever hosted
separately, set `CORS_ALLOWED_ORIGINS` to exactly the deployed UI origin;
`*`/`null` are rejected at configuration time and disallowed preflights answer
the shared error envelope without an `Access-Control-Allow-Origin` header.

## 10. Production API base (same-origin, PD-SEC-04)

The frontend resolves the API as `/api/v1` from the current browser origin
(`https://<host>/api/v1/...`). There is **no absolute public API URL**, and a
production build must not embed `http://localhost:8000` (the audit finding),
`127.0.0.1`, a private/LAN IP or an Ollama provider endpoint. The release check
scans `frontend/dist` for these and fails the gate on any hit
(`python -m tools.release_check --allow-hosted-placeholders`). Local dev may
still override `VITE_API_BASE_URL=http://localhost:8000`; production must not.

## 11. Cache policy (unchanged)

Every private/authenticated `/api/v1` response carries `Cache-Control: no-store`
(+ `Pragma: no-cache`). The SPA `index.html` is served `no-store`;
content-hashed Vite assets under `/assets` are `immutable, max-age=31536000`.
Health/readiness stay cacheable-safe status words.

## 12. Dev-trace prohibition in production (PD-SEC-06)

`PD_DEV_TRACE=true` can print upstream provider error content — it must never
run in production. The production profile forces `PD_DEV_TRACE=false` and sets
`ENVIRONMENT=production`; production startup rejects (or forces off and warns
about) dev tracing. Provider error bodies are logged class/status-only, never
contents, and provider URLs/bearer tokens are never logged.

## 13. Ollama stays private (PD-SEC-03/§27)

Local-AI mode is operator-configured (`GENERATION_PROVIDER=ollama` +
`OLLAMA_BASE_URL`). `OLLAMA_BASE_URL` accepts only loopback, private/LAN or
`host.docker.internal` hosts — it is never exposed to the browser, never taken
from a prompt, and in production the Ollama host must remain on the private
network (never the public edge, never the Internet).

## 14. Docker build-context hygiene (PD-SEC-07)

The repo-root `.dockerignore` excludes `.env` / `.env.*` (keeping `.env.example`),
`logs/`, `*.db`/`*.sqlite*`, `tmp/`/`temp/` and local Ollama config from the
Docker build context, so operator secrets are never sent to a daemon/builder
or build cache. The release check `check_dockerignore` asserts these exclusions.

## 15. Logging & log bounds (Phase 21 F-04)

Container stdout and application file logs are both bounded, at two different
layers, so no log stream can grow without limit:

- **Container stdout (uvicorn + Caddy access logs) — rotated by the runtime.**
  `docker-compose.prod.yml` carries an explicit bounded `logging:` block on
  BOTH public services (`procedural-detective` and `caddy`):

  ```yaml
  logging:
    driver: json-file
    options:
      max-size: "10m"
      max-file: "5"
  ```

  Uvicorn's access logs and Caddy's access/error logs both go to stdout; the
  `json-file` driver rotates them to roughly **10 MB x 5 files** per container
  (the Docker daemon enforces the envelope; verify your actual deployment with
  `docker inspect <container> --format '{{.HostConfig.LogConfig}}'` after a
  smoke run).
- **Backend application file logs — rotated at the app level (unchanged).**
  When file logging is enabled (`PD_FILE_LOGS` / `PD_LOG_FILE`), the backend
  writes `logs/procedural-detective.log` through a Python
  `RotatingFileHandler` at **5 MB x 3 backups**
  (`backend/app/core/observability.py`: `LOG_MAX_BYTES = 5 * 1024 * 1024`,
  `LOG_BACKUP_COUNT = 3`). That rotation is independent of the container
  stdout policy and is unchanged by F-04.
- **No secrets/tokens in access logs (established policy, restated).** Access
  logs must NEVER contain bearer tokens, prompts or secrets; the backend's
  structured logging allow-lists safe fields only
  (`backend/app/core/observability.py`), and the Caddy access path carries no
  credentials. Caddy is configured with `admin off` and no token is ever a URL
  component, so the access-log no-secret rule holds at both boundaries.
- **Enforcement:** `python -m tools.release_check` asserts the PROD compose
  services still carry the bounded block (`check_compose_logging_bounds`), and
  `docker compose -f docker-compose.prod.yml config` renders the effective
  logging policy.

## 16. Timeout envelope (Phase 21 P-02)

A generation request spans four layers, each with its own timeout. The strict
deployment contract — enforced by construction, documented here explicitly:

```
provider timeout  <  remaining backend generation deadline
                   <  frontend request timeout
                   <  reverse-proxy upstream timeout
```

**Values (chosen from the code's clamping math, not from a phase example):**

| Layer | Value | Where configured |
| --- | --- | --- |
| Provider per-call timeout | `OLLAMA_TIMEOUT_SECONDS`, bounded `5..300` — clamped to `remaining deadline − 0.1s` by `BudgetTracker.effective_provider_timeout` (`backend/app/generation/budgets.py`) | `.env.example` (`180`) |
| Backend generation deadline | `CASE_GENERATION_DEADLINE_SECONDS`, default `60`, recommended showcase `300` (max supported by this envelope) | `.env.example` |
| Frontend request timeout | `360s` (`REQUEST_TIMEOUT_MS = 360000`, `frontend/src/api/client.ts`) | source constant |
| Reverse-proxy upstream timeout | `420s` (`response_header_timeout 420s`, `docker/Caddyfile`) | `docker/Caddyfile` |

**Margins (documented policy):**

1. **Provider classification margin = 0.1s.** Every provider call is clamped
   to `remaining_deadline − 0.1s`, so `provider < remaining deadline` holds
   MECHANICALLY on every call — even a `300` timeout with a `300` deadline
   (showcase) runs with an effective `299.9s` cap, leaving the controller time
   to classify a timeout and persist the terminal state. An operator must
   never raise `OLLAMA_TIMEOUT_SECONDS` above the deadline; doing so only
   means the clamp silently shortens every call.
2. **Deadline-to-frontend margin = 60s.** The frontend aborts at 360s and the
   maximum supported backend deadline is 300s, so `300 + 60 = 360`: the
   browser NEVER aborts a request the backend still legitimately allows, and
   a max-length provider call that starts at `t=0` (effective 299.9s) still
   lands before the browser boundary.
3. **Frontend-to-proxy margin = 60s.** Caddy aborts at 420s vs the frontend at
   360s (`360 + 60 = 420`), so the TLS edge never kills a request before the
   browser's own timeout would. Do not reduce `response_header_timeout` below
   the frontend timeout when tuning for latency.

**Operational rules (do NOT break the envelope):**

- Never set `CASE_GENERATION_DEADLINE_SECONDS` above `300` unless the
  frontend and Caddy constants are raised together, preserving both 60s
  margins. A deadline ≥ the frontend timeout lets the browser abort a request
  the backend still allows.
- `OLLAMA_TIMEOUT_SECONDS` is bounded `5..300` by configuration validation and
  is clamped to the remaining deadline regardless.
- The config-wiring test `backend/tests/test_phase21_timeout_envelope.py`
  reads the documented constants AND parses `frontend/src/api/client.ts` +
  `docker/Caddyfile`, so any drift between this table and the real files fails
  the suite.

## 17. Security posture (summary)

- CaseTruth never reaches the client before reveal; pre-reveal payloads are
  allowlisted DTOs (leak scanners in the test suite).
- Reveal gated on `{ACCUSED, REVEALED}` (frozen REQUIREMENTS 40.12).
- Bearer tokens opaque, hashed at rest, constant-time compared, returned once.
- `{"error":{code,message,details}}` envelope; 500s never leak tracebacks.
- No secrets logged; `.env` keeps credentials out of git **and** out of Docker
  build contexts.
- HSTS only at the TLS boundary; no preload unless the operator explicitly opts in.

## 18. Quick reference

```bash
# Development (unchanged): SPA + API on http://localhost:8000
docker compose up --build

# Production (HTTPS-only): TLS edge + private backend
CADDY_DOMAIN=detective.example.com CADDY_EMAIL=you@example.com \
  docker compose -f docker-compose.prod.yml up --build -d

# Verify production
curl -k https://localhost/api/v1/health          # local smoke (internal CA)
curl -k https://localhost/api/v1/readiness
curl -I -k https://localhost/                     # SPA index.html + HSTS
curl -I http://localhost/                          # 301 -> https

# Platform-ingress alternative (only the private backend):
docker compose -f docker-compose.prod.yml up -d procedural-detective

# Release gate (includes the .dockerignore + production-bundle scans)
python -m tools.release_check --allow-hosted-placeholders
```