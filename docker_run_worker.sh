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

docker run -d \
    --name "$CONTAINER" \
    --env-file "$REPO_DIR/.env" \
    -v "$REPO_DIR/data:/app/data" \
    -v "$HOME/.claude:/root/.claude" \
    -v "$HOME/.claude.json:/root/.claude.json" \
    -e CLAUDE_CODE_USE_OAUTH=1 \
    --restart unless-stopped \
    "$IMAGE"

echo "Running $CONTAINER (logs: docker logs -f $CONTAINER)"
