#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# build-docker.sh — build the Procedural Detective image for a deployment target.
#
#   ./build-docker.sh dev      -> docker compose -f docker-compose.yml build
#   ./build-docker.sh netcup   -> docker compose -f docker-compose.prod.yml build
#   ./build-docker.sh prod     -> docker compose -f docker-compose.prod.yml build
#
# Production is STANDALONE: netcup/prod use ONLY docker-compose.prod.yml. Never
# the base+prod merge — Compose concatenates `ports`, so the base :8000 mapping
# would leak the private backend to the host (ADR-007).
#
# Non-destructive: this only builds. It removes no volumes, runs no image
# cleanup, and uploads no image. Non-zero exit on any failure.
# ---------------------------------------------------------------------------
set -euo pipefail

usage() {
    cat >&2 <<'EOF'
Usage: build-docker.sh <target>

Targets:
  dev        build the local development stack (docker-compose.yml)
  netcup     build the production stack (docker-compose.prod.yml)
  prod       alias for netcup (production)

Examples:
  ./build-docker.sh dev
  ./build-docker.sh netcup
  ./build-docker.sh prod
EOF
}

if [ "$#" -ne 1 ]; then
    echo "error: exactly one target is required (got $#)" >&2
    usage
    exit 2
fi

TARGET="$1"
case "$TARGET" in
    dev)
        COMPOSE_FILE="docker-compose.yml"
        ;;
    netcup|prod)
        COMPOSE_FILE="docker-compose.prod.yml"
        ;;
    *)
        echo "error: invalid target '$TARGET'" >&2
        usage
        exit 2
        ;;
esac

# Use plain `docker` when the socket is reachable WITHOUT root (the normal case
# for a user in the docker group); fall back to `sudo docker` ONLY when it is
# not. Never require root unnecessarily.
if docker info >/dev/null 2>&1; then
    DOCKER="docker"
elif command -v sudo >/dev/null 2>&1; then
    echo "note: docker socket not accessible without sudo; using 'sudo docker'" >&2
    DOCKER="sudo docker"
else
    echo "error: docker is not accessible and sudo is unavailable" >&2
    exit 1
fi

cd "$(dirname "$0")"

echo "==> build-docker: target='$TARGET' configuration='$COMPOSE_FILE'"
# shellcheck disable=SC2086  # $DOCKER may intentionally expand to 'sudo docker'
$DOCKER compose -f "$COMPOSE_FILE" build
echo "==> build-docker: done (target='$TARGET')"
