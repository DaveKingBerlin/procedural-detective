# Third-Party Attribution & Licensing Disclosure

This document truthfully lists the third-party software and data that
Procedural Detective depends on, bundles, or can optionally invoke. The
repository itself is MIT-licensed (`LICENSE`, `backend/pyproject.toml`).

Versions are given exactly where they are fixed in lockfiles and metadata;
elsewhere the declared requirement range is given. `see upstream` marks
projects whose license text is too large or site-maintained to reproduce here.

## 1. Backend (Python) dependencies

Declared in `backend/pyproject.toml` (runtime) and the `[dev]` extra / root
`requirements-dev.txt`. Installed in the container image at build time; exact
resolved versions at the time of writing are shown in parentheses.

| Dependency | Version | Purpose | License |
| --- | --- | --- | --- |
| FastAPI | `>=0.115,<1.0` (0.141.1) | REST API framework | MIT |
| Pydantic | `>=2.7,<3.0` (2.13.5) | Typed validation of API schemas and provider payloads | MIT |
| pydantic-settings | `>=2.3,<3.0` (2.15.0) | Env-driven configuration (`Settings`) | MIT |
| SQLAlchemy | `>=2.0.30,<3.0` (2.0.52) | ORM / persistent case store (SQLite) | MIT |
| Alembic | `>=1.13,<2.0` (1.19.2) | Database migrations (`alembic upgrade head`) | MIT |
| uvicorn | `>=0.30,<1.0` ([standard], 0.52.4) | ASGI server | BSD-3-Clause (see upstream) |
| httpx | `>=0.27,<1.0` (0.28.1) | HTTP client used at runtime by the live- and Ollama-provider adapters; ships in the runtime image via the backend `[runtime]` extra (also in `[dev]`) | BSD-3-Clause |
| pytest (dev) | `>=8,<10` (9.1.1) | Backend test suite | MIT |

Transitive dependencies are resolved by pip from the above; their licenses are
governed by their own metadata and are not redistributed in this document.

### 1.1 Bridge client (Python) dependencies

Declared in `bridge/pyproject.toml` (`pd-ollama-bridge`). The bridge client runs
on the visitor's / operator's own machine — it is **not** installed in the
container image — and is the only Ollama client for a `remote_client`
deployment. No lockfile is shipped; the declared requirement ranges are given.

| Dependency | Version | Purpose | License |
| --- | --- | --- | --- |
| websockets | `>=13` | WebSocket transport to the deployment (bridge client) | BSD-3-Clause |
| httpx | `>=0.24` | HTTP client to the local Ollama server and for model listing | BSD-3-Clause |
| httpcore | transitive (via httpx) | Low-level HTTP transport used by httpx | BSD-3-Clause |
| certifi | `>=2023.7.22` | CA bundle fallback for TLS trust when no usable system bundle exists (never a downgrade) | MPL-2.0 |

#### Packaged standalone binary (Phase 49)

The downloadable `pd-ollama-bridge` release is a **standalone executable** that
**bundles** the Python runtime and the dependencies above (plus their transitive
dependencies) rather than relying on a system Python. The components below were
enumerated directly from the released artifacts — each bundled distribution's
`*.dist-info` and embedded module bytecode in
`dist/releases/1.0.0/pd-ollama-bridge-v1.0.0-windows-x64.zip` and
`pd-ollama-bridge-v1.0.0-linux-x64.tar.gz` — not assumed. Where the binary does
not record a package's version, the version is marked "not recorded in binary".

| Component | Version bundled | Purpose | License |
| --- | --- | --- | --- |
| websockets | 17.1 (both platforms) | WebSocket transport to the deployment | BSD-3-Clause |
| httpx | 0.28.1 | HTTP client to the local Ollama server and for model listing | BSD-3-Clause |
| httpcore | 1.0.9 | Low-level HTTP transport used by httpx | BSD-3-Clause |
| certifi | 2026.07.22 (both platforms) | Bundled CA trust store (also shipped as `certifi/cacert.pem`) | MPL-2.0 |
| anyio | not recorded in binary | Async I/O primitives used by httpx/httpcore | MIT |
| idna | 3.19 (Windows) / 3.20 (Linux) | Internationalized domain names for httpx | BSD-3-Clause |
| h11 | 0.16.0 | HTTP/1.1 protocol implementation used by httpcore | MIT |
| typing_extensions | 3.15 (both platforms) | Backports for the bundled interpreter | PSF-2.0 |
| click | 8.5.0 (Windows only) | CLI helper pulled in by the PyInstaller toolchain | BSD-3-Clause |
| pygments | 2.21.0 (Windows only) | Syntax highlighter pulled in by the PyInstaller toolchain | BSD-2-Clause |
| packaging | 26.3 (Windows only) | Version/packaging utilities pulled in by the PyInstaller toolchain | Apache-2.0 OR BSD-2-Clause |
| CPython runtime | 3.12 | Embedded interpreter (`python312.dll` / `python3.12`) | PSF License Agreement (see upstream) |
| PyInstaller bootloader | PyInstaller 6.x | Standalone executable packaging | GPL-2.0-or-later **with the PyInstaller bootloader exception** (permits bundling non-GPL applications); see upstream |
| OpenSSL | 3.0.16 (Windows) / 3.0.22 (Linux) | TLS for the bundled `ssl` module (`libcrypto-3.dll`/`libssl-3.dll` on Windows; `libcrypto.so.3`/`libssl.so.3` on Linux) | Apache-2.0 |
| libffi | 8 (`libffi-8.dll` Windows / `libffi.so.8` Linux) | Foreign-function interface for the bundled `ctypes` module | MIT |
| Microsoft Visual C++ runtime | VCRUNTIME140 (`VCRUNTIME140.dll`, Windows only) | Native C runtime required by the bundled CPython build | Microsoft VC++ Redistributable (proprietary; see upstream) |

The Windows and Linux artifacts are built on different platforms and do not
bundle an identical set: `click`, `pygments` and `packaging` appear only in the
Windows `.exe`; the native libraries are platform-specific.

Bundling a dependency does not remove its license obligations: the licenses
above apply to the packaged binary as well. Each release archive ships a
`THIRD_PARTY_NOTICES.txt` (source: `bridge/THIRD_PARTY_NOTICES.txt`) reproducing
these notices. License identifiers were verified against the bundled
distributions' metadata (`.dist-info`) and the installed package metadata, not
assumed.

## 2. Frontend (JavaScript) dependencies

Declared in `frontend/package.json`; exact locked versions from
`frontend/package-lock.json`. The runtime packages are built into the shipped
SPA; the dev-time packages build it.

| Dependency | Version | Purpose | License |
| --- | --- | --- | --- |
| @babylonjs/core | 8.56.2 (locked) | 3D engine powering the investigation scene | Apache-2.0 |
| react | 19.3.0 (locked) | UI framework | MIT |
| react-dom | 19.3.0 (locked) | UI rendering | MIT |
| react-router | 7.18.3 (locked) | Client-side navigation | MIT |
| vite (dev) | 7.3.6 (locked) | Build tool / dev server | MIT |
| typescript (dev) | 5.9.3 (locked) | Type checking and SPA compilation | Apache-2.0 |
| vitest (dev) | 3.2.7 (locked) | Frontend unit tests | MIT |
| jsdom (dev) | 26.1.0 (locked) | Browser DOM for tests | MIT |
| @playwright/test (dev) | 1.63.0 (locked) | Browser end-to-end test runner (`e2e/`) | Apache-2.0 |
| @vitejs/plugin-react (dev) | 4.7.0 (locked) | Vite React integration | MIT |
| @types/node, @types/react, @types/react-dom (dev) | 24.13.4 / 19.3.0 / 19.3.0 (locked) | Typing stubs | MIT |

The complete transitive dependency tree and its integrity hashes are recorded
in `frontend/package-lock.json`.

## 3. Container base images

| Image | Version | Purpose | License |
| --- | --- | --- | --- |
| python:3.12-slim | 3.12 (Debian "slim") | Runtime base image carrying the backend and the built SPA | Debian software is under its own licenses (predominantly GPL); see upstream |
| node:24-alpine | Node 24 (Alpine) | Build stage only — compiles the SPA; not in the final image | Alpine/BusyBox under their own licenses; see upstream |
| caddy:2-alpine | Caddy 2 (Alpine) | **Optional production TLS edge** (`docker-compose.prod.yml`): automatic HTTPS, HTTP→HTTPS redirect, HSTS, request limits/timeouts, reverse proxy to the private backend. Shipped as a deployment option; not part of the app image itself | Apache-2.0 (see upstream) |

The final image runs as a non-root user and contains no build-time node
toolchain (see `Dockerfile`).

## 4. Bundled / shipped project assets

All assets shipped inside the repo or the container are **project-authored**
and fall under the repository's MIT license. There are no third-party fonts,
icons, textures, audio files, 3D models or image assets bundled anywhere:

- `assets/catalog/catalog.json` — the declarative, byte-stable asset catalog
  (v1 contract, `catalogVersion`-gated). Project-owned data.
- `assets/environments/*.json` — the five environment kits (apartment, office,
  hotel suite, warehouse, mansion): declarative zone/anchor/lighting
  descriptors. Project-owned data.
- `screenshots/evidence/*.png` — test/QA-captured screenshot evidence from
  phases 8-17. Project-captured, referenced by the README.
- Any procedural 3D assets ("proc.*") generated at runtime are deterministic
  compositions derived from the above project data — no third-party media.

## 5. Optional AI runtime and model disclosure

**This is a local-first design.** AI/procedural content is generated by a local
LLM **only when the operator enables it** (`GENERATION_PROVIDER=ollama`, with a
local Ollama server); **the deterministic demo (`GENERATION_PROVIDER=fake`, the
default) uses no AI** — it replays the shipped, validation-locked golden case
offline with zero credentials, zero network and zero cost.

- **Ollama** — the local/remote model runtime consumed by the opt-in Ollama
  provider (`backend/app/services/ollama_driver.py`). It is **not bundled**;
  the operator installs and runs it separately. Ollama itself is MIT-licensed
  open source (see upstream).
- **Models** — downloaded by the operator from the Ollama registry / Hugging
  Face; model weights are **never redistributed** in this repository:
  - `hermes3:8b` — the operator-showcased model for Phase17 (local-AI
    showcase). Hermes 3 is an open model by **Nous Research** built on the
    **Llama** architecture (base model by Meta).
  - `llama3.2:3b` — the documented default `OLLAMA_MODEL` (`llama3.2:3b`,
    Meta's Llama 3.2 small model).
  These models carry their own upstream license terms (e.g. the Llama
  community license by Meta; Hermes by Nous Research); see upstream before
  commercial use.
- **Cloud mode (optional)** — `GENERATION_PROVIDER=live` exists as an
  opt-in, HTTPS-only path (`LLM_API_KEY` / `LLM_MODEL` / `LIVE_PROVIDER_URL`).
  It is **not** the default, is never used by the deterministic demo, and the
  provider boundary is replaceable (`backend/app/generation/live_provider.py`).
  The browser never talks to a provider directly — only the backend does.

The model **proposes** structured case/evidence/world data; deterministic
validators, the deduction solver, the Asset Oracle and the procedural renderer
**verify and construct** the playable investigation. The model never proves a
case and never runs generated code.