# Procedural Detective — Monitoring & Usage Analytics (Phase 29)

Read this after `docs/DEPLOYMENT.md`. Phase 29 adds a **thin, observability-only
layer** — it never changes the trust boundary, the game flow, or any security
control (MON-18):

```
Internet
   |
   +--> Caddy :80/:443       <-- access logs: JSON lines on stdout
            |                     (Docker json-file rotation: 10 MB x 5)
            +--> procedural-detective:8000   (private, expose-only)
                    |
                    +--> SQLite /data/procedural_detective.db
                          (product/usage metrics read with SELECTs only)
```

Two questions are answered separately (MON-04 vs MON-08):

1. **Web/HTTP monitoring** — how often is the public app called, which
   endpoints, which status codes, how much traffic.
2. **Product/usage metrics** — how many real game sessions, case starts, and
   completed cases exist.

The implementation consists of:

- **Caddy access logging** (`docker/Caddyfile`, MON-01): the public HTTPS
  virtual host emits structured JSON access-log lines to `stdout`.
- **A repository report CLI** (`tools/monitoring_report.py`, MON-04/05/08/10):
  reads those log lines (via `docker compose logs caddy`) and computes HTTP
  aggregates, plus reads the **existing** SQLite schema read-only for product
  metrics. No new database, no long-running container, no new public port, no
  external SaaS, no network calls (MON-12/MON-14/MON-17).
- **Preflight gates** (`tools.prod_preflight` / `tools.release_check`,
  MON-15): hermetic checks that Caddy access logging stays enabled and JSON
  formatted, the log rotation stays bounded, no monitoring port is published
  and no Docker socket is mounted.

---

## 1. Access logs anzeigen (MON-16)

Caddy writes one JSON object per request to the container's stdout. The Docker
`json-file` driver rotates them (10 MB x 5 per container, F-04 / MON-02) — this
Docker-bound rotation is the **only** retention mechanism; Caddy writes no file
inside the container (no unbounded log file, MON-01).

```bash
# Last hour of access log lines (plain Caddy JSON on one line per request)
docker compose -f docker-compose.prod.yml logs caddy --since=1h

# Raw JSON without the "caddy-1  | " compose prefix (Docker 23.x+):
docker compose -f docker-compose.prod.yml logs caddy --since=1h --no-log-prefix

# Inspect the effective rotation policy of a RUNNING caddy container:
docker inspect $(docker compose -f docker-compose.prod.yml ps -q caddy) \
  --format '{{.HostConfig.LogConfig}}'
# -> {"Type":"json-file","Config":{"max-file":"5","max-size":"10m"}}
```

A single access-log line looks like (Caddy 2 `format json`):

```json
{"level":"info","ts":1700000000.5,"logger":"http.log.access.log0",
 "msg":"handled request",
 "request":{"remote_ip":"203.0.113.7","remote_port":"51970","client_ip":"203.0.113.7",
            "proto":"HTTP/1.1","method":"GET","host":"detective.example.com",
            "uri":"/api/v1/health"},
 "bytes_read":0,"user_id":"","duration":0.002,"size":40,"status":200}
```

> **HTTP requests are NOT equivalent to visitors or players.** One page load
> fans out into many requests (HTML + JS + CSS + assets + several `/api/v1`
> calls). Health checks and static assets are therefore tracked but never
> presented as player activity (MON-04/MON-05).

## 2. Monitoring prüfen — the report CLI (MON-10)

```bash
# 1) Snapshot the production database (read-only copy for the report):
docker compose -f docker-compose.prod.yml cp \
  procedural-detective:/data/procedural_detective.db ./pd-snapshot.db

# 2) Run the report over the last 24 h:
docker compose -f docker-compose.prod.yml logs caddy --since=24h | \
  python -m tools.monitoring_report --logs - --db ./pd-snapshot.db

# 3) Compact status block only (the MON-10 "Last 24 hours" view):
docker compose -f docker-compose.prod.yml logs caddy --since=24h | \
  python -m tools.monitoring_report --logs - --db ./pd-snapshot.db --summary

# 4) Machine-readable JSON (identical output on every run for the same input):
python -m tools.monitoring_report --logs ./caddy.jsonl --db ./pd-snapshot.db --json

# 5) Optional operator health summary over the private stack (requires a
#    running Docker daemon; read-only `docker compose ps` only):
python -m tools.monitoring_report --logs ./caddy.jsonl --db ./pd-snapshot.db --health
```

No root required. The tool performs **only SELECTs** (read-only connection when
the filesystem allows it), never opens a port, never mounts the Docker socket
and makes no network calls (MON-17/MON-13/MON-14). Exit codes: `0` report
produced, `1` data-source error, `2` usage error.

### What the HTTP section reports (MON-04/MON-05)

Total requests, average requests/hour over the observed span, requests per day,
HTTP methods, most-requested paths (query strings stripped), status classes
(2xx/3xx/4xx/5xx), 404 count, 5xx count, transferred bytes (the `size` field
where determinable), an hourly time series, and a request categorization:

| Category | Route set (taken from the repository, not invented) |
| --- | --- |
| **Page** | SPA routes from `frontend/src/main.tsx`: `/`, `/new`, `/generating`, `/scene`, `/accuse`, `/reveal` |
| **API** | every `/api/v1/*` route (`backend/app/api/v1/*.py` routers) |
| **Static** | `/assets/*`, `/static/*` (content-hashed bundles) |
| **Health** | `/api/v1/health`, `/api/v1/readiness` — the compose healthcheck hits `/api/v1/health` every 30 s; counted as infra traffic, never as players |
| **Other** | anything else (e.g. `/favicon.ico`, unknown paths) |

### What the product section reports (MON-08)

Read from the **existing** schema (no analytics database):

- **Playthroughs started** — rows in `playthroughs` (a session pinned to one
  published CaseVersion).
- **Cases started** — rows in `cases` (one logical case per generation
  request).
- **Cases completed** — distinct `case_id`s in `published_versions` whose
  `published_at` falls in the window. `published_versions` is **insert-only**
  and a row exists iff a CaseVersion reached the terminal `PUBLISHED` state of
  the §7.3 generation state machine (`backend/app/generation/state_machine.py`
  + `backend/app/models/published.py`). This is the exact, non-invented
  meaning of "case completed".
- **Completion rate** — cases completed / cases started in the window
  (approximate: a case created at the window edge may complete just after it).
- **Usage timestamps** — first/last `created_at` of playthroughs and cases in
  the window; playthroughs per day.
- **Session duration — deliberately NOT reported.** The schema stores
  `playthroughs.created_at` and `expires_at` (a fixed token-validity TTL),
  never when a session actually ended; `{CREATED, PLAYING} -> ACCUSED ->
  REVEALED` transitions carry no end timestamp and players may idle or leave
  mid-session. Any derived duration would be fabricated, so MON-08 documents
  this instead of inventing a number.

## 3. Troubleshooting (MON-16)

| Symptom | Cause / check |
| --- | --- |
| **No access logs visible** | The `log` block is missing from `docker/Caddyfile` (the preflight `caddy-access-logging` finding fails), the caddy container is not running (`docker compose -f docker-compose.prod.yml ps`), or `--since=` filters everything. Verify a real request produced a line: `curl -sk https://<host>/api/v1/health` then `docker compose -f docker-compose.prod.yml logs caddy --since=1m`. |
| **Report can't read the logs** | Feed the report RAW access-log lines. `docker compose logs` prefixes lines with `caddy-1  | ` (the tool strips it) and Docker 23+ may wrap lines in a JSON envelope (the tool unwraps it too). If you captured a log file with PowerShell redirection it may be UTF-16/BOM (the tool decodes it). A line that is not an access-log line (`logger` / no `status` / no `request.uri`) is counted as "skipped non-access/garbled lines" — that counter is healthy, not an error. |
| **Caddy configuration invalid** | Check the caddy container logs: `docker compose -f docker-compose.prod.yml logs caddy`. Validate the file with `docker compose -f docker-compose.prod.yml config` (renders/validates the compose + mounts) and `caddy validate` against the shipped `docker/Caddyfile`. The LAN overlay variant (`docker/Caddyfile.internal`) must be a byte-copy of the canonical file plus `tls internal`. |
| **Monitoring data grows unexpectedly** | Both log streams are bounded by the Docker `json-file` rotation (10 MB x 5 per container). Verify with `docker inspect` (§1, F-04 / MON-02). If an operator pipes logs elsewhere (`> report.log`, a log shipper), that target is THEIR retention responsibility — the repo itself stores nothing outside the rotated Docker logs. Reporting artifacts (a `pd-snapshot.db` copy) are git-ignored (`*.db`) and are read-only snapshots. |
| **Dashboard not reachable** | There is **no web dashboard and no monitoring port** (MON-07). Nothing listens beyond `22/80/443`. The "dashboard" is the report CLI / its `--json` output. If an operator builds an optional local GoAccess HTML report (§5), it must be regenerated outside the public web root and opened via SSH tunnel — never published. |
| **5xx spike** | Run the report: the human output prints a `5xx spike check` line (warning at ≥20 5xx in the window or ≥5% ratio). Correlate with the DB metrics (`Failed generation attempts`, `Cases started peak`) and the timestamped HTML/site requests with `docker compose logs caddy --since=...`. The backend is private, so 5xx are served by the app, not by Caddy itself — check `docker compose -f docker-compose.prod.yml logs procedural-detective` for the sanitized error envelopes. |

## 4. Privacy & data retention (MON-03/MON-09/MON-17)

**What is captured:** per-request aggregates only — request count, method,
status class, path (query string stripped), response size, hour/day bucket.
Product metrics are row counts and timestamps from the existing database.

**Why it is captured:** to distinguish real traffic from probes/health checks,
to detect 5xx spikes, and to measure actual game usage (sessions, case starts,
completions) without adding tracking.

**How long it is retained:** access-log lines live only in the Docker
`json-file` rotation (10 MB x 5 files per container ≈ 50 MB). Reports are
computed on demand and kept in memory; a captured log/snapshot file is the
operator's own artifact and is git-ignored. Nothing else stores them.

**Raw rotated Docker logs vs the report capture:** the two beats must not be
conflated. The Docker `json-file` rotation retains Caddy's **raw** `format
json` access-log lines, and those lines carry a `request.headers` object whose
contents are Caddy-minor-version dependent: `User-Agent` and `Referer` are
typically present, while whether `Authorization` / `Cookie` headers are
redacted or echoed varies across Caddy versions — verify the emitted header
set on the live host (one-liner below) and note the result in the operator
runbook. The **REPORT tool** (`tools/monitoring_report.py`), by contrast,
never reads headers, IP addresses, User-Agent strings or bodies: it consumes
only `request.uri` (query stripped), `request.method`, `status`, `size` and
`ts`, so its output stays header-free regardless of what the raw rotated
lines contain.

**What is explicitly NOT captured by the report (MON-03):**

- request bodies — never logged, never read;
- passwords; `Authorization` headers, cookies/session secrets, API keys/tokens
  are never read or reported by the tool (their presence in the RAW rotated
  lines is a Caddy-version question — check the live header set, below);
- query strings (stripped before the top-path report; never persisted);
- client IP addresses — the report prints no IP field at all; IPs from Caddy's
  `request.remote_ip`/`client_ip` are never aggregated or stored long-term;
- browser fingerprints, persistent analytics cookies, cross-site identifiers
  (MON-09). No user identifier is ever created by the monitoring layer; the
  existing anonymous quota session ids are never used for analytics.

**Operator note (bounded rotated logs + live header check):** always run the
report over the bounded rotated logs (`docker compose -f
docker-compose.prod.yml logs caddy --since=24h | python -m
tools.monitoring_report --logs - --db ./pd-snapshot.db`); once per live host,
verify which headers Caddy actually emits into what the report ingests:
`docker compose -f docker-compose.prod.yml logs caddy --since=1m |
sed -n 's/^[A-Za-z0-9_.-]* *| *//p' | head -1 | jq '.request.headers'`
(adjust the prefix-strip/jq to the installed Caddy minor version) and record
whether `authorization`/`cookie` appear in the raw rotated lines.

## 5. GoAccess decision (MON-06)

Investigated whether GoAccess can robustly consume the Caddy JSON logs.

**Verdict: NOT adopted.** Reasons:

1. **Fragile Caddy JSON mapping.** GoAccess's log parser is line-oriented and
   its `CADDY` support has changed across releases; upstream issue
   allinurl/goaccess#2626 documents `format errors` / "IPv4/6 is required"
   against Caddy's site-level `format json` logs, and the man page states the
   built-in default targets Caddy's `local/info` logger format (not the
   site-level `format json` we ship). Mapping our exact nesting
   (`request.method`, `request.uri`, top-level `ts`/`status`/`size`) to
   GoAccess format tokens would depend on the host package version — the
   definition of a fragile workaround.
2. **Input plumbing.** The canonical source is Docker-managed stdout
   (`caddy-1  | {...}` prefixes, possible Docker JSON envelopes). Every
   `awk`/`docker logs --raw` transform is another fragile step.
3. **Privacy regression risk.** GoAccess's built-in "unique visitors" is
   IP+date+user-agent based — long-term per-IP retention — which collides with
   MON-03's no-long-term-IP rule unless extra flags (`--anonymize-ip`,
   `--no-query-string`) are maintained by hand.
4. **Dashboard port.** A real-time dashboard wants a WebSocket server bound
   `0.0.0.0` (MON-07 non-starter); a static report needs a host package +
   regeneration flow that duplicates the repo CLI.

**Alternative (adopted):** `tools/monitoring_report.py` — a stdlib-only,
dependency-free repository CLI that parses the exact emitted format (including
Docker/compose/UTF-16 wrappers), excludes health/static traffic from player
numbers, reads the existing DB read-only, and produces the same aggregates —
with no extra package, no port, no IP retention. GoAccess remains an optional
operator-side tool; if an operator already runs it, an anonymized static report
(never the public web root) is possible, but it is NOT part of the supported
deployment path.

## 6. Health monitoring (MON-11)

Phase 29 adds **no external SaaS monitor**. The report CLI's `--health` flag
surfaces container state via read-only `docker compose ps`. Manual daily
checks:

1. **App container runs:** `docker compose -f docker-compose.prod.yml ps`
2. **App container healthy:** `docker inspect --format '{{.State.Health.Status}}' <backend-container>` → `healthy` (the compose healthcheck hits `/api/v1/health` loopback every 30 s)
3. **Caddy runs:** `docker compose -f docker-compose.prod.yml ps caddy`
4. **DB reachable:** `curl -sk https://<host>/api/v1/readiness` → `{"status":"ready",...}`
5. **Public app status:** `curl -sk https://<host>/api/v1/health` → 200 fixed body
6. **5xx spike:** run the report and read the `5xx spike check` line (§3)

## 7. Preflight enforcement (MON-15)

`python -m tools.prod_preflight` (and `python -m tools.release_check
--allow-hosted-placeholders`) now assert, hermetically (no Docker daemon
required):

```
OK: Caddy access logging enabled       (check_caddy_access_logging)
OK: Caddy log output bounded           (check_compose_logging_bounds, F-04)
OK: backend remains private            (check_prod_effective_config)
OK: no monitoring host port published  (check_compose_monitoring_safety)
OK: monitoring does not expose Docker socket (check_compose_monitoring_safety)
```

These findings are strictly additive — no existing finding is weakened by
Phase 29 (MON-15).