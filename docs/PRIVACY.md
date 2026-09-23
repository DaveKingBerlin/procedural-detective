# Procedural Detective — Privacy & Data Retention Policy

Phase 20 (PD-SEC-05). This document is the **explicit privacy/retention
policy** for public (hackathon/demo) hosting. It describes what is stored,
how long it is kept, how an operator deletes it, and how the storage volume is
protected.

---

## 1. What is stored

Procedural Detective persists one SQLite database (``procedural_detective.db``)
in a private Docker volume (``pd-data`` at ``/data`` in the container). For a
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
# 1. Optional: back up first (see §5).
docker run --rm -v pd-data:/data -v "%CD%":/backup alpine \
  tar czf /backup/pd-data-backup.tar.gz -C /data .

# 2. Stop and remove the deployment AND its data volume.
docker compose -f docker-compose.prod.yml down -v

# 3. Start fresh (migrations run automatically on startup).
docker compose -f docker-compose.prod.yml up --build -d
```

The same works for the local dev stack: `docker compose down -v`.

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

#### Where the operator runs it — HOST-side (tool availability)

The runtime Docker image does **not** ship the `tools/` tree (the `Dockerfile`
copies only `backend/`, `assets/` and the built frontend into the runtime
stage), so `python -m tools.delete_case` is **not available inside the
container**. The supported path is **host-side invocation against the mounted
DB volume**, from a source checkout whose backend dependencies are installed
(e.g. `pip install -e ./backend[dev]`).

Procedure (back up FIRST, guarantee a single writer):

```bash
# 1) STOP the stack so the SQLite volume has no live writer (SQLite is
#    single-writer; the app container must not hold a conflicting write lock).
docker compose -f docker-compose.prod.yml down

# 2) BACK UP the whole volume first (non-negotiable before ANY deletion).
docker run --rm -v pd-data:/data -v "%CD%":/backup alpine \
  tar czf /backup/pd-data-backup.tar.gz -C /data .

# 3) Run the audited command ON THE HOST, pointing DATABASE_URL at the SQLite
#    file inside the mounted volume. Linux hosts keep named-volume data under
#    /var/lib/docker/volumes/pd-data/_data/:
#    DATABASE_URL=sqlite:////var/lib/docker/volumes/pd-data/_data/procedural_detective.db python -m tools.delete_case <case_id> --yes
#
#    Docker Desktop (Windows/macOS) does not expose a stable host path into
#    the volume, so use the portable copy-out flow instead (forward-slash
#    absolute path in DATABASE_URL, e.g. sqlite:///C:/work/pd-maintenance.db):
docker run --rm -v pd-data:/data -v "%CD%":/work alpine \
  cp /data/procedural_detective.db /work/pd-maintenance.db
DATABASE_URL=sqlite:///<path-to>/pd-maintenance.db \
  python -m tools.delete_case <case_id> --yes
#    then verify + copy the maintained file back into the volume:
docker run --rm -v pd-data:/data -v "%CD%":/work alpine \
  cp /work/pd-maintenance.db /data/procedural_detective.db

# 4) Restart the deployment (migrations run automatically on startup).
docker compose -f docker-compose.prod.yml up --build -d
```

Restarting is not required after a successful run; the command itself verifies
the triggers. Do **not** fall back to a manual trigger-drop when the command
is unavailable — that voids the immutability guarantee for the remaining
active cases (there is NO supported "recreate by re-applying a migration" path;
a recorded migration head is not re-run). Full workflow, including
backup/restore details and the Linux vs Docker Desktop path difference, is in
**`docs/OPERATIONS.md` §2**.

## 4. Player-facing exposure (what is never served)

- Player APIs never return the **raw prompt** pre-reveal unless intentionally
  public, and never return the **CaseTruth** pre-reveal (enforced by the full
  leak-scanning test suite).
- Private/playthrough responses are served ``Cache-Control: no-store``.
- Access is token-bound and scoped: a playthrough token only ever reaches its
  own playthrough.

## 5. SQLite volume privacy & backups

- The database lives in a **Docker named volume** (``pd-data``), **not** inside
  the image — rebuilds and restarts never touch it, and deleting the volume
  deletes the data.
- The backend is reachable only on the **private compose network** in the
  production profile; its port is **never published to the host or Internet**
  (PD-SEC-03). The database is never exposed directly to browsers or to the
  public edge.
- **Backup inference:** the volume contains the entire dataset. Backup the
  whole volume while the stack is stopped for a consistent snapshot
  (§3.1 step 1); restoring the archive restores all prompts/cases/truth. Never
  back the volume up through the public edge.
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