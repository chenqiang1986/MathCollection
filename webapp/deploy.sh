#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

# ---- Required: edit these for your project ----
PROJECT_ID="${PROJECT_ID:-math-mistake-tracker}"
REGION="${REGION:-us-west1}"
SERVICE="${SERVICE:-math-mistake-tracker}"
BUCKET="${BUCKET:-math_mistake_tracker_bucket}"
SECRET_PREFIX="${SECRET_PREFIX:-MATH_MISTAKE_TRACKER__}"
# ------------------------------------------------

if [[ -z "${PROJECT_ID}" ]]; then
  PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
fi
if [[ -z "${PROJECT_ID}" ]]; then
  echo "ERROR: set PROJECT_ID env var or run 'gcloud config set project <id>'" >&2
  exit 1
fi

echo "Project: ${PROJECT_ID}"
echo "Region:  ${REGION}"
echo "Service: ${SERVICE}"
echo "Bucket:  ${BUCKET}"
echo

# Enable required APIs (idempotent).
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  --project="${PROJECT_ID}"

# Grant the default Cloud Run runtime SA access to the secrets and bucket.
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

for KEY in GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET ANTHROPIC_API_KEY; do
  SECRET_NAME="${SECRET_PREFIX}${KEY}"
  gcloud secrets add-iam-policy-binding "${SECRET_NAME}" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="${PROJECT_ID}" >/dev/null || true
done

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/storage.objectAdmin" >/dev/null || true

# Build the env-var -> secret mapping.
SECRETS="GOOGLE_CLIENT_ID=${SECRET_PREFIX}GOOGLE_CLIENT_ID:latest"
SECRETS+=",GOOGLE_CLIENT_SECRET=${SECRET_PREFIX}GOOGLE_CLIENT_SECRET:latest"
SECRETS+=",ANTHROPIC_API_KEY=${SECRET_PREFIX}ANTHROPIC_API_KEY:latest"

# Deploy. Source-based deploy uses the local Dockerfile via Cloud Build.
gcloud run deploy "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --source="." \
  --execution-environment=gen2 \
  --allow-unauthenticated \
  --port=8080 \
  --cpu=1 \
  --memory=1Gi \
  --timeout=300 \
  --max-instances=1 \
  --set-secrets="${SECRETS}" \
  --add-volume="name=data-vol,type=cloud-storage,bucket=${BUCKET}" \
  --add-volume-mount="volume=data-vol,mount-path=/app/data"

echo
echo "Deployed. URL:"
gcloud run services describe "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format='value(status.url)'
