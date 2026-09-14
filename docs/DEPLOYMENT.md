# Procedural Detective — Production Deployment

Single-container, single-origin deployment. One container serves the React SPA
**and** the FastAPI backend from the same origin, so CORS is moot for normal
browser traffic and the SQLite database lives in a durable Docker volume.

## 1. Environment variables

Canonical names (REQUIREMENTS §45), exact one-per-setting. Full list with
defaults: `.env.example`.

| Variable | Purpose | Container default |
| --- | --- | --- |
| `DATABASE_URL` | SQLAlchemy URL. Container default points at the durable volume: `sqlite:////data/procedural_detective.db`. Local default is `<repo-root>/procedural_detective.db`. | `sqlite:////data/procedural_detective.db` |
| `GENERATION_PROVIDER` | `fake` (deterministic demo, default) or `live` (opt-in LLM). | `fake` |
| `LLM_API_KEY` | Live-mode credential. **Never committed.** | unset |
| `LLM_MODEL` | Live-mode model name (e.g. `gpt-4.1`). Required for live. | unset |
| `LIVE_PROVIDER_URL` | Live-mode HTTPS endpoint. Rejected unless `https://`. | unset |
| `CORS_ALLOWED_ORIGINS` | Comma-separated allowlist. `*`/`null` are rejected. Same-origin serving makes CORS moot; set it to the deployed UI origin only when the UI is hosted separately. | `http://localhost:5173` (dev) |
| `STATIC_DIR` | Directory of the built frontend (`index.html` + `assets/`). When set, the backend serves the SPA at `/` and falls back to `index.html` for `/scene` `/accuse` `/reveal`. Unset in local dev (Vite serves the UI). | `/app/static` |
| `API_HOST` / `API_PORT` | uvicorn bind. | `0.0.0.0` / `8000` |
| `…TTL_SECONDS` | Token/quota TTLs (`CREATOR_TOKEN_TTL_SECONDS`, `PLAYTHROUGH_TOKEN_TTL_SECONDS`, `ANONYMOUS_QUOTA_SESSION_TTL_SECONDS`) and all generation budgets (`MAX_*`, `CASE_GENERATION_DEADLINE_SECONDS`, …). | see `.env.example` |

## 2. Durable volume

The container persists SQLite to `/data` (a Docker **named volume**, not a
path in the image). The entrypoint creates `/data` and hands ownership to the
unprivileged `app` user before starting the server:

- `docker compose up --build` defines the volume automatically (`pd-data`).
- Plain `docker run`:
  `docker run --rm -v pd-data:/data -p 8000:8000 procedural-detective:latest`

Rebuilds and restarts never touch the data; deleting the volume deletes cases.

## 3. Migrations on startup

Controlled startup (no sidecar, no init container):

1. `docker/entrypoint.sh` runs `alembic upgrade head` **once** before the
   server starts (`python -m alembic -c /app/backend/alembic.ini upgrade
   head`). Backend module: `backend/app/startup.py::run_migrations()`.
2. Only after migration succeeds does uvicorn start.
3. `/api/v1/readiness` compares the database revision against the Alembic head
   and reports `200 {"status":"ready", ...}` afterwards.
4. **Graceful failure:** a malformed `DATABASE_URL` is rejected by `Settings`
   at configuration time; an unwritable database directory makes the entrypoint
   exit non-zero with a clear sanitized message ("Failed to apply database
   migrations…"). A misconfigured container never serves a never-ready app.

## 4. Health / readiness probes

| Endpoint | Meaning |
| --- | --- |
| `GET /api/v1/health` | Liveness: fixed body, **never touches the database**. Used by the docker HEALTHCHECK. |
| `GET /api/v1/readiness` | Readiness: real `SELECT 1` **and** Alembic at head. `503 NOT_READY` envelope otherwise. |

## 5. Demo mode vs Live mode

**Demo mode (default, zero-cost, zero-credentials):** `GENERATION_PROVIDER=fake`.
The deterministic offline provider replays the shipped validated golden case
for any prompt, so the complete browser journey (prompt → generate →
investigate → accuse → reveal) works with no API key, no network, no cost.
This is the public hackathon demo path.

**Live mode (opt-in):** `GENERATION_PROVIDER=live` **plus** all of
`LLM_API_KEY`, `LLM_MODEL`, `LIVE_PROVIDER_URL` (must be `https://`). A live
call can then synthesize genuinely new cases through the same
validate-repair-publish pipeline. The live-provider boundary stays replaceable
(`app/generation/live_provider.py`). Never commit credentials; inject them via
the environment (`.env` is git-ignored).

## 6. Production CORS (single-origin)

The shipped container serves the SPA **and** the API from one origin
(`http://<host>:8000`), so same-origin browser requests never trigger CORS at
all. If the UI is ever hosted separately, set `CORS_ALLOWED_ORIGINS` to exactly
the deployed UI origin. Arbitrary origins are never reflected: `*` and `null`
are rejected at configuration time and disallowed preflights answer the shared
error envelope without an `Access-Control-Allow-Origin` header.

## 7. Cache policy

Every private/authenticated `/api/v1` response (sessions, cases, generations,
playthroughs, investigation, accusation, reveal) — including the reveal
response and the playthrough bootstrap — carries `Cache-Control: no-store`
(+ `Pragma: no-cache`). The SPA `index.html` is served `no-store`; content-
hashed Vite assets under `/assets` are served `immutable` (public,
`max-age=31536000`). `/api/v1/health` and `/api/v1/readiness` remain
cacheable-safe status words.

## 8. Security posture (summary)

- `CaseTruth` never reaches the client before reveal; every pre-reveal payload
  is an allowlisted DTO (enforced by the full test suite's leak scanners).
- Reveal is gated on `{ACCUSED, REVEALED}` (frozen REQUIREMENTS 40.12).
- Bearer tokens are opaque, hashed at rest, constant-time compared, and only
  ever returned at issuance.
- Non-2xx responses all use the `{"error":{code,message,details}}` envelope;
  500s never leak tracebacks or internals.
- No secrets are logged; the API key never appears in provider-config error
  messages. Uncommitted `.env` keeps real credentials out of git.

## 9. Quick reference

```bash
# Build + run (compose)
docker compose up --build

# Plain docker
docker build -t procedural-detective:latest .
docker run -d --name pd -v pd-data:/data -p 8000:8000 procedural-detective:latest

# Verify
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/readiness
curl -I http://localhost:8000/          # SPA index.html (Cache-Control: no-store)
curl http://localhost:8000/scene        # SPA fallback (index.html, 200)
```