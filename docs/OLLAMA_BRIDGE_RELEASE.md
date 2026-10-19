# Ollama Bridge — Release Procedure (maintainers)

This document describes how the maintainers produce and publish the
`pd-ollama-bridge` standalone client release for Phase 49.

It is **not** the end-user guide — that is `bridge/QUICKSTART.md`. It is also not
the developer/source workflow — that is `bridge/README.md`.

> **Publication is not authorized yet.** Everything up to and including local
> release assembly can be prepared and verified. Do not push, publish a GitHub
> Release, upload binaries, or update production without explicit authorization
> (Phase 49 §53).

---

## 1. Release contract (frozen)

```text
Version:          1.0.0
Windows artifact: pd-ollama-bridge-v1.0.0-windows-x64.zip
Linux artifact:   pd-ollama-bridge-v1.0.0-linux-x64.tar.gz
Checksums:        SHA256SUMS.txt
Manifest:         release-manifest.json
Output dir:       dist/releases/1.0.0/
Hosting:          GitHub Releases
                  https://github.com/DaveKingBerlin/procedural-detective/releases
```

Archive contents (both platforms):

```text
pd-ollama-bridge.exe   (Windows)  /  pd-ollama-bridge   (Linux)
QUICKSTART.md
LICENSE
THIRD_PARTY_NOTICES.txt
```

Supported targets: **Windows 10/11 x64** and **Linux x86_64 / amd64**. macOS,
ARM64, installers, auto-update and package-manager repositories are out of
scope for Phase 49.

### 1.1 Version source

The bridge version comes from a **single authoritative source** consumed by the
build, the CLI and the release tooling (Phase 49 §4). The canonical value is the
`__version__` constant in `bridge/pd_ollama_bridge/__init__.py`:

```python
# bridge/pd_ollama_bridge/__init__.py
__version__ = "1.0.0"
```

`bridge/pyproject.toml` consumes that same attribute dynamically — there is no
literal version string in it:

```toml
# bridge/pyproject.toml
dynamic = ["version"]
# ...
[tool.setuptools.dynamic]
version = {attr = "pd_ollama_bridge.__version__"}
```

The packaged CLI exposes `--version` (alias `-V`), which prints the canonical
version and exits 0:

```text
pd-ollama-bridge 1.0.0
```

> Do not hand-edit version strings in `pyproject.toml`, the build scripts, the
> manifest or the archive names — read them from `pd_ollama_bridge.__version__`.
> `pyproject.toml` uses `dynamic = ["version"]`; do not re-introduce a literal
> `version = ...` there.

---

## 2. Prerequisites

- A **clean release commit** on the release branch.
- **Windows Dev-Box** with Python 3.11+ and the packaging toolchain for the
  native Windows build.
- **Docker** for the Linux build (the Linux binary is built in a container, not
  cross-compiled on Windows).
- `git` available; the tree is a git checkout so the commit can be recorded as
  provenance.

---

## 3. Clean-tree guard (Phase 49 §37)

Canonical release artifacts must be built from a **clean working tree**.

```bash
git status --porcelain
```

The command must print nothing. A dirty tree is not a valid release source: fix
it, commit, and rebuild. Do not publish dirty-tree builds. If a development
override is genuinely required, it must be explicit and the resulting artifact
must not be published as an official release.

Record the exact commit for provenance:

```bash
git rev-parse HEAD
```

---

## 4. Windows build (native, on the Dev-Box)

The Windows x64 executable is built **directly on the Windows Dev-Box** — never
inside Docker, and never produced from a Linux host.

Entry point (Phase 49 §8/§35; PowerShell is canonical, a `.bat` wrapper may also
be provided):

```text
build-bridge-windows.ps1
build-bridge-windows.bat
```

The build script is expected to:

1. fail fast;
2. resolve the bridge version from the canonical source;
3. verify platform/architecture (Windows x64);
4. create a clean release staging directory;
5. build the standalone executable;
6. run a basic executable smoke test;
7. package the release ZIP;
8. compute the SHA-256;
9. write outputs under `dist/releases/<VERSION>/`;
10. return a non-zero exit code on any failure.

Run it from the repository root:

```powershell
.\build-bridge-windows.ps1
```

Then verify the packaged artifact (§6) — not only the source tree.

> Confirm the actual script name in the tree before running; if the release
> tooling uses a different name, use that name and update this document.

---

## 5. Linux build (Docker only)

The Linux x64 executable is built **inside Docker** targeting `linux/amd64`.

Entry points (Phase 49 §12/§35):

```text
build-bridge-linux.sh
bridge/Dockerfile.release
```

Run from the repository root:

```bash
./build-bridge-linux.sh
```

The build must copy the produced binary/archive out into
`dist/releases/<VERSION>/`, and the Linux archive must preserve the executable
bit on `pd-ollama-bridge`.

Verify the binary in a **clean runtime container** that does not contain the
source tree or build toolchain (§6). Document the actual glibc/musl/bundle
result of the chosen packaging — do **not** claim "runs on all Linux
distributions".

> Confirm the actual script/Dockerfile names in the tree before running; update
> this document if they differ.

---

## 6. Verification

### 6.1 Windows packaged smoke

From the **extracted archive** in a clean temporary directory:

```text
pd-ollama-bridge.exe --help      -> exit 0
pd-ollama-bridge.exe --version   -> exit 0, prints the canonical version
pd-ollama-bridge.exe list-models -> runs; reports Ollama availability truthfully
```

Startup must be clean and Ctrl+C must shut the process down cleanly (no stale
lock files). If Ollama is unavailable, the bridge must report it truthfully
rather than pretend success.

### 6.2 Linux clean-container smoke

In a minimal runtime container (e.g. Debian stable slim or Ubuntu LTS) with only
the extracted artifact copied in:

```text
pd-ollama-bridge --help      -> exit 0
pd-ollama-bridge --version   -> exit 0
pd-ollama-bridge list-models -> runs without the build toolchain
```

The executable bit must be preserved and the process must stop cleanly. With no
Ollama present, the bridge must still start and report Ollama unavailable
truthfully.

### 6.3 Artifact integrity / secret scan

Scan the final extracted artifacts for accidental secrets before accepting a
package (Phase 49 §31). Search for markers such as `OPENROUTER`, `GEMINI`,
`API_KEY`, `Authorization:`, `Bearer`, private-key headers and developer paths.
Do **not** print matched secret values. Fail closed on a confirmed credential.
The packaged binary must not embed any `.env`, API key or developer token.

---

## 7. Release assembly (Phase 49 §36)

Once **both** native artifacts exist in `dist/releases/1.0.0/`, assemble the
release:

1. Collect the Windows artifact produced on the Dev-Box.
2. Collect the Linux artifact produced in Docker.
3. Validate both archives (contents, executable bit, smoke results).
4. Generate `SHA256SUMS.txt` from the **final archive bytes**:

   ```text
   <hash>  pd-ollama-bridge-v1.0.0-windows-x64.zip
   <hash>  pd-ollama-bridge-v1.0.0-linux-x64.tar.gz
   ```

5. Generate `release-manifest.json` (Phase 49 §19) — the generated shape, for
   example (values shown are placeholders; `gitCommit` is the SHORT commit,
   with the full SHA in `gitCommitFull`):

   ```json
   {
     "version": "1.0.0",
     "gitCommit": "d6ffd6b",
     "releasedAt": "2026-10-19T17:39:44Z",
     "artifacts": [
       {
         "platform": "windows",
         "arch": "x64",
         "filename": "pd-ollama-bridge-v1.0.0-windows-x64.zip",
         "sha256": "<hash>",
         "size": 0
       },
       {
         "platform": "linux",
         "arch": "x64",
         "filename": "pd-ollama-bridge-v1.0.0-linux-x64.tar.gz",
         "sha256": "<hash>",
         "size": 0
       }
     ],
     "gitCommitFull": "<full commit sha>",
     "dirty": false,
     "buildTimestamp": "2026-10-19T17:39:44Z",
     "packager": "tools/build_release.py (Python 3.12.10)"
   }
   ```

6. Validate version consistency: the version in the archive names, the
   manifest and `pd-ollama-bridge --version` must all match `1.0.0`.

Checksums and the manifest are generated from the final artifacts and are never
hand-maintained in more than one place. Build provenance (Phase 49 §38) records
at least: bridge version, git commit, target platform, target architecture,
build timestamp and packager/tool version — in the manifest or build metadata.

Expected output layout:

```text
dist/releases/1.0.0/
  pd-ollama-bridge-v1.0.0-windows-x64.zip
  pd-ollama-bridge-v1.0.0-linux-x64.tar.gz
  SHA256SUMS.txt
  release-manifest.json
```

`dist/` is git-ignored — do **not** commit the compiled binaries or archives to
normal Git history (Phase 49 §55).

---

## 8. Publication (NOT AUTHORIZED YET)

When — and only when — publication is explicitly authorized:

1. Create a GitHub Release tagged `v1.0.0` at
   `https://github.com/DaveKingBerlin/procedural-detective/releases` and upload
   the two archives, `SHA256SUMS.txt` and `release-manifest.json`.
2. Point the website's Local Ollama download section at the immutable, versioned
   asset URLs (not a single mutable "latest" URL):

   ```text
   https://github.com/DaveKingBerlin/procedural-detective/releases/download/v1.0.0/pd-ollama-bridge-v1.0.0-windows-x64.zip
   https://github.com/DaveKingBerlin/procedural-detective/releases/download/v1.0.0/pd-ollama-bridge-v1.0.0-linux-x64.tar.gz
   ```

   These URLs do **not** work until the release is published.
3. Run the production download smoke test (§54): click each download from the
   website, confirm the correct versioned archive downloads and its checksum
   matches. Also verify the Quick Start, Checksums and Source links.

If publication is not performed, report:

```text
NOT RUN — publication not authorized
```

---

## 9. Rollback / re-release notes

- Never overwrite a published version's assets in place; cut a new version
  instead.
- If an artifact is wrong, delete the release, fix the source, rebuild from a
  clean commit, and re-assemble (§7) before re-publishing.
- Keep the `release-manifest.json` git commit field in sync with the tagged
  commit so a download can always be traced back to its exact source revision.
