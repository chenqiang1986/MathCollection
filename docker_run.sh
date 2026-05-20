#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${IMAGE:-mathcollection-web}"
CONTAINER="${CONTAINER:-mathcollection-web}"
HOST_PORT="${HOST_PORT:-8000}"

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

docker run --rm -d \
    --name "$CONTAINER" \
    --env-file "$REPO_DIR/.env" \
    -p "${HOST_PORT}:8000" \
    -v "$REPO_DIR/data:/app/data" \
    "$IMAGE"

echo "Running $CONTAINER on http://localhost:${HOST_PORT}"
