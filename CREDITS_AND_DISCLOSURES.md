# Procedural Detective — Credits, Licensing, Attribution & AI Disclosure White Paper

**Working title:** "Procedural Detective — Credits, Licensing, Attribution & AI Disclosure White Paper"
**Version:** 1.0 (draft for submission review)
**Repository:** <https://github.com/DaveKingBerlin/procedural-detective>
**Date of audit:** 2026-10-09
**Status:** Evidence-based technical assessment. This document is **not** a legal opinion and does not certify hackathon eligibility, license compliance, or production readiness. Every substantial claim below is traced to repository evidence or named external sources; where evidence is insufficient, the item is explicitly classified **UNVERIFIED**.

---

## Abstract

This white paper is the provenance, licensing, attribution and AI-disclosure record that accompanies the submission of **Procedural Detective** to the Victoria VR AI Builder Hackathon 2026. The audit examined the repository's manifests, lockfiles, license files, container configuration, source trees, Git history, development-phase reports, adversarial review records, and the RAD delivery protocol. We verified that the project is authored by a single human developer ("David"/"DaveKingBerlin", 132 commits, 2026-09-09 → 2026-10-09), is distributed under the MIT License, declares a small, permissive-licensed dependency set (FastAPI/Pydantic/SQLAlchemy/Alembic/uvicorn/httpx on the backend; React/Babylon.js/Vite/TypeScript/Vitest on the frontend; websockets/httpx in the bridge), bundles **no third-party media assets**, and integrates AI exclusively through opt-in, operator-controlled generation providers with a deterministic no-AI default. AI assistance was used extensively throughout development and is disclosed in Chapter 12, including model/provider roles, benchmark evidence, and the human review loop. The principal uncertainties are (a) unverified full transitive-license closure, (b) unverified official hackathon rules text, and (c) model-specific provider terms for the optional BYOK Frontier path. No paid marketplace assets were identified. The project's own web-hosted demo and video URLs remain placeholders in `SUBMISSION.md`.

---

## 1. Introduction and Project Background

Procedural Detective is an AI-native, browser-based 3D detective game: a player describes a crime in natural language and the application generates a coherent world of suspects, evidence and red herrings that is **provably solvable** before being presented for play. Its central design claim is that AI "proposes" structured case data while deterministic validators, a deduction solver, an Asset Oracle and a procedural renderer "verify and construct" the playable investigation (`SUBMISSION.md`; `README.md` "AI proposes, deterministic systems guarantee").

The project was developed through **34 numbered phase documents** (Phases 1–33 plus sub-phases 19A–J, 21A–C, 22–24 fix lines, 26A/B/C1–C5, 30 fix, 31/31A/31B, 32, 33) under a repository-local delivery protocol (`.rad/`, "Runtime-Agnostic Delivery Protocol", RAD `3.0.0-beta.1` per `.rad/manifest.json`). Git history shows an initial commit on 2026-09-09 and the most recent merge (Phase 33) on 2026-10-09 — a single-month development campaign of 132 commits.

Transparency objectives of this paper: (1) give hackathon reviewers, organizers and IP specialists a verifiable record of every external contribution; (2) disclose AI-assisted development and AI-generated content honestly; (3) separate verified facts from inference and from declared-but-unverified information; (4) surface material compliance risks rather than claim blanket compliance.

---

## 2. Research Scope and Research Methodology

The audit was performed read-only against the working repository at commit `d79b51b` (post Phase 33 merge, 2026-10-09), with repository-wide inspection of:

- Top-level documentation: `README.md`, `REQUIREMENTS.md`, `SUBMISSION.md`, `THIRD_PARTY.md`, `LICENSE`.
- Delivery protocol: `.rad/` (manifest, core protocol, workflows, policies, roles, security command policy).
- Package manifests and lockfiles: `backend/pyproject.toml`, `frontend/package.json`, `frontend/package-lock.json`, `bridge/pyproject.toml`, `requirements-dev.txt`.
- Containerization: root `Dockerfile`, `docker-compose{.yml,.ci.yml,.lan.yml,.prod.yml}`, `docker/` (entrypoint, Caddyfile), `compose/` profiles.
- Source trees: `backend/app/` (API, domain, generation, persistence, world, validation), `frontend/src/`, `bridge/`.
- Static/data assets: `assets/` (catalog + environment kits), `screenshots/evidence/`.
- Git history: 132 commits, all branches, author metadata, phase merge records.
- Phase reports: 28 root `Phase*.md` documents from Phases 19J through 34B.
- QA/governance records: `DEFECTS.md` (58 formal defects), `ADVERSARIAL_REVIEW.md` (33 "Phase33 gate" findings plus prior sessions), `docs/` (deployment, operations, monitoring, privacy, ADRs).
- AI configuration and adapters: `backend/app/generation/` (providers, prompts, registry, benchmark hooks), `.env.example`, `benchmarks/frontier/`.

**Method limitations.** (a) The complete transitive dependency license inventory below is declared-manifest evidence; the full license closure of every transitive wheel/npm package was not independently re-published here (declared in `THIRD_PARTY.md`, §1–2, which delegates transitive metadata "to upstream"). (b) The official Victoria VR AI Builder Hackathon 2026 rules PDF/website were not accessible from this environment; compliance mapping in Chapter 15 therefore uses the requirements as described in the phase brief and flags them as requiring confirmation against the official documentation. (c) LLM provider terms (OpenAI/OpenRouter/Groq/etc.) were not re-fetched from live URLs; they are cited by product name and marked as operator-verifiable.

The audit made **no paid API calls**, modified **no application source file**, and changed **no dependency or license metadata**.

---

## 3. Software Architecture and Technology Stack

Procedural Detective is a single-container web application (FastAPI + packaged SPA, one origin on port 8000) with an independent bridge client. The architecture is documented in `README.md`, `docs/DEPLOYMENT.md`, `backend/` and `frontend/`.

**External technology roles:**

| Layer | Technology | Role |
|---|---|---|
| Backend framework | FastAPI + Pydantic + pydantic-settings | REST API, typed validation, env configuration |
| Persistence | SQLAlchemy + Alembic + SQLite | Case/playthrough store; migrations on container startup |
| ASGI server | uvicorn | HTTP serving |
| HTTP client | httpx | Provider adapters (live/ollama/frontier) |
| Frontend UI | React + React DOM + react-router | UI framework, rendering, client navigation |
| 3D scene | @babylonjs/core | Babylon.js 3D engine for the investigation scene |
| Frontend build | vite + typescript + @vitejs/plugin-react | SPA compilation and dev server |
| Frontend tests | vitest + jsdom + @playwright/test + @types/* | Unit tests; E2E browser tests |
| Bridge | websockets + httpx (`pd-ollama-bridge`) | Optional "bring-your-own Ollama" connector |
| Container | python:3.12-slim (runtime), node:24-alpine (build stage), caddy:2-alpine (optional prod TLS edge) | Single-container deployment; optional automatic HTTPS |
| AI (optional) | fake / ollama / live / frontier providers | Deterministic demo default; opt-in local/cloud/BYOK generation |

Design consequences: the provider layer is replaceable (`backend/app/generation/` exposes `fake_provider`, `ollama_provider`, `live_provider`, `remote_client_provider`, `frontier_provider`), and the deterministic engine remains authoritative over all generated values (`REQUIREMENTS.md`, phase 16_2 design notes in `prompts.py`).

---

## 4. Software Architecture and Technology Stack (expanded)

*(Editorial note: the requested outline overlaps Chapters 3 and 4; this chapter consolidates container/deployment detail so Chapter 3 stays readable.)*

The production image is a two-stage build (`Dockerfile`): Stage 1 (`node:24-alpine`) compiles the SPA (`npm ci` + `npm run build`); Stage 2 (`python:3.12-slim`) installs `./backend[runtime]`, copies the built SPA to `/app/static`, and runs as non-root UID 1001 with a durable SQLite volume at `/data`, `GENERATION_PROVIDER=fake` by default, and a `/api/v1/health` HEALTHCHECK. `docker-compose.prod.yml` optionally adds the Caddy 2 reverse-proxy edge (automatic HTTPS, HTTP→HTTPS redirect, HSTS). Compose profiles under `compose/profiles/` (`ci`, `dev`, `ollama`, `prod-like`) and the `compose/fake-worlds/` fixtures support reproducible environments. A Windows one-command demo launcher (`scripts/start-demo.cmd`) is provided for judges.

---

## 5. Development History and Engineering Evolution

The development history is reconstructed from Git commits and phase documents. **All dates below are Git-author dates; commit authorship is a single human identity (`DaveKingBerlin <github@daves-web.de>` for 131 commits and `David <153939358+DaveKingBerlin@users.noreply.github.com>` for 1 commit).**

| Period (2026) | Milestones (commit/phase evidence) |
|---|---|
| 09-09 → 09-11 | Repository bootstrap; frozen `REQUIREMENTS.md` (`e8dd957`); Phase 2 runnable skeleton (`d36baa3`) |
| 09-11 → 09-15 | Phases 3–7: deterministic deduction domain, generation lifecycle, private versioned cases+playthrough persistence, browser investigation, accusation/reveal; **Milestone 1 hackathon demo** (`faddbeb`) |
| 09-15+ | Phases 9–14: asset oracle, showcase world library, procedural asset generation, prompt-to-world pipeline, five environment kits |
| Phase 16–16_2 | Ollama stage driver, prompt templates, unit/meter/bounds contract |
| Phases 17A–17E | Local-AI showcase (Hermes 3 8B), timeout handling, structured logging, reliability and observability; sub-phases 18A–18D (submission hygiene, forensic focus, detective notebook, art direction) |
| Phases 19–22 / 19A–19J | Activity logs, universal object inspection, real-Ollama reliability, BYO-Ollama bridge (Phase 22), provider budgets |
| Phases 23–24 | Witness interviews and player-safe statements; Docker + GitLab CI/CD |
| Phases 25–28 | Browser-selectable provider; bridge fixes B/C; provider-call budget alignment; demo-case pool; demo UX cleanup |
| Phase 29–30 | Monitoring & usage analytics; Frontier BYOK via trusted provider registry; outbound wall-clock timeout; release-hygiene scrub |
| Phases 31/31A/31B | Secret-safe Frontier benchmark harness; model compatibility diagnostics + fixes; structured-contract alignment |
| Phase 32 | Save & Load Case: replayable `.pdcase` savegames |
| Phase 33 | Generation reliability: asset-ID registry + anchor + locked-witness content fixes; per-attempt failure-category benchmark extension |

Engineering evolution highlights: the project moved from pure deterministic generation (Phases 3–4) through a hybrid "AI proposes, determinism verifies" pipeline (Phases 9–16), then hardened provider integration (Phases 22–31), then addressed content-reliability failure classes (Phase 33). Each phase followed the RAD workflow (contract → delegated implementation → QA validation → adversarial pass → defect lifecycle → gate checkpoint).

---

## 6. Major Technical Challenges and Root-Cause Analysis

The structured engineering-incident register in **Appendix E** lists the significant verified incidents. Representative root causes with repository evidence:

**A. AI model integration and provider compatibility (Phase 31 series).** Live Gemini probe `GA-ZP_gqsayEUfI` passed all four structured-output stages (`openai_json_schema`) but ended in `TERMINAL_FAILURE` on real content/world/solver constraints (asset-ID registry, anchor allowlist, locked witness); Llama probe `GA-HeeF_dWxn2mS` failed `FRONTIER_TIMEOUT` on case_truth at the 180 s timeout (`Phase33.md` §1; `benchmark-results/phase31b-*`). Root cause split: provider/transport vs. content vs. schema — the reports explicitly forbid classifying the Llama timeout as a schema or content failure.

**B. Schema validation and contract consistency (Phase 31B).** Timestamp contract mismatches (e.g. `20:15`, `20:18` rejected by `_ISO_RE`/`parse_iso8601`), reliability-vocabulary drift (`HIGH`, `DEFINITIVE` rejected), structured proposition fields (`FORENSIC_WEAPON_MATCH.match` boolean; `ALIBI_TIME_CLAIM.claimedDeparture` timestamp), and the `"int" in "interaction"` type-inference bug — fixed by shared canonical schema builders with Cohere-only stripping at the transport boundary (`Phase31B-SCA.md`; `test_phase31b_*`). Key lesson documented: schema-valid ≠ semantically valid.

**C. Procedural world generation and solver challenges (Phase 33).** Deterministic reproduction of three content failure classes — unknown asset ids against the AssetRegistry, disallowed anchors against `ANCHOR_ALLOWLIST`, locked-witness contradictions (`TERMINAL_FAILURE`) — and minimal fixes that supply registry-backed identifiers, kit-scoped anchor vocabularies and the locked-witness contract to model-facing contexts without weakening a single validator (commits `664fcba`, `4f816ba`; tests `test_phase33_*`). Residual risks are in Appendix G.

**D. Testing/regression.** The full backend suite grew to 2869 passed / 1 skipped; bridge 300; frontend 1680 + typecheck + build; root suite 250 passed + 2 known host-only `.env` `OLLAMA_BASE_URL` compose failures; adversarial gate fields 33 findings for Phase 33 alone, all triaged, 8 accepted as DEF-058..065 and closed after independent QA retest. One pre-existing flaky Phase 32 token-tail test was recorded as non-Phase-33 (Appendix F).

**E. Infrastructure/operations (Phases 24, 29, 30).** Docker/CI reproducibility (Phase 24), single-writer SQLite backup rules (`docs/OPERATIONS.md`), Caddy JSON access logging + product/usage metrics (`docs/MONITORING.md`), and the local-vs-prod `CADDY_DOMAIN` preflight constraint (`Phase33.md` §1). Operational health checks are explicitly separated from production-scale load tests.

**F. AI-assisted development challenges (all phases).** Coordinating DeepSeek (this agent), GPT-family review passes (Phase 21 "GPT-Terra findings F-01..F-07"), Gemini/Llama benchmarking, plus the RAD delegated-role structure. Documented incidents of AI-introduced regressions were caught by QA/adversarial gates (e.g., Phase 19J activity-log structured-output regression `2f1e0eb`; Phase 31B Cohere transport regressions caught by drift guards).

---

## 7. AI Model Integration and Reliability

Procedural Detective's generation architecture is a **proposal/verification split**:

1. A provider **proposes** structured JSON for staged outputs (case truth, public world, evidence, world graph), under closed enum/allowlist guidance rendered from the authoritative registries.
2. Deterministic validation (structural parser, safety validators, solver, world composition, publication gate) **accepts, repairs or rejects**. Repair is bounded (`DEFAULT_MAX_REPAIR_PASSES = 2`; regeneration budget 1; budget/deadline/timeout invariants unchanged since Phase 31B).
3. A case is **published only when the solver proves a unique answer** for every accusation dimension.

Providers (all opt-in; default `GENERATION_PROVIDER=fake` uses no AI):

| Provider | Requirement | Modelled by | Evidence |
|---|---|---|---|
| `fake` (default) | none | Deterministic golden case | `backend/app/generation/fake_provider.py`, `dev_mode_case.json` |
| `ollama` | local Ollama server | Llama 3.2 3B (default `OLLAMA_MODEL`), Hermes 3 8B (Phase 17 showcase) | `ollama_provider.py`, `ollama_driver.py`, `.env.example` |
| `live` | `LLM_API_KEY`/`LLM_MODEL`/HTTPS endpoint | Operator-chosen hosted chat model | `live_provider.py` |
| `frontier` | player BYOK key+model | OpenRouter/Groq/etc. via trusted server-owned registry | `frontier_provider.py`, `frontier_registry.py`, `frontier_benchmark.py` |
| `remote_client` | bridge to remote Ollama | Operator-run `pd-ollama-bridge` | `remote_client_provider.py`, `bridge/` |

**Benchmark evidence (Phase 31/33).** A secret-safe benchmark harness (`tools/frontier_benchmark.py`) records per-attempt provider/model/attempt-id, failure category (closed taxonomy incl. `registry/anchor/witness`), publishable rate, and cost per validated published case (zero-success groups reported as undefined, never zero). Per Phase 33, **no paid run was authorized**, so results are labeled offline-only / live-inconclusive (`LIVE_BENCHMARK_BLOCKED_PENDING_AUTH`). Historical live probes (Gemini, Llama) were performed under the Phase 31A/31B authorizations recorded in `benchmark-results/`.

---

## 8. Testing, Validation and Quality Assurance

**Inventories (repo evidence):** 157 backend test modules; 84 frontend test files (vitest); 36 Playwright E2E specs; 11 bridge test modules; 91 screenshot-evidence files under `screenshots/evidence/`. Governance registers: 58 formal defects in `DEFECTS.md`; 33 Phase-33-gate adversarial findings in `ADVERSARIAL_REVIEW.md` plus earlier sessions.

**Definitive recent full-suite results (QA + orchestrator verified, 2026-10-09):**

| Suite | Result |
|---|---|
| Backend full (`pytest backend/tests`) | 2869 passed / 1 skipped |
| Bridge full | 300 passed |
| Frontend (`npm test`) | 1680 passed (84 files) |
| Frontend typecheck/build | pass (typecheck + vite build + bundle scan) |
| Root tooling (`pytest tests/`) | 250 passed; 2 failed — **known host-only** `.env` `OLLAMA_BASE_URL` compose pollution |
| Adapter drift (`generate_adapters.py --check --all`) | OK (28 files, fingerprint `bdceb825721cd46f`) |
| release_check | 20/20 with allowed placeholders |

**Exception register:** (1) 2 root-suite failures are host-only `.env` pollution (git-ignored), reproduced identically without Phase 33 changes; (2) 3 `release_check` README/SUBMISSION TBD placeholders (hosted demo URL, video URL); (3) strict `prod_preflight` local default `CADDY_DOMAIN` (previous `--allow-local` 7/7); (4) one pre-existing flaky Phase 32 token-tail test (`test_export_available_after_reveal_and_wire_headers`, ~3.34% random tail collision), non-Phase-33, passes on re-runs. None were hidden or redefined as passing.

---

## 9. Infrastructure, Deployment and Operational Challenges

- **Single-container deployment** with automatic migrations on startup, health/readiness endpoints, and a durable SQLite volume with documented single-writer backup/restore (`docs/DEPLOYMENT.md`, `docs/OPERATIONS.md`).
- **Optional TLS edge (Caddy 2)** with automatic HTTPS, HSTS, request limits (`docker-compose.prod.yml`, `docker/Caddyfile`).
- **Monitoring (Phase 29)** adds observability only: Caddy JSON access logs + `tools/monitoring_report.py` product/usage metrics; no trust-boundary change (`docs/MONITORING.md`).
- **Known operational caveats:** production-scale load/reliability testing has **not** been performed; "health check" ≠ "load test" (explicitly distinguished in the phase briefs and `docs/`).

---

## 10. Open-Source Libraries and License Inventory

The repository itself is **MIT-licensed** (`LICENSE`, "Copyright (c) 2026 David"; `backend/pyproject.toml` declares `license = { text = "MIT" }`).

**Direct dependencies (declared; exact resolved versions in parentheses are lockfile/metadata evidence as recorded in `THIRD_PARTY.md`):**

| Component | Version | Role | License (SPDX) |
|---|---|---|---|
| FastAPI | ≥0.115,<1.0 (`0.141.1`) | API framework | MIT |
| Pydantic | ≥2.7,<3.0 (`2.13.5`) | Validation | MIT |
| pydantic-settings | ≥2.3,<3.0 (`2.15.0`) | Config | MIT |
| SQLAlchemy | ≥2.0.30,<3.0 (`2.0.52`) | ORM | MIT |
| Alembic | ≥1.13,<2.0 (`1.19.2`) | Migrations | MIT |
| uvicorn | ≥0.30,<1.0 (`0.52.4`) | ASGI server | BSD-3-Clause |
| httpx | ≥0.27,<1.0 (`0.28.1`) | HTTP client | BSD-3-Clause |
| pytest (dev) | ≥8,<10 (`9.1.1`) | Tests | MIT |
| @babylonjs/core | `8.56.2` (locked) | 3D engine | Apache-2.0 |
| react / react-dom | `19.3.0` (locked) | UI | MIT |
| react-router | `7.18.3` (locked) | Navigation | MIT |
| vite (dev) | `7.3.6` (locked) | Build | MIT |
| typescript (dev) | `5.9.3` (locked) | Typing/SPA | Apache-2.0 |
| vitest / jsdom (dev) | `3.2.7` / `26.1.0` (locked) | Tests | MIT |
| @vitejs/plugin-react (dev) | `4.7.0` (locked) | Vite React | MIT |
| @types/node, @types/react, @types/react-dom (dev) | locked | Typing stubs | MIT |
| bridge: websockets, httpx | ≥13 / ≥0.24 | Bridge | BSD-3-Clause / MIT-family (see upstream) |

**Container base images:** `python:3.12-slim` (Debian; predominantly GPL-associated stack per upstream), `node:24-alpine` (build stage only), `caddy:2-alpine` (Apache-2.0) — optional prod edge, not in the app image.

**Attribution obligations:** each direct dependency has an SPDX-identifiable license; MIT/BSD/Apache-2.0 projects require license/notice preservation in redistribution. The upstream license texts are preserved by the package ecosystem (wheel metadata / npm package `LICENSE` fields); the project does not embed modified third-party source beyond standard dependency installation. The complete per-package obligation matrix, including the transitive scope limitation, is in **Appendix A**.

---

## 11. Third-Party Assets and Marketplace Content

**Asset audit result:** *No third-party fonts, icons, textures, audio files, 3D models or image assets are bundled in the repository.* All shipped assets are project-authored and fall under the repository MIT license (`THIRD_PARTY.md` §4):

- `assets/catalog/catalog.json` — declarative, byte-stable asset catalog (project-owned).
- `assets/environments/*.json` — five environment kits (apartment, office, hotel suite, warehouse, mansion): declarative zones/anchors/lighting (project-owned).
- `screenshots/evidence/*.png` — project-captured QA/test screenshots (project-owned).
- Runtime `proc.*` assets — deterministic compositions derived from the above (project-owned).

**Paid marketplace content:** **No paid marketplace assets were identified within the audited evidence.** No asset was found whose provenance indicates purchase from a marketplace, store or commercial content provider. This conclusion is supported by (a) the absence of any purchase/license records, artwork source references or marketplace metadata in the repository, (b) the declarative, project-authored nature of all shipped assets, and (c) `THIRD_PARTY.md` §4. **Scope caveat:** the audit covers repository contents at commit `d79b51b`; any asset added outside the audited commit set would require re-audit. **Appendix B** contains the asset inventory.

---

## 12. AI-Assisted Development and Generative AI Disclosure

### 12.1 Declaration of AI-Assisted Development and Content Generation

> **Procedural Detective — Declaration on AI-Assisted Development and Content Generation**
>
> This submission was developed with substantial assistance from AI coding assistants and language models. AI systems were used to assist with programming, architecture design, debugging, test authoring, documentation, and engineering analysis throughout the development lifecycle (Phases 1–33, September–October 2026). This repository includes a local delivery protocol (`.rad/`) that structured roles (implementation, QA, adversarial review), gates, and evidence requirements for AI-assisted work, and all changes were subject to human review before commit.
>
> **Runtime AI in the product:** The shipped default (`GENERATION_PROVIDER=fake`) is a deterministic, no-AI generator; optional AI providers (`ollama`, `live`, `frontier`) may be enabled by an operator/player and are used to *propose* structured case content that the application validates, repairs, and must prove solvable before publication. Generated content therefore never reaches players without passing deterministic validation. Model weights are never distributed with this repository.
>
> This declaration is made in good faith and does not assert that AI-assisted code is free of licensing obligations or that AI-generated content carries independent copyright. No financial compensation was received for AI assistance. Final review and acceptance responsibility rests with the human developer.

### 12.2 Verified AI systems and their roles

| System | Role in this project | Category (Phase34A §5 classification) | Evidence |
|---|---|---|---|
| DeepSeek (OpenCode runtime; this agent) | Primary implementation/QA coordination under `.rad`; assisted programming, debugging, test authoring, documentation | (1) AI-assisted software development + (3) benchmarking | `.rad/manifest.json`, phase reports, commit history |
| OpenCode | Coding assistant runtime used throughout | (1) | Generated-adapters (`tools/generate_adapters.py`) |
| GPT-family (labeled "GPT-Terra" review) | External pre-hosting security/hygiene audit pass (Phase 21 findings F-01..F-07) | (1) review | `Phase24.md`/Phase 21 commit records; PRIVACY.md |
| Google Gemini (`google/gemini-3.8-flash`, attempt `GA-ZP_gqsayEUfI`) | **Benchmark/evaluation only** — never in the shipped default; live structured-output probe | (3) | `benchmark-results/phase31b-gemini-probe` |
| Meta Llama (`meta-llama/llama-3.3-70b-instruct`, attempt `GA-HeeF_dWxn2mS`) | Benchmark/evaluation only; timed out at 180 s | (3) | `benchmark-results/phase31b-llama-control` |
| Cohere (`cohere/command-a-plus`) | Declared benchmark contestant (enabled in `contestants.toml`); adapter invariants tested offline | (3) | `benchmarks/frontier/contestants.toml`, `test_phase31a_cohere.py` |
| Ollama + local models (Llama 3.2 3B; Nous Research Hermes 3 8B) | **Optional runtime component** for local AI generation (operator opt-in) | (2) integrated app component + (4) user-visible content proposer | `.env.example`, `ollama_driver.py`, `THIRD_PARTY.md` §5 |
| Frontier BYOK (OpenRouter/Groq/Together/Mistral/Fireworks/DeepInfra/xAI) | Optional runtime component; player supplies key+model; server-owned registry | (2) + (3) | `frontier_registry.py`, `docs/OPERATIONS.md` |

**Clarifications required by Phase34A §5:**
- **Not every listed system contributed.** OpenAI GPT models were used for a review audit (Phase 21), not as a product runtime; Gemini and Llama were used exclusively for benchmarking. Neither is integrated into the shipping application.
- **AI-generated game content** is limited to the optional generation providers. In the default demo, no AI is used at all. When enabled, the model proposes case data; the deterministic engine verifies/repairs/publishes.
- **Human review:** all AI-assisted contributions were reviewed and accepted by the human developer (single-author history; RAD gates; QA/adversarial gates; formal defect lifecycle).

### 12.3 Model/asset licensing note

Ollama is MIT-licensed open source (not bundled). Model weights (`llama3.2:3b` by Meta, `hermes3:8b` by Nous Research built on the Llama architecture by Meta) are **never redistributed** by this repository; the operator downloads them under their own upstream terms (Llama community license; Hermes terms) — `THIRD_PARTY.md` §5. Frontier players supply their own credentials under their own provider/model terms.

---

## 13. Originality, Authorship and Intellectual Property

**Verified original work (project-specific):** the deterministic deduction domain/solver (`backend/app/domain/`), the generation lifecycle, the Asset Oracle and environment kits, the publication gate with truth isolation, the savegame/replay format, the benchmark harness, the `.rad` protocol, and the repository documentation — Developed by a single human developer with substantial AI assistance, under human direction, a documented review process and MIT-licensed.

**Standard patterns vs. third-party code:** the application uses standard language/framework idioms (FastAPI route patterns, SQLAlchemy sessions, React components, Babylon scene scaffolding). No evidence was found of copied third-party application source outside declared dependencies; no vendored third-party source exists in the tree.

**AI-assisted code:** features and fixes were produced with AI assistance under a structured review process; they are original in the copyright sense of human-directed authorship but disclose AI assistance per Chapter 12. **No claim is made that AI-generated code is automatically original or legally risk-free.**

**IP risk findings:** see **Appendix G** and Chapter 16. Notably: no missing-attribution issue was identified for direct dependencies; the transitive closure remains only **conditionally verified**; unverified AI output provenance exists generically for generated cases (the models are external, and generation truth is case-local); no copied third-party material was identified.

**Plagiarism statement:** no evidence was found that the project plagiarizes a specific existing work. Use of open-source libraries under their permissive licenses does not constitute plagiarism when conditions are satisfied — conditions we assessed as satisfied for direct dependencies at the declared versions.

---

## 14. Lessons Learned and Scientific Discussion

Distilled from the engineering record (Phase34B §5 questions):

1. **Integrating probabilistic models into deterministic game architectures** is safest as a propose/verify split with a publication gate; the model never proves a case and never executes generated code (`SUBMISSION.md`, `README.md`).
2. **Schema validity ≠ semantic correctness**: Phase 31B's structural fixes produced schema-valid drafts that still failed real content/world/solver constraints (Gemini `GA-ZP_gqsayEUfI`); Phase 33 then addressed the content layer. A JSON-schema acceptance is not a successful generation.
3. **Provider-specific transport adapters + canonical contracts** reduce integration churn: non-Cohere canonical schema bytes stayed unchanged while Cohere strips only unsupported keywords at the boundary (Phase 31B), and adapter drift is guarded by tests.
4. **Deterministic validators + bounded repair** convert black-box failures into auditable `RECOVERABLE_REPAIR`/`TERMINAL_FAILURE` classes; repair budgets prevent unbounded cost. Repair can improve output without producing a publishable case ("improved but not publishable" is a documented outcome class).
5. **AI-assisted regression control** works best with red-regression tests, drift guards, independent QA and an adversarial pass — multiple AI-introduced regressions (e.g. Phase 19J activity-log, Phase 31B Cohere drift) were caught only by those gates.
6. **Runtime vs pre-generated catalog tradeoff** remains open: the evidence supports a hybrid, but the decision is deliberately deferred (Phase 33 §2 — architecture choice not implemented).
7. **Cost/timeouts/availability shape design**: the 180 s Llama timeout and missing cost telemetry are honest limits, not schema facts; cost-per-validated-case is reported as undefined where unverifiable.
8. **Public deployment limitations remain**: no load test; placeholders for hosted demo/video; host-specific env pollution in the local test suite.
9. **Transferable lesson**: for AI-generated interactive experiences, an objective proof-of-solvability gate is a practical, auditable way to make "generated" content trustworthy.

---

## 15. Hackathon Requirements and Compliance Assessment

**Rules status:** the official Victoria VR AI Builder Hackathon 2026 rules were **not verifiable from this environment**. The phase brief requires the submission to identify open-source libraries, third-party assets, paid marketplace content, licenses, author/source credits, and AI-generated code/assets where disclosure is required. Those items are all provided in this paper (Chapters 10–12, Appendices A–C). **Final compliance must be confirmed against the current official competition documentation.** No competition approval is claimed.

**Risk profile:** no copied/unlicensed content was identified; the main risks are (a) completeness of the transitive license inventory, (b) the presence of `TBD` placeholders in `SUBMISSION.md` (hosted demo URL and video URL) that a submission reviewer may require, and (c) the operator-nature of AI generation (the default demo uses no AI; the AI story depends on optional providers).

---

## 16. Remaining Risks and Future Development

| Risk area | Status | Required action |
|---|---|---|
| Transitive license closure | CONDITIONAL | Generate and publish a full `pip-licenses`/`license-checker`/OSV inventory for the pinned lockfiles before public distribution |
| Official hackathon rules confirmation | UNVERIFIED | Confirm against official rules text/site before submission |
| Hosted demo + video URLs | ACTION REQUIRED | Populate `SUBMISSION.md` placeholders once hosting/recording exist |
| Load/scale testing | UNVERIFIED | Schedule a load test for the intended deployment profile |
| Live provider benchmark (cost per case) | ACTION REQUIRED (blocked) | Requires explicit paid-run authorization (Phase 33 RAD-3/RAD-4) |
| Provider/model terms for BYOK options | CONDITIONAL | Operators/players must review their chosen provider/model terms (incl. Llama/Hermes for local mode) |
| Pre-existing flaky Phase 32 test | PARTIALLY RESOLVED | Fix the token-tail nondeterminism in a future phase |

---

## 17. Conclusion

Procedural Detective is a single-developer, MIT-licensed project built over one month with heavy AI assistance under a structured delivery protocol. The repository bundles no third-party media, declares a small permissive-licensed direct dependency set, and integrates AI only through opt-in providers behind deterministic validation. The AI disclosure in Chapter 12 and the engineering analysis in Chapters 5–8 are supported by primary repository evidence (commits, reports, test suites, benchmark results, governance records). The document does not certify legal compliance or hackability eligibility; the principal outstanding items are transitive-license closure, official-rule confirmation, `TBD` submission placeholders, and the deferred paid live-benchmark run.

---

## 18. Formal Credits and Disclosures

- **Developer / copyright holder:** David ("DaveKingBerlin") — sole author of all 132 commits (2026-09-09 → 2026-10-09).
- **Repository:** <https://github.com/DaveKingBerlin/procedural-detective> · **License:** MIT (see `LICENSE`).
- **AI assistance (development):** DeepSeek via OpenCode (primary implementation/QA assistance); GPT-family review audit (Phase 21). AI-assisted code was reviewed and accepted by the human developer under the `.rad` gating workflow.
- **AI models used for benchmarking/evaluation:** Google Gemini `google/gemini-3.8-flash`; Meta Llama `meta-llama/llama-3.3-70b-instruct`; Cohere `command-a-plus` (declared contestant). These never appear in the default product path.
- **Optional runtime AI:** Ollama + Llama 3.2 3B / Hermes 3 8B (local, opt-in); Frontier BYOK (OpenRouter/Groq/Together/Mistral/Fireworks/DeepInfra/xAI) with player-supplied keys; `live` provider (operator-configured). Model weights are operator-downloaded under their own terms and never redistributed.
- **Third-party open source (summary):** FastAPI, Pydantic, SQLAlchemy, Alembic, uvicorn, httpx, pytest, React, React DOM, react-router, Babylon.js (Apache-2.0), Vite, TypeScript (Apache-2.0), Vitest, jsdom, Playwright, websockets (see Chapter 10 and Appendix A).
- **Third-party assets:** none bundled; all assets are project-authored.
- **Paid marketplace content:** none identified.
- **Governance records:** `DEFECTS.md`, `ADVERSARIAL_REVIEW.md`, `.rad/` (protocol, policies, workflows), `docs/` (deployment, operations, monitoring, privacy, ADR-001..003).

---

## 19. References

**Repository primary sources**
1. `LICENSE` (MIT, "Copyright (c) 2026 David").
2. `backend/pyproject.toml`; `frontend/package.json`; `frontend/package-lock.json`; `bridge/pyproject.toml`; `requirements-dev.txt`.
3. `THIRD_PARTY.md` — dependency and asset declarations.
4. `SUBMISSION.md`; `README.md`; `REQUIREMENTS.md`.
5. `.rad/manifest.json`; `.rad/core/protocol.md`; `.rad/workflows/*.md`; `.rad/policies/*.md`.
6. `Dockerfile`; `docker-compose{.yml,.ci.yml,.lan.yml,.prod.yml}`; `docker/`; `compose/profiles/`.
7. Git history (132 commits; `DaveKingBerlin <github@daves-web.de>`); phase merge records (`Phase31B-SCA.md`, `Phase32-SALC-R.md`, `Phase33.md`).
8. `DEFECTS.md` (58 defects); `ADVERSARIAL_REVIEW.md` (Phase 33 gate, 33 findings, DEF-058..065).
9. `docs/DEPLOYMENT.md`; `docs/OPERATIONS.md`; `docs/MONITORING.md`; `docs/PRIVACY.md`; `docs/CI.md`; `docs/demo-storyboard.md`; `docs/adr/ADR-001..003`.
10. `tools/frontier_benchmark.py`; `benchmarks/frontier/{suites,contestants}.toml`; `benchmark-results/phase31*,phase31a*,phase31b*`.
11. `backend/app/generation/{prompts,pipeline,safety,constraints,frontier_registry,fake_provider,ollama_provider,live_provider,remote_client_provider}.py`; `backend/app/domain/`; `backend/app/world/`.
12. `screenshots/evidence/` (91 captured evidences).

**External sources (to be verified by operator)**
13. MIT License text — opensource.org/licenses/MIT (as reproduced in `LICENSE`).
14. FastAPI, Pydantic, SQLAlchemy, Alembic, uvicorn, httpx, pytest official repositories/license files.
15. Babylon.js license (Apache-2.0); React/Vite/TypeScript/Vitest/Playwright official licenses.
16. Ollama — ollama.com (MIT; upstream license).
17. Meta Llama 3.2 / 3.3 model cards + Llama community license — llama.com / Hugging Face.
18. Nous Research Hermes 3 model card — huggingface.co (NousResearch/Hermes-3).
19. OpenRouter model/pricing pages; Groq/Together/Mistral/Fireworks/DeepInfra/xAI developer terms.
20. Victoria VR AI Builder Hackathon 2026 official rules and submission requirements (**UNVERIFIED** — must be confirmed from official documentation).

---

## Appendices

### Appendix A — Open-Source Dependency Inventory

See Chapter 10 tables. Scope: **direct dependencies verified** from manifests/lockfiles; **transitive closure declared but not exhaustively re-published** (deferred to a license-checker run — see Appendix G, action O-1). SPDX identifiers are given where verified from metadata. Notice-preservation obligations apply to MIT/BSD/Apache-2.0 projects when redistributed; Debian base-image components are governed by Debian's license distribution rules (predominantly GPL; preserved by the base image itself).

### Appendix B — Third-Party Asset and Marketplace Inventory

| Asset | Source | Path | License | Redistribution | Attribution | Evidence |
|---|---|---|---|---|---|---|
| Asset catalog (v1 contract) | Project-authored | `assets/catalog/catalog.json` | MIT (repo) | Permitted | Not required from third parties | Repo tree; `THIRD_PARTY.md` §4 |
| Environment kits (5) | Project-authored | `assets/environments/*.json` | MIT (repo) | Permitted | — | Repo tree |
| Screenshot evidence (91) | Project-captured | `screenshots/evidence/*.png` | MIT (repo) | Permitted | — | Repo tree |
| Runtime `proc.*` assets | Deterministic derivation | generated at runtime | MIT (repo) | Permitted | — | `world/composer.py` |

Paid marketplace content: none identified (consistent with `THIRD_PARTY.md` §4).

### Appendix C — AI Model and Tool Usage Inventory

| Name | Category | Role | Evidence |
|---|---|---|---|
| DeepSeek (OpenCode) | Dev assistant | Primary implementation/QA | `.rad/`, git history |
| OpenCode | Dev tool | Coding runtime | `.claude/`, `.opencode/` adapters, `tools/generate_adapters.py` |
| GPT-family (Phase 21 audit) | Dev tool | Review audit | Phase 21 records, `PRIVACY.md` references |
| Gemini 3.8-flash | Benchmark | Live probe (structural validation) | `benchmark-results/phase31b-gemini-probe` |
| Llama 3.3-70B | Benchmark | Live probe (timeout track) | `benchmark-results/phase31b-llama-control` |
| Cohere Command A+ | Benchmark contestant | Adapter offline verification | `contestants.toml`, `test_phase31a_cohere.py` |
| Llama 3.2 3B / Hermes 3 8B | Runtime (optional) | Local proposal provider | `.env.example`, `ollama_driver.py` |
| Frontier providers | Runtime (optional, BYOK) | Player-supplied proposal provider | `frontier_registry.py` |
| Gemini/Llama/others | — | **Not bundled; not in default path** | — |

### Appendix D — Development Phase Timeline

Chronological phase milestones with commit evidence are in Chapter 5. Phases 1–7 (Milestone 1 demo) → 9–16 (asset oracle/prompt-to-world/local-AI) → 17–18 (reliability, UX polish) → 19–22 (logs, bridge, budgets) → 23–28 (witness, Docker/CI, providers, demo pool) → 29–33 (monitoring, Frontier BYOK, benchmark series, savegame, content reliability). Exact dates: 2026-09-09 (init) → 2026-10-09 (Phase 33 merge).

### Appendix E — Engineering Incident and Root-Cause Register

| Phase | Technical problem | Root cause | Resolution | Verification | Status |
|---|---|---|---|---|---|
| 31B | Schema-valid but parser-rejected timestamps (`20:15`) | `_ISO_RE`/schema mismatch | Canonical timestamp schema pattern | `test_phase31b_timestamp_schema` | RESOLVED |
| 31B | Reliability vocabulary drift | Validator rejected `HIGH`/`DEFINITIVE` | Closed `RELIABILITY_VOCABULARY` | `test_phase31b_reliability_enum` | RESOLVED |
| 31B | `"int" in "interaction"` inference | Type-inference bug | String, required, ≤4000 | `test_phase31b_world_interaction` | RESOLVED |
| 31A/31B | Cohere/DeepSeek transport incompatibilities | Schema keywords / envelope | Boundary-only stripping/adapters | `test_phase31b_cohere_regression` | RESOLVED |
| 31 | Llama 180 s timeout | Provider latency | Tracked as transport, not schema | benchmark records | EXTERNAL BLOCKER |
| 33 | Unknown asset ids | Missing trusted context | Registry+catalog scope in contexts | `test_phase33_content_failures` | RESOLVED |
| 33 | Disallowed/cross-kit anchors | Missing/over-broad context | Kit-scoped anchor vocabulary | `test_phase33_defects` | RESOLVED |
| 33 | Locked-witness mismatch | Ambiguous guidance | Witness contract wording (lock unchanged) | `test_phase33_content_failures` | RESOLVED |
| 33 | Benchmark success/failure miscounts | Reporting logic | Publishable/cost/failure-category fixes (DEF-061/062) | `test_phase33_benchmark_fields` | RESOLVED |
| 24/29/30 | Local `.env` compose pollution; `CADDY_DOMAIN` default; monitoring | Host env | Documented host-only exceptions | root suite record | UNVERIFIED (host-only, non-code) |
| 32 | Flaky token-tail savegame test | Nondeterministic token tail | None yet | passes on re-runs | OPEN (minor) |

### Appendix F — Test and Benchmark Evidence

Full-suite results per Chapter 8. Benchmark: `tools/frontier_benchmark.py` prints a per-model scorecard template (dry-run); live execution is blocked pending authorization (`LIVE_BENCHMARK_BLOCKED_PENDING_AUTH`). Historical live runs (Phase 31A/B) are in `benchmark-results/`. **No fabricated benchmark numbers appear in this paper.**

### Appendix G — Unresolved Compliance and Technical Issues

- O-1 **Transitive license closure** (CONDITIONAL): run `pip-licenses`/`license-checker` on `backend`/`bridge` and OSV/`npm audit` on the frontend lockfile, and publish the result before public redistribution.
- O-2 **Official hackathon rules** (UNVERIFIED): confirm submission requirements against official docs.
- O-3 **Submission placeholders** (ACTION REQUIRED): hosted demo URL and demo video URL in `SUBMISSION.md`.
- O-4 **Paid live benchmark** (ACTION REQUIRED, blocked): requires explicit budget authorization.
- O-5 **Load test** (UNVERIFIED): not performed.
- O-6 **Flaky Phase 32 token-tail test** (OPEN): fix nondeterminism.
- O-7 **Local-model terms** (CONDITIONAL): operators review Llama/Hermes terms before commercial use.

### Appendix H — Reproducibility and Audit Methodology

1. Checkout commit `d79b51b`; `git log`/`git branch -a` for history and author metadata.
2. Read of `REQUIREMENTS.md` (byte-identical across phases; SHA-256 `b2e568c0…9970d` per Phase 31B/33 records).
3. Read manifests (`backend/pyproject.toml`, `frontend/package.json`/lockfile, `bridge/pyproject.toml`) and `THIRD_PARTY.md` for dependency/version/license declarations.
4. Inspect `Dockerfile`, compose files, `docs/`, `.rad/` for architecture, deployment, operations and governance.
5. Extract counts: test modules (backend 157, frontend 84 vitest, e2e 36, bridge 11), screenshots (91), defects (58), Phase-33 adversarial findings (33).
6. Full-suite verification (orchestrator + QA, 2026-10-09): backend 2869/1, bridge 300, frontend 1680/tc/build, root 250/2 known host-only, adapters OK.
7. All commands executed were read-only; no paid calls; no file modifications beyond this document.