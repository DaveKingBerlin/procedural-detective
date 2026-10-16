# ADR-007: Production Compose Network Boundary & Deployment Ergonomics

- Status: Accepted (Phase 46 contract)
- Date: 2026-10-16
- Phase: 46
- Related: `Phase46.md`, ADR-005 (world-compose ownership)

## Context

`docker-compose.yml` (base/dev) publishes `8000:8000` for the backend. `docker-compose.prod.yml`
is a complete standalone production stack (backend + Caddy on an internal bridge network,
Caddy publishing `80`/`443`, backend `expose`-only). Compose merges sequence keys such as
`ports` **by concatenation**, so the combined invocation

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

inherits the base `8000:8000` and publishes the backend to the host, violating the intended
topology (only Caddy's `80`/`443` public; backend `8000` Docker-internal).

## Decision

**Option A — production is standalone.** `docker compose -f docker-compose.prod.yml ...` is the
canonical production command; the base+prod merge is **not** a supported production topology.
Do not document or script the merged command.

Add a mandatory deterministic **regression check** that renders the exact merged
`docker-compose.yml` + `docker-compose.prod.yml` configuration and fails if the backend
publishes any host port (specifically `8000`), asserting Caddy publishes only `80`/`443` and
the backend only `expose`s `8000`. Extend the existing `tools/release_check.py` renderer/checks
and the CI `validate` job rather than adding parallel tooling.

## Deployment scripts

Provide `build-docker.{sh,bat}` and `start-server.{sh,bat}` accepting `dev|netcup|prod`
(`netcup`/`prod` → production). They must fail fast, validate the target, print the
target/configuration, use Docker Compose v2, preserve persistent volumes, never run
`down -v`/prune, never auto-push images, and return non-zero on failure. `start-server`
prints `docker compose ... ps` after startup. Production start/build use the standalone
prod file.

## Credential hygiene

Joints: tracked QA evidence under `screenshots/evidence/` contains application-issued bearer
token fields (`creatorAccessToken` / `playthroughAccessToken` / `anonymousSessionToken`,
`token_urlsafe` values). These are short-lived (24h/4h TTL) with no revocation API; the
submission requirement is to **redact/remove** them from the current state (no history
rewrite), extend the release scan to detect the application's bearer-token field patterns,
and exclude QA evidence from the Docker build context.

## Documentation

README/SUBMISSION/PRIVACY/THIRD_PARTY/CREDITS are reconciled with the implementation: fix the
README malformed literal and document the canonical production command/scripts; record the
intended demo URL (canonical value only — never invented); document the hosted-vs-local
provider data flow and that the provider API key is used per-request and not stored; add the
direct runtime/bridge/e2e third-party deps (`certifi`, `websockets`, `@playwright/test`) and
correct the `httpx` runtime classification.

## Preserved invariants

No generation/solver/difficulty/Phase 44–45 changes; no dependency upgrades; no history
rewrite; `REQUIREMENTS.md` unchanged; no new provider architecture; persistent volumes and
existing application data are preserved.

## Phase 46 outcome and findings

Findings raised during QA/adversarial review, final disposition:

| Finding | Severity | Disposition | Resolution |
|---|---|---|---|
| Production port exposure (base+prod merge inherits backend `8000:8000`) | — | CLOSED | Option A standalone; `check_prod_merge_effective_config` reports the merged inheritance and hard-fails any prod-file/unexpected host port or `network_mode: host` |
| QA token hygiene (tracked app bearer tokens) | — | CLOSED | 4 files / 7 fields redacted (short-lived, expired); `scan_app_bearer_tokens` added |
| Docker build context (`screenshots/evidence/` included) | — | CLOSED | `.dockerignore` + required-pattern gate (evidence + coverage excluded; runtime inputs kept) |
| Submission docs / README literal / SUBMISSION URL | — | CLOSED | README fixed + generation-mode + production-command docs; SUBMISSION URL = canonical README value |
| Privacy disclosure | — | CLOSED | hosted-vs-local data flow, key not stored, retention documented |
| Third-party inventory | — | CLOSED | bridge deps + `@playwright/test` added; `httpx` runtime classification corrected |
| `DEF-110` (real regression: dockerignore tests) | MEDIUM | CLOSED | canonical required-body fixtures; non-vacuous check preserved |
| `DEF-111`/`ADV-46-01` (real-token derivative in a test) | MEDIUM | CLOSED | synthetic token only |
| `DEF-112`/`ADV-46-05` (docs path) | LOW | CLOSED | corrected |
| `DEF-113`/`ADV-46-04` (coverage not dockerignored) | LOW | CLOSED | `.coverage.*`/`coverage.xml` added |
| `ADV-46-02` (scan evasion) | LOW | CLOSED | association scan covers the app's real evidence format + realistic variants |
| `ADV-46-03` (merge guard non-backend port) | LOW | CLOSED | any unexpected host port / `network_mode: host` fails |
| `ADV-46-06` (deliberately-obfuscated token serializations) | LOW | REJECTED (documented) | not reachable from the app's own JSON evidence format and no such value in the tree; defense-in-depth residual |

Outcome: release gate `tools/release_check` ALL 21 checks OK; backend `3429 passed / 1 skipped
/ 1 known pre-existing failure`; root `tests` green except the operator-`.env` env-blocked pair;
frontend typecheck/build green + `2009 passed`; bridge `351 passed`; live standalone production
smoke PASS (`/health` 200 over HTTPS, backend `8000` not host-bound, SQLite persists across
rebuild). **READY FOR VIDEO / FINAL SUBMISSION.**
