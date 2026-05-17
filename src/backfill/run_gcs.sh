#!/usr/bin/env bash
# Run a backfill task against the production GCS bucket by syncing one user's
# data directory down, running the backfill locally, then syncing it back up.
#
# Usage:
#   ./src/backfill/run_gcs.sh classify --email user@example.com [--mode missing|all] [--dry-run]
#
# Env overrides:
#   BUCKET   GCS bucket name (default: math_mistake_tracker_bucket)

set -euo pipefail

BUCKET="${BUCKET:-math_mistake_tracker_bucket}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DATA_DIR="${REPO_ROOT}/data"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <task> --email <user@example.com> [task args...]" >&2
  exit 2
fi

EMAIL=""
DRY_RUN=0
for ((i=1; i<=$#; i++)); do
  case "${!i}" in
    --email)
      j=$((i+1))
      EMAIL="${!j}"
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
  esac
done

if [[ -z "${EMAIL}" ]]; then
  echo "ERROR: --email <user@example.com> is required" >&2
  exit 2
fi

# Use the same sanitization the storage layer uses, to avoid path drift.
SLUG="$(cd "${REPO_ROOT}" && PYTHONPATH=src python -c '
import sys
from lib.storage.paths import sanitize_email
print(sanitize_email(sys.argv[1]))
' "${EMAIL}")"

GCS_USER="gs://${BUCKET}/${SLUG}"
LOCAL_USER="${DATA_DIR}/${SLUG}"

echo "[run_gcs] bucket=${BUCKET}"
echo "[run_gcs] user=${EMAIL} (slug=${SLUG})"
echo "[run_gcs] download: ${GCS_USER}/ -> ${LOCAL_USER}/ (mirror; deletes local extras)"
mkdir -p "${LOCAL_USER}"
gcloud storage rsync --recursive --delete-unmatched-destination-objects \
  "${GCS_USER}" "${LOCAL_USER}"

echo "[run_gcs] running: python -m backfill $*"
( cd "${REPO_ROOT}" && PYTHONPATH=src python -m backfill "$@" )

if [[ "${DRY_RUN}" -eq 1 ]]; then
  echo "[run_gcs] --dry-run detected: skipping upload."
  exit 0
fi

echo "[run_gcs] upload: ${LOCAL_USER}/ -> ${GCS_USER}/ (additive; will not delete remote files)"
gcloud storage rsync --recursive "${LOCAL_USER}" "${GCS_USER}"

echo "[run_gcs] done."
