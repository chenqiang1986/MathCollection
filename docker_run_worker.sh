#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${IMAGE:-mathcollection-worker}"
CONTAINER="${CONTAINER:-mathcollection-worker}"

REBUILD=0
for arg in "$@"; do
    case "$arg" in
        --rebuild) REBUILD=1 ;;
        *) echo "Unknown argument: $arg" >&2; exit 1 ;;
    esac
done

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

if [[ "$REBUILD" -eq 1 ]]; then
    docker rmi -f "$IMAGE" >/dev/null 2>&1 || true
    docker build -t "$IMAGE" -f "$REPO_DIR/Dockerfile_worker" "$REPO_DIR"
    echo "Built $IMAGE"
fi

# Container env lives in .env.docker (DATABASE_URL points at
# host.docker.internal, the Mac host, rather than the container's localhost).
ENV_FILE="$REPO_DIR/.env.docker"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing $ENV_FILE (copy .env.docker.example and fill it in)" >&2
    exit 1
fi

docker run -d \
    --name "$CONTAINER" \
    --env-file "$ENV_FILE" \
    --add-host=host.docker.internal:host-gateway \
    -v "$REPO_DIR/data:/app/data" \
    -v "$HOME/.claude:/root/.claude" \
    -v "$HOME/.claude.json:/root/.claude.json" \
    --restart unless-stopped \
    "$IMAGE"

echo "Running $CONTAINER (logs: docker logs -f $CONTAINER)"
