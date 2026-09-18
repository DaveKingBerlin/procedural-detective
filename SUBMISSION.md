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

1. turns the prompt into a hidden immutable **CaseTruth** (murderer, motive,
   weapon, time, timeline) with your locked constraints preserved;
2. runs the **Asset Oracle** — resolves every requested object and environment
   against a bundled asset catalog and **five environment kits** (apartment,
   office, hotel suite, warehouse, mansion), falling back to **safe procedural
   asset generation** only when no sensible match exists;
3. composes the world (evidence, red herrings, supporting props, kit shell,
   lighting, camera framing) and runs a **deterministic validation** that the
   case has exactly one solution — provable from the evidence the player can
   actually find;
4. publishes a playable browser-based **3D investigation** (Babylon.js) where
   you click objects directly in the scene, collect evidence, accuse
   (WHO / WHY / WEAPON / WHEN), and reveal the canonical truth.

Different prompts visibly produce different worlds: each environment kit has
its own geometry, lighting identity and anchors, and each showcase prompt
resolves different evidence objects (letter opener + custom trophy in the
office, glass/medication bottles in the hotel, wrench + rope in the warehouse,
antique ceremonial letter opener + wristwatch in the mansion) — all rendered as
safe, deterministic primitives.

The central guarantee: **the AI generates the world, but the truth is
validated — every generated mystery has a real answer.**

## Demo path

Two obvious routes, both zero-token, zero-cost by default:

- **Try Demo Case** — a one-click deterministic demo: no API keys, no cost,
  guaranteed showcase case, clearly labelled as a deterministic demo.
- **Generate a New Mystery** — your own custom prompt through the same
  generation journey (`POST /cases` → progress → playthrough → 3D scene).

Default stack: `GENERATION_PROVIDER=fake` (deterministic offline generator) +
the SPA's `VITE_APP_PROVIDER` note keeps the UI honest about it. Live mode is
opt-in via `GENERATION_PROVIDER=live` and `LLM_API_KEY` / `LLM_MODEL` /
`LIVE_PROVIDER_URL`.

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