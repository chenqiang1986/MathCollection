#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${IMAGE:-mathcollection-web}"
CONTAINER="${CONTAINER:-mathcollection-web}"
HOST_PORT="${HOST_PORT:-8000}"

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
    docker build -t "$IMAGE" "$REPO_DIR"
    echo "Built $IMAGE"
fi

# Container env lives in .env.docker (DATABASE_URL points at
# host.docker.internal, the Mac host, rather than the container's localhost).
ENV_FILE="$REPO_DIR/.env.docker"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing $ENV_FILE (copy .env.docker.example and fill it in)" >&2
    exit 1
fi

# Apply the Postgres schema and sync every user's problems from their JSON
# files before the server starts, so request handling never triggers a DB
# sync. Version-gated, so this is cheap when nothing changed. `set -e` aborts
# the launch if the DB is unreachable rather than serving against a missing
# schema.
#
# Run this on the Mac host directly (not in a container): it talks to Postgres
# over localhost via .env, and reads/writes the same data/ tree the container
# mounts. Uses the project venv if present, else the system python3.
echo "Applying DB schema + syncing data..."
PYTHON="$REPO_DIR/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="python3"
( cd "$REPO_DIR" && PYTHONPATH=. "$PYTHON" -m common.db_setup )

docker run -d \
    --name "$CONTAINER" \
    --env-file "$ENV_FILE" \
    --add-host=host.docker.internal:host-gateway \
    -p "${HOST_PORT}:${HOST_PORT}" \
    -v "$REPO_DIR/data:/app/data" \
    -v "$HOME/.claude:/root/.claude" \
    -v "$HOME/.claude.json:/root/.claude.json" \
    --restart unless-stopped \
    "$IMAGE"

echo "Running $CONTAINER on http://localhost:${HOST_PORT}"
