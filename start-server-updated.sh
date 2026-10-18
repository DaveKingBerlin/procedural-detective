#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-}"

usage() {
  echo "Usage: $0 <dev|devbox|netcup|prod>"
  echo
  echo "  dev      Start Dev-Box using prod + lan + debug compose files"
  echo "  devbox   Alias for dev"
  echo "  netcup   Start production using docker-compose.prod.yml only"
  echo "  prod     Alias for netcup"
}

if [[ -z "$TARGET" ]]; then
  usage
  exit 2
fi

docker_cmd() {
  if docker info >/dev/null 2>&1; then
    DOCKER=(docker)
  elif command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    DOCKER=(sudo docker)
  else
    echo "ERROR: Docker is not available or the current user has no Docker access."
    exit 1
  fi
}

require_file() {
  [[ -f "$1" ]] || {
    echo "ERROR: $1 not found. Run this script from the repository root."
    exit 1
  }
}

case "$TARGET" in
  dev|devbox)
    export ENVIRONMENT="development"
    export CADDY_DOMAIN="enshrouded-server"
    export PD_GENERATION_DEBUG_LOGS="true"

    COMPOSE_FILES=(
      -f docker-compose.prod.yml
      -f docker-compose.lan.yml
      -f docker-compose.debug.yml
    )

    require_file docker-compose.prod.yml
    require_file docker-compose.lan.yml
    require_file docker-compose.debug.yml
    docker_cmd

    echo "==> Target: $TARGET"
    echo "==> Environment: $ENVIRONMENT"
    echo "==> Caddy domain: $CADDY_DOMAIN"
    echo "==> Debug generation logs: $PD_GENERATION_DEBUG_LOGS"
    echo "==> Compose: prod + lan + debug"
    echo "==> Starting Dev-Box services with --force-recreate..."

    "${DOCKER[@]}" compose "${COMPOSE_FILES[@]}" up -d --force-recreate

    echo
    echo "==> Container status:"
    "${DOCKER[@]}" compose "${COMPOSE_FILES[@]}" ps
    ;;

  netcup|prod)
    require_file docker-compose.prod.yml
    docker_cmd

    echo "==> Target: $TARGET"
    echo "==> Compose: docker-compose.prod.yml"
    echo "==> Starting production services..."

    "${DOCKER[@]}" compose -f docker-compose.prod.yml up -d

    echo
    echo "==> Container status:"
    "${DOCKER[@]}" compose -f docker-compose.prod.yml ps
    ;;

  *)
    echo "ERROR: Unknown target '$TARGET'."
    usage
    exit 2
    ;;
esac

echo
echo "==> Server start completed successfully."
