# Procedural Detective — Credits & Disclosures

**Project:** Procedural Detective  
**Developer:** David / DaveKingBerlin  
**Repository:** https://github.com/DaveKingBerlin/procedural-detective  
**Project license:** MIT

## 1. Hackathon disclosure summary

For the Victoria VR AI Builder Hackathon, the important disclosure points are:

- third-party libraries and their applicable licences;
- third-party or marketplace assets and required credits;
- AI-assisted development and AI-generated materials;
- confirmation that the participant has the necessary rights to submitted materials.

The hackathon also expects the final project to remain accessible in the browser and the submission to include a public repository, hosted demo, demo video, short project description and focus category.

## 2. Open-source software

Procedural Detective uses standard open-source libraries.

Main dependencies include:

- **Backend:** FastAPI, Pydantic, SQLAlchemy, Alembic, uvicorn, httpx
- **Frontend:** React, React DOM, React Router, Babylon.js, Vite, TypeScript, Vitest, Playwright
- **Optional bridge:** websockets, httpx, certifi

The direct dependencies use permissive open-source licences such as **MIT, BSD-3-Clause and Apache-2.0**.

For the complete dependency inventory, see `THIRD_PARTY.md` and the package manifests/lockfiles in the repository.

## 3. Third-party assets and marketplace content

**No third-party media or paid marketplace assets are bundled with the project.**

The repository does not include externally purchased or downloaded:

- 3D models;
- textures;
- images;
- fonts;
- icons;
- audio assets.

The shipped asset catalogue, environment definitions and procedural scene data are project-authored.

## 4. AI-assisted development

The project was developed with substantial AI assistance.

AI tools were used for:

- programming and refactoring;
- architecture and design assistance;
- debugging;
- test generation and review;
- documentation;
- QA and adversarial review.

The primary development assistant was **DeepSeek via OpenCode**. GPT-family models were also used for review/audit work. Other models were used for compatibility testing and benchmarking.

All AI-assisted changes were reviewed and accepted by the human developer before being committed.

## 5. AI-generated game content

Procedural Detective can optionally use AI to generate structured detective cases.

Supported paths include:

- local models through **Ollama**;
- hosted models through the configurable `live` provider;
- player-supplied **Frontier/BYOK** providers such as OpenRouter-compatible services.

The default demo mode can run without AI.

AI output is treated as a proposal only. Before a case is published, deterministic application logic validates its structure and verifies that the investigation has a valid solution.

Model weights and third-party AI models are **not distributed with this repository**.

## 6. Ownership and attribution

The project-specific application code, game logic, deterministic solver, procedural world system and repository-authored assets were created for this project with AI-assisted development under human direction.

No copied third-party application source or uncredited third-party media was identified in the repository.

Where third-party software is used, its upstream licence terms remain applicable.

## 7. Short jury-facing disclosure

> Procedural Detective is an MIT-licensed, browser-based 3D detective game developed by David / DaveKingBerlin with substantial AI coding assistance. It uses open-source libraries including FastAPI, React and Babylon.js. No third-party or paid marketplace media assets are bundled. Optional AI providers generate structured case proposals, while deterministic validators and a deduction solver verify cases before publication. AI-assisted development and runtime AI usage are disclosed here in accordance with the hackathon's attribution and licensing requirements.

## 8. References

For detailed technical or licensing information, see:

- `LICENSE`
- `THIRD_PARTY.md`
- `README.md`
- `SUBMISSION.md`
- backend/frontend/bridge package manifests and lockfiles

This document is intentionally concise for hackathon submission review.
