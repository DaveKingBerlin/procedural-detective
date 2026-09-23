# Procedural Detective — Operator Maintenance Runbook

Operator-facing maintenance procedures for a live (public / demo) deployment.
Complements `docs/DEPLOYMENT.md` (deployment) and `docs/PRIVACY.md` (privacy
policy + deletion). Phase 20/21 (PD-SEC-05/F-07).

> **Single-writer rule (SQLite):** the backend uses SQLite, which allows ONE
> writer at a time. Run every mutate-on-the-database maintenance step with the
> stack stopped (`docker compose -f docker-compose.prod.yml down`) or otherwise
> guarantee no live container holds a write lock. Never write to the database
> while the app is serving.

---

## 1. Before any maintenance

1. **Back up first.** The simplest safe snapshot is a tar of the whole
   `pd-data` volume with the stack stopped:

   ```bash
   docker compose -f docker-compose.prod.yml down
   docker run --rm -v pd-data:/data -v "%CD%":/backup alpine \
     tar czf /backup/pd-data-backup.tar.gz -C /data .
   ```

   (Linux/macOS: replace `"%CD%"` with `"$(pwd)"`.) Restore = unpack the
   archive back into the volume:

   ```bash
   docker run --rm -v pd-data:/data -v "%CD%":/backup alpine \
     tar xzf /backup/pd-data-backup.tar.gz -C /data
   docker compose -f docker-compose.prod.yml up --build -d
   ```

2. **For single-file operations** (e.g. case deletion) you can back up just the
   database file:

   ```bash
   docker run --rm -v pd-data:/data -v "%CD%":/backup alpine \
     cp /data/procedural_detective.db /backup/procedural_detective.db.$(date +%Y%m%d)
   ```

3. Record the case ids / timestamps you plan to touch before deleting anything.

## 2. Single-case operator deletion (canonical command)

The **only supported** path to delete ONE generated case is the audited
maintenance command (Phase 21 F-07; replaces the old manual trigger-drop SQL):

```text
python -m tools.delete_case <case_id> --yes
```

Behavior (fail-closed, ONE transaction): validates the case exists, deletes the
exact documented table set in foreign-key order, re-creates the immutability
triggers identically, verifies 4/4 before commit, and re-verifies immutability
after deletion. Exit codes: `0` = deleted + verified, `1` = abort (missing
case / mismatch), `2` = refused without `--yes`.

### Tool availability — HOST-side only

The runtime Docker image does **not** ship the `tools/` tree (the `Dockerfile`
copies only `backend/`, `assets/` and the built frontend into the runtime
stage), so the command is **not available inside the container**. Run it on the
**host** from a source checkout whose backend dependencies are installed
(`pip install -e ./backend[dev]`).

The command uses the app's single configuration source, so it points at the
right database by setting `DATABASE_URL` to the SQLite file inside the mounted
`pd-data` volume.

### Workflow

```bash
# 1) Stop the stack (single-writer; no lock contention with the app).
docker compose -f docker-compose.prod.yml down

# 2) Back up the volume or the .db file FIRST (see §1).

# 3a) Linux hosts — the named volume lives at a stable host path, so run
#     directly against it:
DATABASE_URL=sqlite:////var/lib/docker/volumes/pd-data/_data/procedural_detective.db \
  python -m tools.delete_case <case_id> --yes

# 3b) Docker Desktop (Windows/macOS) — no stable host path into the volume;
#     use the portable copy-out -> run -> copy-back flow:
docker run --rm -v pd-data:/data -v "%CD%":/work alpine \
  cp /data/procedural_detective.db /work/pd-maintenance.db
DATABASE_URL=sqlite:///<forward-slash-absolute-path>/pd-maintenance.db \
  python -m tools.delete_case <case_id> --yes
#     (Windows example: sqlite:///C:/work/pd-maintenance.db)
docker run --rm -v pd-data:/data -v "%CD%":/work alpine \
  cp /work/pd-maintenance.db /data/procedural_detective.db

# 4) Restart the deployment (migrations run automatically on startup).
docker compose -f docker-compose.prod.yml up --build -d
```

> Do **NOT** fall back to a manual `DROP TRIGGER` / raw `DELETE` when the
> command is unavailable: it voids the publication/accusation immutability
> guarantee for the REMAINING active cases. There is no supported "re-run the
> migration to recreate triggers" path — a recorded Alembic head is not re-run,
> so `alembic upgrade head` does NOT recreate removed triggers.

## 3. Full reset (end of demo/hackathon period)

```bash
docker compose -f docker-compose.prod.yml down -v   # removes pd-data too
docker compose -f docker-compose.prod.yml up --build -d
```

`docker compose down -v` first (step 3.1 of `docs/PRIVACY.md`) is reversible
ONLY via a backup — always back up before a wipe.

## 4. Health / readiness

| Probe | Meaning | How |
| --- | --- | --- |
| `GET https://<host>/api/v1/health` | liveness (no DB touch) | `curl -k https://localhost/api/v1/health` |
| `GET https://<host>/api/v1/readiness` | readiness (`SELECT 1` + Alembic at head) | `curl -k https://localhost/api/v1/readiness` |
| `docker compose -f docker-compose.prod.yml ps` | container states/restarts | `docker compose ... ps` |
| `docker inspect <ctr> --format '{{.State.Health.Status}}'` | healthcheck status | per container |

## 5. Logs & rotation

- Container stdout (uvicorn access + Caddy access/error) is bounded by the
  compose `json-file` logging blocks (`10m` x `5`, F-04). Verify the deployed
  runtime enforces them:
  ```bash
  docker inspect procedural-detective --format '{{.HostConfig.LogConfig}}'
  docker inspect procedural-detective-caddy-1 --format '{{.HostConfig.LogConfig}}'
  ```
- Backend application file logs (`PD_FILE_LOGS`/`PD_LOG_FILE`) rotate 5 MB x 3
  backups (`backend/app/core/observability.py`).
- Access/error logs must NEVER contain tokens/secrets. Check for anomalies:
  `docker compose -f docker-compose.prod.yml logs --tail=200`.
- Nothing sensitive is served: private/playthrough responses are
  `Cache-Control: no-store`.

## 6. Production preflight (run before/after deployment changes)

```bash
python -m tools.prod_preflight            # strict ready-to-host verdict
python -m tools.prod_preflight --allow-local   # local smoke only
```

It validates the EFFECTIVE (`.env`-interpolated) production configuration:

- `ENVIRONMENT=production`, `PD_DEV_TRACE=false`, `TRUST_PROXY=true` (Caddy
  edge); a `.env` carrying dev values (from copying `.env.example`) FAILS.
- P-02 timeout envelope: backend deadline < frontend 360s < proxy 420s.
- Docker log bounds present; backend port never publicly published; no
  published Ollama port; production bundle is same-origin/clean.
- `CADDY_DOMAIN` must be a real public domain (fails on `localhost`/empty
  unless `--allow-local`).

The broader hygiene gate (docs/tracked-secrets/build-context/bundle):
`python -m tools.release_check --allow-hosted-placeholders`.

## 7. Volume/storage hygiene (PD-SEC-05)

- Keep `pd-data` private: never expose `/data`, never mount the volume into a
  Docker build context, never serve it through the edge.
- Wipe the environment (`down -v`) at the end of the demo period or before
  handing the box over.
- Back up through a local volume mount only (`alpine` tar/cp), never the
  public edge.