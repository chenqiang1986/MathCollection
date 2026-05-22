#!/bin/sh
# Container entrypoint. Hydrates secret env vars from Google Secret Manager
# when running on GCP (signaled by GCP_PROJECT_ID being set), then execs
# gunicorn. On Cloud Run --set-secrets the keys are already set, so the
# Python helper no-ops. Locally with no GCP_PROJECT_ID, this script just
# passes through.
set -e

if [ -n "${GCP_PROJECT_ID:-}" ]; then
  eval "$(python /app/webapp/load_secrets.py)"
fi

exec gunicorn \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers 1 \
  --threads 8 \
  --timeout 120 \
  webapp.src.app:app
