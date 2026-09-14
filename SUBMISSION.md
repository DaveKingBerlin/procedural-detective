# Procedural Detective — Hackathon Submission

**Project:**
Procedural Detective

**Short pitch:**
AI-native browser-based 3D detective game that turns natural-language crime
prompts into logically consistent, interactive investigations.

**Core differentiator:**
AI can generate a world. Procedural Detective makes sure that world has a truth.

---

## Submission assets (placeholders)

| Asset | URL |
| --- | --- |
| Repository | `https://github.com/<owner>/procedural-detective` (placeholder) |
| Live demo | `https://<demo-host>/` (placeholder) |
| Demo video (< 3 min) | `https://<video-url>/` (placeholder; storyboard: `docs/demo-storyboard.md`) |
| Focus category | `AI Gaming / AI-powered game creation` (placeholder — adjust to the official category list) |

---

## What it is

Type (or paste) a detective scenario in plain language. Procedural Detective:

1. parses the prompt into a hidden immutable **CaseTruth** (murderer, motive,
   weapon, time, timeline),
2. generates a coherent world: suspects, evidence, emails, transactions,
   red herrings,
3. runs an objective, deterministic validation that the case has exactly one
   solution — provable from the evidence the player can actually find,
4. renders the result as an interactive browser-based **3D apartment
   investigation** (Babylon.js),
5. lets the player investigate, accuse (WHO / WHY / WEAPON / WHEN), and reveal
   the canonical truth.

The central guarantee: **the AI generates the world, but the truth is
validated — every generated mystery has a real answer.**

## Demo path

- Zero credentials, zero cost, deterministic: `GENERATION_PROVIDER=fake`
  (the default) replays a fully validated golden case for any prompt.
- Live mode is opt-in via `LLM_API_KEY` / `LLM_MODEL` / `LIVE_PROVIDER_URL`.

## Run it

```bash
# Local development
cd backend && pip install -e "./backend[dev]"
python -m alembic -c backend/alembic.ini upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
cd ../frontend && npm install && npm run dev

# Single-container production
docker compose up --build
```

See `README.md` and `docs/DEPLOYMENT.md` for full details.