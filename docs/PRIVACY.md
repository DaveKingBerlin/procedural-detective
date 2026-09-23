# Procedural Detective — Privacy & Data Retention Policy

Phase 20 (PD-SEC-05). This document is the **explicit privacy/retention
policy** for public (hackathon/demo) hosting. It describes what is stored,
how long it is kept, how an operator deletes it, and how the storage volume is
protected.

---

## 1. What is stored

Procedural Detective persists one SQLite database (``procedural_detective.db``)
in the private Compose data volume mounted at ``/data``. Docker derives the
concrete runtime volume name from the effective project configuration; the
operator tooling resolves it rather than assuming a literal name. For a
generated case the database stores:

- the **raw prompt** the user submitted (``cases.title``; the full prompt is
  carried inside the published payload — see §2);
- the **public case** (the playable world: scene, objects, evidence,
  accusation candidates);
- the **hidden CaseTruth** (ground truth: murderer, motive, weapon, time and
  solver material) — never served to players before reveal (§4);
- **generation metadata** (attempt status/stage/progress, timestamps, quotas);
- **player state** (per-playthrough discovered/read/visited evidence, notes)
  and the first (immutable) accusation of each playthrough;
- **credential verifiers** — only the SHA-256 digest of each bearer token is
  stored, never a raw token.

### 1.1 Browser local storage (Phase 21 F-06)

The browser may store, in its own `localStorage` (scoped to the deployed
origin the player opened):

- the **scoped playthrough bearer token** (the `playthroughAccessToken`
  returned when a playthrough is created) — scoped to that single playthrough;
- the **playthrough ID**; and
- **notebook hypothesis pins** (which hypothesis rows the player pinned).

Clarifications:

- `localStorage` **persists across page reloads and browser restarts** — it is
  durable local browser state, not a session cookie.
- **Shared-device implications:** anyone using the same browser profile on that
  device can **resume or edit the player's playthrough and see their pinned
  hypotheses**. On a shared machine, play in a **private/incognito window** or
  **clear the site data** before handing the device over.
- **Token expiry scope:** the server-side token is scoped to its playthrough
  and expires after `PLAYTHROUGH_TOKEN_TTL_SECONDS`; the browser's copy remains
  in `localStorage` until the site data is cleared (or the token is otherwise
  invalidated server-side).
- **Clearing site data** (browser settings / "Clear site data" for the origin)
  removes all of the above local browser state.

None of the server-side database contents (§1) are stored in the browser — the
three items above are the complete local browser state this build writes. A
legacy `pd_generation_mode` key may persist in a browser that once used an
older build (or a QA seam); it is only read and re-validated for backwards
compatibility — it never drives the provider — and it is removed by the same
"clear site data" action. Do not overstate what the backend stores: the browser
copy is local to the device and profile, while the database keeps the
server-side records described in §1.

## 2. Retention period

> **Retention policy (honest, operator-bounded):** generated case data —
> raw prompt, public case, hidden truth, generation metadata and player state —
> is retained **for the duration of the hackathon/demo period and is then
> eligible for deletion by the operator.** This deployment does **NOT**
> implement automatic deletion; nothing is deleted automatically. Deletion is
> an operator action (§3).

Only the operator chooses when a period ends and what happens next. Do not
claim automatic expiry — no retention job exists in this build.

## 3. Operator deletion procedure

Two supported procedures. The **full reset** is the recommended hackathon
control; the **single-case procedure** is an advanced operator operation that
requires database-level control.

### 3.1 Full reset — recommended for the hackathon/demo period end

Removes **everything** (all prompts, cases, truths, playthroughs, quotas):

```bash
# 1. Stop all SQLite writers but retain the current volume.
docker compose -f docker-compose.prod.yml stop

# 2. Create a verified backup outside the source checkout. Record the printed
#    archive path and SHA-256 and require exit code 0 before continuing.
python -m tools.backup_production backup \
  --output-dir /secure/backups/procedural-detective

# 3. As a separate, explicitly confirmed action, remove the deployment and
#    its actual project-scoped data volume.
docker compose -f docker-compose.prod.yml down -v

# 4. Start fresh (migrations run automatically on startup).
docker compose -f docker-compose.prod.yml up --build -d
```

Do not combine backup and `down -v` in one command chain or unattended job.
The backup contains all private data and remains subject to this retention
policy; securely erase it when its approved recovery period ends. Restoring a
wipe uses the verified command in `docs/OPERATIONS.md` §1. When startup uses an
explicit Compose `--env-file`, `--project-directory`, or `--project-name`, pass
the identical options to backup and restore so they resolve the same concrete
project volume.

### 3.2 Single-case deletion — canonical audited command

A single generated case and *its own* playthroughs/accusations are removed by
the **audited maintenance command** (Phase 21 F-07), which replaces the old
manual raw-SQL trigger-drop procedure (removed — it voided immutability and its
"`alembic upgrade head` recreates dropped triggers" claim is NOT reliable once
the migration head is already recorded; migration re-runs do not re-run a
recorded head).

```text
python -m tools.delete_case <case_id> --yes
```

What the command does (in ONE transaction, fail-closed):

1. validates the target `cases` row exists (a missing case aborts, changes
   nothing — even with `--yes`);
2. deletes in foreign-key order the exact table set of the documented
   procedure (`player_knowledge`, `accusations`, `playthroughs`,
   `creator_credentials`, `generation_attempts`, `published_versions`,
   `case_versions`, `cases`) via `Store.delete_case_cascade` — the single
   audited deletion unit;
3. drops only the two publication/accusation immutability guards, runs the
   deletion, then **re-creates the triggers identically and verifies 4/4**
   before committing — any failure rolls the whole operation back to the
   original guard set (SQLite DDL is transactional);
4. verifies immutability protections still hold AFTER the deletion (a raw
   UPDATE/DELETE on a remaining published case / accusation is still rejected
   at the database level).

It NEVER runs against the default/repository database unless `DATABASE_URL`
points there — it uses the exact same single configuration source as the app.

#### Where the operator runs it

The runtime image does **not** ship the repository `tools/` tree. The supported
production procedure stops every writer, creates a verified backup, and then
uses `docker compose run` with the source checkout mounted read-only. Compose
attaches the application's effective data-volume declaration automatically;
the procedure never guesses a runtime volume name or uses Docker's private
host storage path.

```bash
# 1) Stop writers but retain the containers and data volume.
docker compose -f docker-compose.prod.yml stop

# 2) Back up first; continue only after exit 0 and checksum verification.
python -m tools.backup_production backup \
  --output-dir /secure/backups/procedural-detective

# 3) Run the audited deletion in a one-off application service container.
docker compose -f docker-compose.prod.yml run --rm --no-deps \
  --entrypoint python --workdir /maintenance \
  --volume "$(pwd):/maintenance:ro" \
  procedural-detective -m tools.delete_case <case_id> --yes

# 4) Restart the deployment and verify readiness.
docker compose -f docker-compose.prod.yml up --build -d
curl --fail https://<public-host>/api/v1/readiness
```

PowerShell uses `--volume "${PWD}:/maintenance:ro"`; see
`docs/OPERATIONS.md` §2 for the complete platform examples. Do **not** fall
back to manual trigger drops or raw SQL. A recorded migration head is not
re-run, so it cannot repair manually removed immutability triggers.

## 4. Player-facing exposure (what is never served)

- Player APIs never return the **raw prompt** pre-reveal unless intentionally
  public, and never return the **CaseTruth** pre-reveal (enforced by the full
  leak-scanning test suite).
- Private/playthrough responses are served ``Cache-Control: no-store``.
- Access is token-bound and scoped: a playthrough token only ever reaches its
  own playthrough.

## 5. SQLite volume privacy & backups

- The database lives in the **Compose-managed data volume**, **not** inside
  the image. Rebuilds and restarts preserve it; deleting that volume deletes
  the data. The concrete runtime name is obtained from the effective Compose
  render by `tools.backup_production`, never assumed by an operator command.
- The backend is reachable only on the **private compose network** in the
  production profile; its port is **never published to the host or Internet**
  (PD-SEC-03). The database is never exposed directly to browsers or to the
  public edge.
- **Backup inference:** the volume contains the entire dataset. Stop the stack
  and use `python -m tools.backup_production backup` for a consistent,
  checksum-verified archive. Restoring that archive restores prompts, cases,
  CaseTruth and player data, so archives require the same privacy controls and
  retention decisions as the live database. Never transfer a backup through
  the public edge.
- Operators who cannot wipe at contest end should state the shorter ad-hoc
  retention they actually guarantee instead of relying on automatic expiry
  (there is none).

## 6. Privacy notice location

- The operator note pointing here lives in ``.env.example`` and the production
  profile example ``.env.production.example`` (PD-SEC-05), and this policy is
  linked from ``docs/DEPLOYMENT.md`` and the README security section. The
  concrete maintenance runbook is ``docs/OPERATIONS.md`` (backup/restore,
  deletion, log/hygiene checks). Deployments that surface a notice to users
  near prompt submission should link to this document.
- Nothing in this policy overrides the operator's own obligations; it is the
  project's honest statement of the current build's behavior.
