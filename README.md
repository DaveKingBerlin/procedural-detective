# Procedural Detective

**AI-native browser-based 3D detective game that turns natural-language crime prompts into logically consistent, interactive investigations.**

Describe a crime in plain language. Procedural Detective parses it into a hidden
ground truth, generates a coherent world of suspects, evidence and red herrings,
*makes sure the case is provably solvable* — then drops you into a 3D apartment
to investigate, accuse and reveal the truth.

---

## What makes it different

> **AI can generate a world. Procedural Detective makes sure that world has a truth.**

Most AI "world generators" stop at impressive prose or pretty scenes. This game
adds the part AI is bad at: **objective validation**. The generated case must
pass a deterministic solver that proves, from the evidence a player can actually
discover, that there is exactly ONE solvable answer — murderer, motive, weapon
and time. If the generated world has no provable truth, it is never published.

## Screenshots

Captured evidence from the Phase 8 demo build (`screenshots/evidence/`):

- Landing / prompt screen — `screenshots/evidence/phase8-landing.png`
- Generation progress — `screenshots/evidence/phase8-generation-progress.png`
- 3D apartment scene — `screenshots/evidence/phase8-apartment-scene.png`
- Evidence inspection — `screenshots/evidence/phase8-evidence-inspection.png`
- Accusation screen — `screenshots/evidence/phase8-accusation-screen.png`
- Reveal screen — `screenshots/evidence/phase8-reveal-screen.png`

## Architecture overview

```text
            Browser (React / TypeScript / Babylon.js 3D)
                          │  REST (same-origin on :8000)
                          ▼
                    FastAPI backend
                          │
        ┌─────────────────┼──────────────────┐
        ▼                 ▼                  ▼
   Prompt → CaseTruth  Evidence/World    Deterministic
        │  (ground truth)  generation     VALIDATION
        │                 │               (unique-solution solver)
        ▼                 ▼                  ▼
        └───────── durable SQLite ──────────┘
                          │
                          ▼
        Player: investigate → accuse → reveal the immutable truth
```

One container serves the SPA **and** the API (single origin), with a durable
SQLite volume and migrations running automatically on startup.

## AI-native workflow

```text
Prompt
  → CaseTruth (immutable ground truth, user constraints locked)
  → Evidence / World generation (typed, validated, allowlisted)
  → deterministic validation (exactly one provable solution)
  → 3D investigation (Babylon.js scene, discoverable evidence)
  → accusation / reveal
```

## Local setup

Requirements: Python 3.12+, Node 20+.

Run every backend command below from the **repo root** (paths like
`backend/...` are root-relative). A bare `cd backend` would make them fail
(`./backend[dev]`, `backend/alembic.ini` and the frontend path all resolve
differently from that directory).

```bash
# Backend (all from the REPO ROOT)
python -m pip install -e "./backend[dev]"
python -m alembic -c backend/alembic.ini upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Frontend (second terminal, also from the REPO ROOT)
cd frontend
npm install
npm run dev        # http://localhost:5173
```

Demo flow: the frontend opens at `http://localhost:5173`. Click **New
Investigation**, enter any prompt (or the example Sarah Miller case), watch the
generation progress, then enter the 3D scene, inspect the knife / laptop / email,
make an accusation (WHO / WHY / WEAPON / WHEN) and reveal the truth.

`GENERATION_PROVIDER=fake` is the default: the deterministic offline demo
provider answers every prompt with the shipped, fully validated golden case —
zero credentials, zero cost (`LLM_API_KEY` etc. are placeholders in
`.env.example`, never real values).

### Troubleshooting — "Demo errors" / "Try Demo Case fails"

1. **Generation dashboard shows a failure** — check the backend is up and its
   database is migrated:
   `http://127.0.0.1:8000/api/v1/readiness` should return
   `{"status":"ready","database":"ok","migrations":"ok"}`. If migrations are
   wrong or missing, run `python -m alembic -c backend/alembic.ini upgrade head`
   from the repo root, then restart uvicorn.
2. **Open the frontend at `http://localhost:5173`** — not `127.0.0.1:5173`.
   The default CORS allowlist is `http://localhost:5173`; requests from any
   other origin are rejected by the backend.
3. **Repeated quick demo re-runs can hit the in-memory global generation quota**
   (HTTP `429 ADMISSION_DENIED`) until the backend restarts. Restart uvicorn
   for a fresh demo window.

## One-shot demo launcher

The fastest way to play the demo is **one command** (Windows):

```bash
.\scripts\start-demo.cmd
```

or, from PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-demo.ps1
```

The launcher installs backend/frontend dependencies and builds the SPA only
when needed, applies database migrations, starts **ONE server** on
`http://localhost:8000` serving the built app **and** the API (single origin —
no CORS, no second terminal), and opens your browser. Press **Enter** (or
Ctrl+C) to stop it cleanly. The default `GENERATION_PROVIDER=fake` demo
provider needs no credentials (zero network, zero cost).

Options:

- `-NoBrowser` — do not open the browser automatically (for headless runs)
- `-Port <n>` — serve on a different port (default `8000`)
- `-Rebuild` — force a fresh `npm run build` of the frontend
- `-Stop` — stop the demo recorded in `<repo>\.pd-demo-meta`, then exit

If the port is already in use, the launcher prints a clear message and exits
`1` (run `.\scripts\stop-demo.ps1` first or pass `-Port <other>`). Stop a
running demo from another terminal with `.\scripts\stop-demo.ps1` (a safe
no-op when nothing is running). All launches/stops go through the repository's
identity-verified lifecycle guard (`tools/process_guard`), so no stray
listeners are left behind.

## Docker setup

```bash
docker compose up --build
# → http://localhost:8000  (health: /api/v1/health, readiness: /api/v1/readiness)
```

The multi-stage image builds the frontend (Node 24) and the backend
(Python 3.12), runs as a non-root user, persists SQLite to the `pd-data`
volume, and runs `alembic upgrade head` before the server starts. See
`docs/DEPLOYMENT.md` for the full production configuration.

## Environment configuration

Copy `.env.example` to `.env` — it documents every canonical variable
(REQUIREMENTS §45). Key variables:

| Variable | Purpose | Default |
| --- | --- | --- |
| `DATABASE_URL` | SQLAlchemy URL | `sqlite:///<repo>/procedural_detective.db` (local) / `sqlite:////data/procedural_detective.db` (container) |
| `GENERATION_PROVIDER` | `fake` demo or `live` LLM | `fake` |
| `LLM_API_KEY` / `LLM_MODEL` / `LIVE_PROVIDER_URL` | live-mode credentials (never committed) | unset |
| `CORS_ALLOWED_ORIGINS` | comma-separated allowlist (`*`/`null` rejected) | `http://localhost:5173` |
| `MAX_*`, `*_TTL_SECONDS` | generation budgets, token TTLs | see `.env.example` |
| `STATIC_DIR` | built frontend directory (SPA serving) | `/app/static` (container) |

## Testing

```bash
# Backend suite (~600 tests): domain solvers, generation lifecycle, API
# contracts, authorization/isolation, leak scanners, cache-safety & deployment polish
cd backend && python -m pytest -q

# Root tooling suite (50 tests + adapter-drift check)
python -m pytest tests/ -q
python tools/generate_adapters.py --check --all

# Frontend (owned by the parallel UI track)
cd frontend && npm test && npm run typecheck && npm run build

# End-to-end Playwright specs (e2e/)
#   boot.spec.ts — landing → prompt → generation → investigation
#   investigation-golden.spec.ts — knife/laptop/email discovery in the 3D scene
#   accusation-reveal-golden.spec.ts — accusation → reveal → reload persistence
#   backend-down.spec.ts / degraded.spec.ts — graceful degraded behavior
```

## Security / truth-independence overview

- **CaseTruth never reaches the client before reveal.** Every pre-reveal
  payload is an allowlisted DTO; the automated suite recursively scans every
  nesting level for hidden truth, solver internals and tokens.
- **Reveal is gated**: `GET /playthroughs/{id}/reveal` is allowed only from
  `{ACCUSED, REVEALED}` (frozen REQUIREMENTS 40.12), transitions
  `ACCUSED → REVEALED` idempotently, and is served `Cache-Control: no-store`.
- **Deterministic solver independence**: deduction uses only the public case +
  discoverable evidence — never the hidden answer. The generated case is
  published only when exactly one survivor remains for every accusation
  dimension.
- The project runs an **adversarial, QA-gated engineering process with a
  closed-defect ledger at every gate** (independent adversarial review, QA-only
  defect closure, cumulative regression — no fix ships without a passing
  gate); deployment specifics and the full security posture are in
  `docs/DEPLOYMENT.md`.

## Tech stack

- **Backend:** Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 · Alembic · SQLite
- **Frontend:** React · TypeScript · Vite · Babylon.js (3D)
- **Testing:** pytest (backend) · Vitest (frontend) · Playwright (browser E2E)
- **Deployment:** Docker (multi-stage, non-root, SQLite volume)

## Hackathon context

Built for the **Victoria VR AI Builder Hackathon 2026** as Milestone 1: a
complete, reliable prompt-to-truth player journey with deployment and demo
assets — no VR required to play.

## Hosted demo

*Live demo URL lands here after deployment.* See `docs/DEPLOYMENT.md` for the
single-container deployment guide.

## Demo video

*Under-3-minute demo video lands here after recording.* Storyboard:
`docs/demo-storyboard.md`.