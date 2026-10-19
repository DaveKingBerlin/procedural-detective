# Phase 49 — Independent QA Report

Repo: `C:\Users\Fujitsu\coding\procedural-detective`
Branch: `phase49/ollama-bridge-distribution` (dirty working tree)
Base HEAD: `d6ffd6b1f7ee2bdcd4f686df88d5a631a96e3982`
QA role: independent verification; no product source edited; nothing pushed/published.

## Verdict: PHASE 49 QA PASS

One NEW product defect: **DEF-122 (LOW, documentation only)**. No release,
runtime, packaging, security or website defect found.

---

## Duty 1 — Release artifact integrity (PASS)

Independently recomputed with `Get-FileHash -Algorithm SHA256`:

| artifact | bytes | recomputed SHA-256 |
| --- | --- | --- |
| pd-ollama-bridge-v1.0.0-windows-x64.zip | 12033641 | b3f3c0c4e1a74fffdd3e91344cbc592103ec7ec982ef674036ba36ea6dd0b663 |
| pd-ollama-bridge-v1.0.0-linux-x64.tar.gz | 10132229 | a3ef0ac54eaf48f941b8ef40c382e4f1d95acc3d2841c0b74f62361cb301126c |

- Both match `SHA256SUMS.txt` and `release-manifest.json`.
- Manifest `size` values (12033641 / 10132229) equal the real byte sizes.
- Both filenames contain `v1.0.0`; manifest `version` `1.0.0` == source
  `pd_ollama_bridge.__version__` `1.0.0`.
- `frontend/public/releases/release-manifest.json` is byte-identical to
  `dist/releases/1.0.0/release-manifest.json`.
- `tools/build_release.py` (positive path) regenerated SHA256SUMS + manifest
  **IDENTICAL** to the checked-in files (evidence: `build_release_positive.txt`).
- Provenance: manifest `gitCommit`/`gitCommitFull` == HEAD; `dirty: true`
  (honest, matches the dirty tree).

## Duty 2 — Windows package smoke (PASS)

Extracted ZIP to a clean temp dir. Members are EXACTLY:
`pd-ollama-bridge.exe`, `QUICKSTART.md`, `LICENSE`, `THIRD_PARTY_NOTICES.txt`
(no extra, no `.env`, no dotfiles, no dev/credential files).

- `pd-ollama-bridge.exe --version` -> `pd-ollama-bridge 1.0.0`, exit 0.
- `pd-ollama-bridge.exe --help` -> usage printed, exit 0.

## Duty 3 — Linux package smoke (PASS)

- tar.gz members: `pd-ollama-bridge` (0755), `QUICKSTART.md` (0644),
  `LICENSE` (0644), `THIRD_PARTY_NOTICES.txt` (0644). Executable bit preserved.
- Ran in CLEAN `python:3.12-slim-bookworm` and `debian:stable-slim` (no Python,
  no pip, no pyinstaller/gcc/make) mounting ONLY the artifact:
  - `--version` -> `pd-ollama-bridge 1.0.0`, exit 0.
  - `--help` -> exit 0.
  - `list-models` with no Ollama -> exit 1, truthful
    `✗ Ollama not reachable at http://127.0.0.1:11434`.
- Linkage: glibc-dependent (`libc.so.6`, libz, libdl, libpthread) — matches the
  documented target (Debian bookworm glibc 2.36); NOT static/musl.
- No build toolchain required at runtime (proven on Debian 13 slim with no
  Python at all).

## Duty 4 — Secret scan (PASS)

- Extracted-content + raw-binary scan (`qa_scan.py`): 0 credentials, 0 markers
  in the Windows ZIP and Linux tar.gz (including the exe/binaries).
- Deep PyInstaller CArchive scan (`qa_pyinstaller_scan.py`) decompresses the
  embedded PYZ (753 Windows / 384 Linux modules) and scans the compressed
  payload the flat scan cannot see: 0 credentials; 2 benign markers
  (`Bearer` in a cli.py docstring; `PRIVATE KEY` in the stdlib `ssl` module).
- `tools/build_release.py` FAILS CLOSED: a planted fake `sk-or-v1-…` secret in
  a copied ZIP produced exit 1 (`RELEASE ASSEMBLY FAILED: secret scan failed
  closed`), printed only label/file/offset, and the secret value never appeared
  in the output; no checksums/manifest were written.

## Duty 5 — Website (PASS)

- `npm --prefix frontend test` -> 101 files / 2134 tests passed.
- `typecheck` -> pass. `build` -> pass (incl. `scan:bundle` OK).
- Built `frontend/dist/releases/release-manifest.json` identical to source and
  to `dist/releases/1.0.0/release-manifest.json`.
- Independent real-Chromium probe against the PRODUCTION build
  (`qa_web_probe.cjs`): **18/18** — section renders; Windows + Linux links
  visible with canonical versioned URLs; version `Version 1.0.0`; Quick Start /
  checksums / source links; both platforms always present; NO artifact fetch and
  NO download event on mount (only the same-origin manifest is fetched);
  missing-manifest (404) -> graceful unavailable state with Quick Start/Source
  still available and the rest of the page intact.

## Duty 6 — Security / defaults (PASS)

- Bridge source has NO listener/port/`/health`/`serve`/`bind` (outbound-only
  WSS client). QUICKSTART states this truthfully.
- Ollama default `http://127.0.0.1:11434` (loopback); `urls.py`,
  `ollama_client.py`, `protocol.py`, `config.py`, `bridge_config.py` are
  UNCHANGED (`git diff` empty) — URL validation preserved.
- No CORS/origin change: `backend/` untouched by Phase 49.
- Default logging: no prompt/token/body/env leak (test + CLI only logs
  origin/endpoint/mode; `--debug` opt-in).
- No release binaries/archives Git-tracked (`git ls-files` empty; `dist/`
  ignored; `frontend/public/releases/` intentionally not ignored).

## Duty 7 — Docs truthfulness (PASS, 1 LOW defect)

PRIVACY.md, QUICKSTART.md, OLLAMA_BRIDGE_RELEASE.md, README.md:
- No "prompts never leave your machine" claim — the docs explicitly state the
  server RECEIVES prompts/jobs + structured result.
- No "digitally signed" / "verified publisher" claim — the docs state the
  binary is UNSIGNED and document the SmartScreen step.
- No documented `/health` endpoint (docs say there is none).
- URLs/checksums/sizes are real; the docs state download links only resolve
  after publication (not yet authorized).
- DEF-122 (LOW): §1.1 of OLLAMA_BRIDGE_RELEASE.md is stale (says the version
  lives in pyproject.toml and the CLI has no `--version`).

## Duty 8 — Regression (PASS)

- Bridge suite: `python -m pytest bridge/tests -q -p no:cacheprovider
  --basetemp <temp>` -> **369 passed**.
- Frontend test/typecheck/build -> pass (see Duty 5).
- Phase 44/45/47/48 + Demo (8 backend files) -> **296 passed, 1 failed**; the
  sole failure is the known env/timing
  `test_phase21b_demo_truthfulness.py::test_ollama_probe_hang_dto_via_bounded_seam`
  (`assert 2.625 < 1.0`); `backend/` is UNCHANGED by Phase 49.
- `python -m tools.release_check` -> 20 checks, 2 FAILING: PRE-EXISTING
  README.md/SUBMISSION.md hosting/video placeholders (present at HEAD, not in
  the Phase 49 diff).

## Residual risk

- **NON-BLOCKING**: `dist/` artifacts are dirty-tree builds (`dirty: true` in
  the manifest). They must NOT be published as the official v1.0.0; rebuild
  from a clean commit before any publication (Phase 49 §34/§37). Publication is
  NOT authorized.
- **NON-BLOCKING**: website download URLs are GitHub Releases placeholders that
  404 until the release is published; UI degrades gracefully and docs say so.
- **PRE-EXISTING / ENVIRONMENT-ONLY**:
  `test_ollama_probe_hang_dto_via_bounded_seam` (timing; backend untouched);
  `tools.release_check` README/SUBMISSION placeholders.
- **ENVIRONMENT-ONLY**: pytest `tmp`/`.pytest_cache` permission denials on this
  Windows host (worked around with `--basetemp`/`-p no:cacheprovider`).
