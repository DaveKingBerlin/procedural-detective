# Procedural Detective — Hackathon Submission

**Project:** Procedural Detective

**Short description:**
AI-native browser-based 3D detective game that turns natural-language crime
prompts into logically consistent, interactive investigations. Type a crime in
plain English; the game generates a world, then *proves the case is solvable*
before you investigate, accuse and reveal the truth in 3D.

**Repository:** <https://github.com/DaveKingBerlin/procedural-detective>

**Hosted demo URL:** `TBD after hosting is chosen` (hosting may not exist yet;
the deployment target is a single container — see `docs/DEPLOYMENT.md`)

**Demo video URL:** `TBD after recording` (storyboard reference:
`docs/demo-storyboard.md`)

**Focus / category:** AI Gaming / AI-powered game creation

## Core innovation

AI can generate a world. Procedural Detective makes sure that world has a
truth. The generated case must pass a **deterministic solver** that proves,
from the evidence the player can actually discover, that there is exactly ONE
solvable answer — murderer, motive, weapon and time. A world with no provable
truth is never published.

## How AI is used (honest)

- **Default: deterministic demo — no AI.** `GENERATION_PROVIDER=fake` replays
  a shipped, fully validated golden case offline with zero credentials, zero
  network, zero cost. The UI is honest about it ("uses the built-in
  deterministic generator in this demo build").
- **Opt-in local AI generation with validation.** `GENERATION_PROVIDER=ollama`
  runs a local LLM (Ollama) that *proposes* structured case/evidence/world
  data; deterministic validators, the deduction solver, the Asset Oracle and
  the procedural renderer *verify and construct* the playable investigation.
  The model never proves the case and never runs generated code.
- **Optional cloud mode.** `GENERATION_PROVIDER=live` (HTTPS-only, opt-in,
  replaceable provider boundary) is never part of the default path.

The AI does not "generate everything" — it proposes; deterministic systems
guarantee the result.

## What deterministic validation guarantees

- **Exactly one provable answer** — the case is published only when exactly
  one survivor remains for every accusation dimension (WHO / WHY / WEAPON /
  WHEN).
- **Truth isolation** — the hidden CaseTruth never reaches the client before
  reveal; every pre-reveal payload is an allowlisted DTO scanned recursively
  for hidden truth, solver internals and tokens.
- **Fail-closed** — leaks, unprovable worlds and malformed generations are
  rejected with sanitized errors; a bad world is never served.

## Best case to try

- *Deterministic demo:* click **Try Demo Case** (the shipped golden apartment
  case) — instant, no credentials.
- *Local-AI mode:* generate the **Hard** example prompt — the bronze
  ceremonial ice pick office case (`Victim: Dr. Anna Weiss / Murderer: Paul
  Becker / Motive: stolen research data / Weapon: bronze ceremonial ice pick /
  Time: 23:42 / Witness: Lisa König / Location: office`). Its weapon is an
  unusual object that is genuinely unseen by the catalog, so it exercises
  procedural object generation end to end.
- The five showcase prompts (apartment / office / hotel suite / warehouse /
  mansion) are documented in `backend/tests/fixtures/world_showcase.py` and
  the Easy/Medium/Hard selector is covered by
  `e2e/phase17dbugfix-examples.spec.ts`.

## Expected generation time

- Deterministic demo: **instant** (shipped golden case).
- Local AI (Ollama, e.g. hermes3:8b on a capable machine): roughly **1-2
  minutes** for a full Prompt-to-World generation including validation,
  repair and rendering — varies with machine, model and prompt. The default
  generation deadline is 300 s.

## Deployment constraints

- **Single container** (FastAPI + built SPA, one origin on port 8000).
- **SQLite** on a durable Docker volume (`pd-data`), migrations run
  automatically on startup.
- **No external AI dependency for the demo** — the deterministic path runs
  fully offline with zero credentials; local/cloud AI are strictly opt-in.
- Compose: `docker compose up --build`. Details in `docs/DEPLOYMENT.md`.

**Run it:** `docker compose up --build` (or the one-command Windows demo
launcher `.\scripts\start-demo.cmd`). Full setup, demo paths and testing
instructions are in `README.md`.