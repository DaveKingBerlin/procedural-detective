# Procedural Detective — Operator Maintenance Runbook

Operator procedures for the supported single-node production deployment.
This complements `docs/DEPLOYMENT.md` and `docs/PRIVACY.md`.

> **Phase 30 — BYOK Frontier (operator note).** `FRONTIER_ENABLED=true` is the
> BYOK feature switch: players select a hosted provider from the **trusted
> server-owned registry** (openai / openrouter / groq / together / mistral /
> fireworks / deepinfra / xai) and supply their own API key + model per
> generation attempt. The server maps the provider ID to the verified official
> HTTPS endpoint — the **browser never supplies a URL**. Operators do **not**
> configure server-funded Frontier credentials for browser use: the legacy
> `FRONTIER_BASE_URL` / `FRONTIER_API_KEY` / `FRONTIER_MODEL` settings are
> documented as **not consumed** by browser BYOK (kept for backward
> configuration compatibility only). Normal production configuration is
> `GENERATION_PROVIDER=fake`, `ENABLE_BRIDGE=true` (optional), `FRONTIER_ENABLED=true`.
> User keys are transient: they are never persisted in the SQLite database,
> never logged, never returned by an API and never appear in monitoring input
> (the backend test suite proves this with a mandatory sentinel secret scan).
> Provider failures are normalized to the closed `FRONTIER_*` vocabulary so an
> operator's log inspection stays secret-safe (only safe metadata such as
> `provider="frontier"`, `frontierProvider=<id>` and the safe model is emitted).

> **Single-writer rule:** stop every container that can write the SQLite
> volume before backup, restore, or database maintenance. The audited tooling
> checks the actual Docker mount state and fails closed when a writer is still
> running.

## 1. Audited backup and restore

Docker Compose derives the concrete data-volume name from its effective
project name. Never type or guess that runtime name. The audited command reads
the actual `docker compose config` render, verifies that the resolved volume
already exists, and refuses to back up while any container using it is not
stopped.

1. Stop the application while retaining its containers and volume:

   ```bash
   docker compose -f docker-compose.prod.yml stop
   ```

2. Create a backup in an operator-controlled directory outside the source
   checkout:

   ```bash
   python -m tools.backup_production backup \
     --output-dir /secure/backups/procedural-detective
   ```

   On Windows, supply an absolute Windows path to `--output-dir`. Python cannot
   portably verify Windows ACLs, so create that directory with access limited
   to the operator account and verify its ACL before use. On POSIX hosts the
   tool creates/requires an operator-owned mode `0700` directory and verifies
   that the finished archive is operator-owned mode `0600`. Success prints the
   concrete volume that was verified, the timestamped archive, its SHA-256,
   and the checksum sidecar. The command exits non-zero when the
   volume is absent, ambiguous, attached to a running container, or lacks the
   expected non-empty SQLite database. No verified backup exists unless the
   command exits 0 and both printed files exist.

   The examples use Compose's automatic project `.env`. If the deployment was
   started with explicit Compose options, pass the **same** values to every
   maintenance command. For example:

   ```bash
   python -m tools.backup_production backup \
     --output-dir /secure/backups/procedural-detective \
     --env-file /secure/config/production.env \
     --project-directory /srv/procedural-detective
   ```

   The same `--env-file`, `--project-directory`, and optional `--project-name`
   must be supplied to restore. They are forwarded to Compose before it
   renders the concrete project and volume. A missing explicit file or project
   directory fails closed. Do not use maintenance options that differ from the
   real startup invocation.

3. Restore only to an empty stopped volume. After an intentional removal such
   as `docker compose -f docker-compose.prod.yml down -v`, run:

   ```bash
   python -m tools.backup_production restore \
     /secure/backups/procedural-detective/<timestamped-archive>.tar.gz \
     --yes --create-volume
   docker compose -f docker-compose.prod.yml up --build -d
   docker compose -f docker-compose.prod.yml exec procedural-detective \
     python -m alembic -c /app/backend/alembic.ini current
   ```

   Restore verifies the checksum sidecar and every archive path before
   touching Docker. `--create-volume` may create only the concrete name
   resolved from Compose; backup never creates a volume. Restore refuses a
   non-empty target, so it cannot silently merge data into an existing volume.

Before any destructive action, record the archive path and SHA-256, confirm
the backup command exited 0, and record the case ids/timestamps you intend to
touch. Keep this confirmation separate from the destructive command.

## 2. Single-case operator deletion

The audited deletion unit remains:

```text
python -m tools.delete_case <case_id> --yes
```

It validates the case, deletes the documented rows in one transaction,
re-creates and verifies all four immutability triggers before commit, and
checks the guards again after deletion. Exit codes are `0` for deleted and
verified, `1` for an abort or mismatch, and `2` when `--yes` is absent.

The runtime image does not contain the repository `tools/` tree. Production
therefore bind-mounts the current source checkout read-only into a one-off
container created from the application service. Compose attaches the same
effective data-volume declaration as the stopped backend. No runtime volume
name or Docker host storage path is used.

### Bash / Linux / macOS

```bash
# 1) Stop all writers but retain containers and volume.
docker compose -f docker-compose.prod.yml stop

# 2) Create and verify the backup first.
python -m tools.backup_production backup \
  --output-dir /secure/backups/procedural-detective

# 3) Delete exactly one case through the audited command.
docker compose -f docker-compose.prod.yml run --rm --no-deps \
  --entrypoint python --workdir /maintenance \
  --volume "$(pwd):/maintenance:ro" \
  procedural-detective -m tools.delete_case <case_id> --yes

# 4) Restart and verify readiness.
docker compose -f docker-compose.prod.yml up --build -d
curl --fail https://<public-host>/api/v1/readiness
```

### PowerShell / Windows

Run the backup step above with an absolute Windows output directory, then:

```powershell
docker compose -f docker-compose.prod.yml run --rm --no-deps `
  --entrypoint python --workdir /maintenance `
  --volume "${PWD}:/maintenance:ro" `
  procedural-detective -m tools.delete_case <case_id> --yes
docker compose -f docker-compose.prod.yml up --build -d
```

Do not use manual `DROP TRIGGER`, raw `DELETE`, or a host path under Docker's
internal volume directory. A recorded Alembic migration head is not re-run and
does not recreate manually removed triggers.

## 3. Full reset

First complete §1 and verify the timestamped archive and checksum. Then, as a
separate explicitly confirmed operator action:

```bash
docker compose -f docker-compose.prod.yml down -v
docker compose -f docker-compose.prod.yml up --build -d
```

`down -v` removes the real project-scoped data volume. It is reversible only
through the verified restore command in §1. Do not put backup and `down -v` in
one command chain, script, or unattended job.

## 4. Tested backup-delete-restore proof

Before public cutover, use a disposable Compose project and database:

1. insert a unique known row and record the four immutability triggers;
2. stop the disposable stack;
3. run `tools.backup_production backup` and record its checksum;
4. remove only the disposable project's volume;
5. run `tools.backup_production restore ... --yes --create-volume`;
6. restart the disposable stack;
7. assert the known row and all four triggers are present.

Never use production data for the first restore exercise. The unit tests mock
Docker and prove fail-closed command behavior; this disposable roundtrip is the
required runtime proof that the host Docker engine, storage driver, image, and
filesystem work together.

## 5. Health and readiness

| Probe | Meaning | How |
| --- | --- | --- |
| `GET https://<host>/api/v1/health` | liveness | `curl --fail https://<host>/api/v1/health` |
| `GET https://<host>/api/v1/readiness` | DB and migration readiness | `curl --fail https://<host>/api/v1/readiness` |
| `docker compose -f docker-compose.prod.yml ps` | container states | run on the host |
| `docker inspect <container> --format '{{.State.Health.Status}}'` | healthcheck status | per container |

## 6. Logs and rotation

- Compose bounds container logs with the `json-file` driver (`10m` × `5`).
  Resolve container ids with `docker compose ... ps -q`; do not guess names.
- Backend application logs rotate at 5 MB × 3 backups.
- Access and error logs must not contain credentials or bearer tokens.
- Private and playthrough responses use `Cache-Control: no-store`.

## 7. Production preflight

```bash
python -m tools.prod_preflight                 # strict public-host gate
python -m tools.prod_preflight --allow-local   # local smoke only
python -m tools.prod_preflight --env-file /secure/config/production.env
```

The strict command validates the actual rendered production Compose
configuration, including shell overrides, production environment flags,
timeouts, bounded logs, private backend/Ollama ports, same-origin frontend,
and a real `CADDY_DOMAIN`. Never use `--allow-local` as public release
evidence.

The broader repository gate is:

```bash
python -m tools.release_check --allow-hosted-placeholders
```

## 8. Volume and backup hygiene

- Keep `/data` and the Compose data volume private and off the public edge.
- Store archives outside the source checkout with operator-only permissions.
  A backup contains raw prompts, private CaseTruth, token verifiers, and all
  player state.
- Retain the `.sha256` sidecar with its archive and test restore before relying
  on the archive for recovery.
- Securely remove archives when their approved retention period ends.

## 9. Docker start / stop / logs / volume (Phase 24)

Local deterministic development (`GENERATION_PROVIDER=fake` default):

```bash
# start (builds the multi-stage image from a clean checkout)
docker compose --env-file compose/profiles/dev.env up --build -d

# logs (bounded: json-file 10m x 5)
docker compose logs --tail=100 -f procedural-detective

# stop (keeps the pd-data volume)
docker compose stop

# full teardown of THIS project (removes its volume; back up first)
docker compose down -v
```

CI smoke uses the hermetic profile (fake + bridge-off + the repo's
deterministic fake world), always under a unique project name:

```bash
docker compose -f docker-compose.yml -f docker-compose.ci.yml \
  --env-file compose/profiles/ci.env -p "pd-ci-<pipeline>-<job>" up -d --build
python -m tools.ci_wait_ready --base-url http://127.0.0.1:8000
python -m tools.docker_smoke --base-url http://127.0.0.1:8000
docker compose -f docker-compose.yml -f docker-compose.ci.yml \
  -p "pd-ci-<pipeline>-<job>" down -v --remove-orphans
```

Production-like local smoke (Caddy edge, localhost, internal CA):

```bash
docker compose -f docker-compose.prod.yml --env-file compose/profiles/prod-like.env \
  up --build -d
curl -k https://localhost/api/v1/health
python -m tools.prod_preflight --allow-local
```

**Isolation rule (§7/§50):** every CI Docker job must use a unique Compose
project (`pd-ci-$CI_PIPELINE_ID-$CI_JOB_ID`). Cleanup targets ONLY that
project (containers, network, volume); never run `docker system prune -af` on a
shared runner, and never `down` another project. Migrations run automatically
on an empty volume (`alembic upgrade head` in the entrypoint before uvicorn);
no manual migration step is required.

## 10. Environment profiles and real-Ollama development (Phase 24)

Profiles live in `compose/profiles/` (basenames deliberately avoid the `.env.*`
family; the release gate reserves that family for operator-local secret files):

- `dev.env` — local deterministic development (fake, bridge off);
- `ci.env` — CI deterministic (fake, bridge off), used by every ordinary
  pipeline;
- `prod-like.env` — production-like local smoke (fake, bridge off, localhost
  Caddy domain);
- `ollama.env` — real-Ollama acceptance documentation (the private
  `OLLAMA_BASE_URL` / `OLLAMA_MODEL` values are NEVER committed and belong only
  to the operator environment / GitLab CI/CD variables).

Real-Ollama local development (server-local mode, Phase 16.2):

```bash
export GENERATION_PROVIDER=ollama
export OLLAMA_BASE_URL=http://127.0.0.1:11434      # or the private LAN host
export OLLAMA_MODEL=hermes3:8b                       # operator-chosen model
export OLLAMA_TIMEOUT_SECONDS=180
export CASE_GENERATION_DEADLINE_SECONDS=300
python -m tools.ollama_smoke --enable --full-chain   # opt-in real smoke
```

If `GENERATION_PROVIDER=ollama` and Ollama is unavailable the product FAILS
TRUTHFULLY and typed — there is NO silent fallback to the fake provider. The
real-Ollama acceptance runner (`tools.real_ollama_regression`, §34/§35) applies
the same rule.

## 11. GitLab Runner + pipeline (Phase 24)

Full guide: `docs/CI.md`. Short form:

- Dedicated Linux host/VM, trusted shell executor, Docker Engine + Compose
  plugin, Python 3.12, Node, Git. Tags: `docker` (all hermetic Docker jobs) and
  `real-ollama` (operator-triggered real-AI jobs, private access to Ollama).
- Pipeline stages: `validate -> test -> build -> docker -> smoke -> real-ai`.
- Normal push/MR pipelines are hermetic (`GENERATION_PROVIDER=fake`,
  `ENABLE_BRIDGE=false`); the real-AI job is schedule/manual only.
- GitLab CI/CD variables (masked/protected, never echoed, never committed):
  `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `CADDY_DOMAIN`, `CADDY_EMAIL`.
- Bounded artifacts only: test reports, `docker-smoke.log`,
  `docker-smoke-report.json`, release-check output. Never `.env`, endpoints,
  tokens, databases, CaseTruth, prompts or provider output.
