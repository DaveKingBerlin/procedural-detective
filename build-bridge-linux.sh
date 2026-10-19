#!/usr/bin/env bash
#
# build-bridge-linux.sh
#
# Phase 49 Sec.12/Sec.13/Sec.14/Sec.35 -- Linux x64 release build for the pd-ollama-bridge
# standalone client. The binary is built INSIDE Docker (never cross-compiled on
# Windows), targeted at linux/amd64, then smoke-tested in a CLEAN runtime
# container that contains none of the source tree or build toolchain.
#
# Output:
#   dist/releases/<VERSION>/pd-ollama-bridge-v<VERSION>-linux-x64.tar.gz
#
# The archive stores pd-ollama-bridge with mode 0755 (executable bit preserved)
# plus QUICKSTART.md, LICENSE and THIRD_PARTY_NOTICES.txt.
#
# It does NOT push, publish or upload anything (Phase 49 Sec.53).
#
# Environment overrides:
#   ALLOW_DIRTY=1        build from a dirty tree (development only; Phase 49 Sec.37)
#   RUNTIME_IMAGE=...    clean runtime smoke image (default python:3.12-slim-bookworm)
#
# Usage:
#   ./build-bridge-linux.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR"
BRIDGE_DIR="$ROOT/bridge"
INIT_PY="$BRIDGE_DIR/pd_ollama_bridge/__init__.py"
DOCKERFILE="$BRIDGE_DIR/Dockerfile.release"

PLATFORM="linux/amd64"
RUNTIME_IMAGE="${RUNTIME_IMAGE:-python:3.12-slim-bookworm}"

log()  { printf '\n==> %s\n' "$*" >&2; }
fail() { printf '\nBUILD FAILED: %s\n' "$*" >&2; exit 1; }

# Docker Desktop on Windows + Git Bash: stop MSYS from rewriting container paths.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

# Convert a host path to the form the Docker CLI expects.
host_path() {
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -w "$1"
    else
        printf '%s' "$1"
    fi
}

command -v docker >/dev/null 2>&1 || fail "docker is required for the Linux build."

# --------------------------------------------------------------------------- #
# 1. Canonical version (single source of truth)
# --------------------------------------------------------------------------- #
[ -f "$INIT_PY" ] || fail "canonical version source not found: $INIT_PY"
VERSION="$(sed -nE 's/^__version__[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$INIT_PY" | head -n1)"
[ -n "$VERSION" ] || fail "could not resolve pd_ollama_bridge.__version__ from $INIT_PY"
log "Canonical version: $VERSION"

# --------------------------------------------------------------------------- #
# 2. Dirty-tree guard (Phase 49 Sec.37)
# --------------------------------------------------------------------------- #
ALLOW_DIRTY="${ALLOW_DIRTY:-0}"
if command -v git >/dev/null 2>&1 && git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    COMMIT="$(git -C "$ROOT" rev-parse HEAD)"
    if [ -n "$(git -C "$ROOT" status --porcelain)" ]; then
        if [ "$ALLOW_DIRTY" != "1" ]; then
            fail "working tree is dirty. Commit your changes or re-run with ALLOW_DIRTY=1 for a NON-publishable development build (Phase 49 Sec.37)."
        fi
        log "WARNING: working tree is DIRTY -- development build, must NOT be published."
        DIRTY=1
    else
        DIRTY=0
    fi
    log "git commit: $COMMIT (dirty: $DIRTY)"
fi

# --------------------------------------------------------------------------- #
# 3. Build the linux/amd64 binary in Docker
# --------------------------------------------------------------------------- #
ROOT_DOCKER="$(host_path "$ROOT")"
DOCKERFILE_DOCKER="$(host_path "$DOCKERFILE")"
WORK="$(mktemp -d)"
EXPORT_DIR="$WORK/export"
mkdir -p "$EXPORT_DIR"
EXPORT_DIR_DOCKER="$(host_path "$EXPORT_DIR")"

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

log "Building $PLATFORM binary in Docker (bridge/Dockerfile.release)"
docker build \
    --platform "$PLATFORM" \
    --file "$DOCKERFILE_DOCKER" \
    --output "type=local,dest=$EXPORT_DIR_DOCKER" \
    "$ROOT_DOCKER"

[ -f "$EXPORT_DIR/pd-ollama-bridge" ] || fail "Linux binary was not exported to $EXPORT_DIR."
log "Built binary: $EXPORT_DIR/pd-ollama-bridge ($(wc -c < "$EXPORT_DIR/pd-ollama-bridge" | tr -d ' ') bytes)"

# --------------------------------------------------------------------------- #
# 4. Clean runtime-container smoke (no source tree, no build toolchain)
# --------------------------------------------------------------------------- #
log "Clean runtime-container smoke in $RUNTIME_IMAGE"
docker run --rm --platform "$PLATFORM" \
    -v "$EXPORT_DIR_DOCKER:/release:ro" \
    "$RUNTIME_IMAGE" bash -euc '
        cp /release/pd-ollama-bridge /tmp/pd-ollama-bridge
        chmod 0755 /tmp/pd-ollama-bridge
        test -x /tmp/pd-ollama-bridge
        echo "--- version ---"
        /tmp/pd-ollama-bridge --version
        echo "--- help ---"
        /tmp/pd-ollama-bridge --help | head -n 4
        echo "--- list-models with no Ollama present (must fail truthfully) ---"
        if /tmp/pd-ollama-bridge list-models --ollama http://127.0.0.1:11434; then
            echo "ERROR: list-models unexpectedly succeeded without Ollama" >&2
            exit 1
        else
            echo "OK: list-models reported Ollama unavailable (non-zero exit)"
        fi
    '

# --------------------------------------------------------------------------- #
# 5. Assemble the executable-preserving tar.gz
# --------------------------------------------------------------------------- #
DIST_DIR="$ROOT/dist/releases/$VERSION"
mkdir -p "$DIST_DIR"
TARBALL="$DIST_DIR/pd-ollama-bridge-v$VERSION-linux-x64.tar.gz"

log "Assembling $TARBALL (executable bit preserved)"
# tar is streamed to stdout so the host needs no Unix tar/permission support.
docker run --rm --platform "$PLATFORM" \
    -e VERSION="$VERSION" \
    -v "$EXPORT_DIR_DOCKER:/release:ro" \
    "$RUNTIME_IMAGE" bash -euc '
        set -e
        for f in pd-ollama-bridge QUICKSTART.md LICENSE THIRD_PARTY_NOTICES.txt; do
            test -f "/release/$f" || { echo "missing release input: $f" >&2; exit 1; }
        done
        mkdir -p /tmp/stage
        cp /release/pd-ollama-bridge /tmp/stage/pd-ollama-bridge
        chmod 0755 /tmp/stage/pd-ollama-bridge
        cp /release/QUICKSTART.md /tmp/stage/QUICKSTART.md
        cp /release/LICENSE /tmp/stage/LICENSE
        cp /release/THIRD_PARTY_NOTICES.txt /tmp/stage/THIRD_PARTY_NOTICES.txt
        chmod 0644 /tmp/stage/QUICKSTART.md /tmp/stage/LICENSE /tmp/stage/THIRD_PARTY_NOTICES.txt
        tar -czf - -C /tmp/stage \
            pd-ollama-bridge QUICKSTART.md LICENSE THIRD_PARTY_NOTICES.txt
    ' > "$TARBALL"

[ -s "$TARBALL" ] || fail "tarball was not created at $TARBALL."

# --------------------------------------------------------------------------- #
# 6. Verify archive contents + executable bit
# --------------------------------------------------------------------------- #
log "Verifying archive contents and executable bit"
docker run --rm --platform "$PLATFORM" \
    -v "$(host_path "$TARBALL"):/tarball.tar.gz:ro" \
    "$RUNTIME_IMAGE" bash -euc '
        tar -tvzf /tarball.tar.gz
        if tar -tvzf /tarball.tar.gz | grep -E "^-rwxr-xr-x .* pd-ollama-bridge$" >/dev/null; then
            echo "OK: pd-ollama-bridge stored executable (0755)"
        else
            echo "ERROR: pd-ollama-bridge executable bit not preserved" >&2
            exit 1
        fi
        for doc in QUICKSTART.md LICENSE THIRD_PARTY_NOTICES.txt; do
            tar -tzf /tarball.tar.gz | grep -qx "$doc" || { echo "ERROR: $doc missing from archive" >&2; exit 1; }
        done
        echo "OK: all release documents present"
    '

# --------------------------------------------------------------------------- #
# 7. SHA-256 + summary
# --------------------------------------------------------------------------- #
if command -v sha256sum >/dev/null 2>&1; then
    SHA="$(sha256sum "$TARBALL" | awk '{print $1}')"
else
    SHA="$(python -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$TARBALL")"
fi
SIZE="$(wc -c < "$TARBALL" | tr -d ' ')"

cat >&2 <<EOF

==============================================================
Linux release artifact built
  version    : $VERSION
  artifact   : $TARBALL
  size       : $SIZE bytes
  sha256     : $SHA
==============================================================
EOF

exit 0
