# Compose environment profiles (Phase 24 §8)

These files drive **Compose interpolation** (`docker compose --env-file ...`)
and document the supported deterministic/real-AI deployment modes. The
basenames deliberately avoid the `.env.*` family — the release gate reserves
that family for operator-local secret files and never scans these safe
placeholder documents (`tools.release_check` gates them like any tracked
text).

| Profile | File | Provider | Bridge | Purpose |
| --- | --- | --- | --- | --- |
| Local deterministic dev | `dev.env` | `fake` | `false` | `docker compose --env-file compose/profiles/dev.env up --build` |
| CI deterministic | `ci.env` | `fake` | `false` | every GitLab push/MR pipeline + docker-smoke (§8/§32) |
| Production-like local smoke | `prod-like.env` | `fake` | `false` | Caddy-edge local smoke (§39), `prod_preflight --allow-local` |
| Real-Ollama acceptance | `ollama.env` | `ollama` | `false` | operator-triggered; private values injected only (docs only) |
| Production deployment | `.env.production.example` (repo root) | operator | `false` | real public deployment |

Rules:

- **Never silently switch provider modes** (§8). `GENERATION_PROVIDER=ollama`
  without a reachable Ollama FAILS TRUTHFULLY and typed — there is no
  fake/cassette/mock fallback.
- These files contain NO private addresses, tokens, keys or credentials —
  only safe placeholders. Real-Ollama values (`OLLAMA_BASE_URL`,
  `OLLAMA_MODEL`) and production domains (`CADDY_DOMAIN`, `CADDY_EMAIL`) live
  in the operator environment / GitLab CI/CD variables (masked/protected).
- The canonical Phase 19J-RI2 timeout envelope (300/180) is declared in every
  profile; Docker and GitLab validate the RENDERED values through the backend
  `timeout_envelope_violations` validator (§9/§38).
- The CI deterministic profile mounts the repository fake world
  (`compose/fake-worlds/ci-activity-log-world.json`) read-only via
  `docker-compose.ci.yml` so the smoke can exercise the golden ACTIVITY_LOG
  record over the wire (§13). That fixture is generated deterministically from
  `backend/app/services/dev_mode_case.json` by
  `python -m tools.generate_ci_fake_world --check`.