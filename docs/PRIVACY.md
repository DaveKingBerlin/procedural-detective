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

None of this is stored in browser storage; it is server-side only.

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

### 3.2 Single-case deletion — advanced operator procedure

A single generated case and *its own* playthroughs/accusations can be removed
by an operator. Two caveats (by design, not a bug):

- SQLite **immutability triggers** protect ``published_versions`` and
  ``accusations`` (this is the publication-immutability guarantee —
  REQUIREMENTS/Phase 7). A plain ``DELETE`` on those tables is **aborted** at
  the database level. Removing a published case therefore requires dropping
  the trigger first — **which voids that guarantee for the deleted rows.**
- Only an operator with full control of the deployment should do this; it is
  never exposed to players.

Steps (run inside the running backend container):

```bash
docker compose -f docker-compose.prod.yml exec procedural-detective python - <<'PY'
import sqlite3, sys

DB = "/data/procedural_detective.db"
CASE_ID = sys.argv[1] if len(sys.argv) > 1 else input("case_id to delete: ").strip()

con = sqlite3.connect(DB)
cur = con.cursor()
cur.row_factory = sqlite3.Row

# 0) Confidentiality of the operation: nothing is auto-committed until verified.
# 1) Authoritative-only triggers: published-payload/accusation immutability is
#    DB-enforced; removing a published case requires dropping those guards.
cur.execute("DROP TRIGGER IF EXISTS published_versions_no_delete")
cur.execute("DROP TRIGGER IF EXISTS accusations_no_delete")

# 2) Delete in foreign-key order (child -> parent).
cur.execute("DELETE FROM player_knowledge WHERE playthrough_id IN "
            "(SELECT playthrough_id FROM playthroughs WHERE case_id = ?)", (CASE_ID,))
cur.execute("DELETE FROM accusations      WHERE case_id = ?", (CASE_ID,))
cur.execute("DELETE FROM playthroughs     WHERE case_id = ?", (CASE_ID,))
cur.execute("DELETE FROM creator_credentials WHERE case_id = ?", (CASE_ID,))
cur.execute("DELETE FROM generation_attempts WHERE case_id = ?", (CASE_ID,))
cur.execute("DELETE FROM published_versions WHERE case_id = ?", (CASE_ID,))
cur.execute("DELETE FROM case_versions     WHERE case_id = ?", (CASE_ID,))
cur.execute("DELETE FROM cases             WHERE case_id = ?", (CASE_ID,))

print("rows deleted for", CASE_ID, "->", con.total_changes)
con.commit()
con.close()
PY
```

Verify before/after with `SELECT count(*) FROM cases;`. Restarting the backend is
not required. Dropped triggers are recreated only by applying migrations again
(``alembic upgrade head`` re-runs are idempotent and recreate missing triggers)
— do **not** run this procedure on a live contest deployment while immutability
of active cases must be preserved.

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

- The operator note pointing here lives in ``.env.example`` (PD-SEC-05), and
  this policy is linked from ``docs/DEPLOYMENT.md`` and the README security
  section. Deployments that surface a notice to users near prompt submission
  should link to this document.
- Nothing in this policy overrides the operator's own obligations; it is the
  project's honest statement of the current build's behavior.