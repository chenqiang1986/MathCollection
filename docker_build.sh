#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${IMAGE:-mathcollection-web}"

docker build -t "$IMAGE" "$REPO_DIR"

echo "Built $IMAGE"
