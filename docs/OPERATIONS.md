# Procedural Detective — Operator Maintenance Runbook

Operator procedures for the supported single-node production deployment.
This complements `docs/DEPLOYMENT.md` and `docs/PRIVACY.md`.

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
