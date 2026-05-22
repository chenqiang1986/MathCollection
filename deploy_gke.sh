#!/usr/bin/env bash
# Deploy the webapp to GKE Standard. Sibling of deploy_gcp.sh (Cloud Run).
#
# What this does, in order:
#   1. Enable required GCP APIs.
#   2. Create an Artifact Registry Docker repo (if missing).
#   3. Build & push the container image via Cloud Build.
#   4. Create a runtime GCP service account and grant it Secret Manager +
#      GCS access.
#   5. Create the GKE Standard cluster with Workload Identity and the
#      GCS Fuse CSI driver enabled (if missing).
#   6. Bind the Kubernetes ServiceAccount to the GCP service account
#      via Workload Identity.
#   7. (No-op — secrets are fetched at container startup by the entrypoint
#      script via the Secret Manager API, authenticated via Workload Identity.)
#   8. Render the YAML templates in ./k8s and `kubectl apply` them.
#   9. Wait for the Deployment to roll out and the LoadBalancer to get an IP.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

# ---- Required: edit these for your project ----
PROJECT_ID="${PROJECT_ID:-math-mistake-tracker}"
REGION="${REGION:-us-west1}"
ZONE="${ZONE:-us-west1-a}"
CLUSTER="${CLUSTER:-math-mistake-tracker}"
SERVICE="${SERVICE:-math-mistake-tracker}"
BUCKET="${BUCKET:-math_mistake_tracker_bucket}"
SECRET_PREFIX="${SECRET_PREFIX:-MATH_MISTAKE_TRACKER__}"
AR_REPO="${AR_REPO:-math-mistake-tracker}"
NAMESPACE="${NAMESPACE:-default}"
KSA_NAME="${KSA_NAME:-webapp-sa}"
GSA_NAME="${GSA_NAME:-webapp-runtime}"
IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d-%H%M%S)}"
# ------------------------------------------------

if [[ -z "${PROJECT_ID}" ]]; then
  PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
fi
if [[ -z "${PROJECT_ID}" ]]; then
  echo "ERROR: set PROJECT_ID env var or run 'gcloud config set project <id>'" >&2
  exit 1
fi

GSA_EMAIL="${GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${SERVICE}:${IMAGE_TAG}"

echo "Project:   ${PROJECT_ID}"
echo "Region:    ${REGION}"
echo "Cluster:   ${CLUSTER} (zone ${ZONE})"
echo "Image:     ${IMAGE}"
echo "Bucket:    ${BUCKET}"
echo "Namespace: ${NAMESPACE}"
echo

# ---- 1. Enable required APIs (idempotent) ----
gcloud services enable \
  container.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  --project="${PROJECT_ID}"

# ---- 2. Artifact Registry repo ----
if ! gcloud artifacts repositories describe "${AR_REPO}" \
       --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --project="${PROJECT_ID}"
fi

# ---- 3. Build and push image ----
gcloud builds submit \
  --project="${PROJECT_ID}" \
  --tag="${IMAGE}" \
  .

# ---- 4. Runtime GCP service account + IAM grants ----
if ! gcloud iam service-accounts describe "${GSA_EMAIL}" \
       --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${GSA_NAME}" \
    --display-name="Math mistake tracker runtime (GKE)" \
    --project="${PROJECT_ID}"
fi

for KEY in GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET ANTHROPIC_API_KEY; do
  SECRET_NAME="${SECRET_PREFIX}${KEY}"
  gcloud secrets add-iam-policy-binding "${SECRET_NAME}" \
    --member="serviceAccount:${GSA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="${PROJECT_ID}" >/dev/null
done

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${GSA_EMAIL}" \
  --role="roles/storage.objectAdmin" >/dev/null

# ---- 5. GKE Standard cluster ----
# Workload Identity + GCS Fuse CSI driver are required by the manifests.
# Secrets are fetched by the container's entrypoint via the Secret Manager
# API (auth = Workload Identity), so no Secret Manager CSI add-on is needed.
# Cluster creation takes ~5-8 minutes the first time.
if ! gcloud container clusters describe "${CLUSTER}" \
       --zone="${ZONE}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud container clusters create "${CLUSTER}" \
    --project="${PROJECT_ID}" \
    --zone="${ZONE}" \
    --release-channel=regular \
    --machine-type=e2-standard-2 \
    --num-nodes=2 \
    --workload-pool="${PROJECT_ID}.svc.id.goog" \
    --workload-metadata=GKE_METADATA \
    --addons=GcsFuseCsiDriver
fi

gcloud container clusters get-credentials "${CLUSTER}" \
  --zone="${ZONE}" --project="${PROJECT_ID}"

# ---- 6. Workload Identity binding (GSA <-> KSA) ----
gcloud iam service-accounts add-iam-policy-binding "${GSA_EMAIL}" \
  --project="${PROJECT_ID}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="serviceAccount:${PROJECT_ID}.svc.id.goog[${NAMESPACE}/${KSA_NAME}]" >/dev/null

# ---- 8. Render and apply manifests ----
RENDERED="$(mktemp -d)"
trap 'rm -rf "${RENDERED}"' EXIT

for f in k8s/*.yaml; do
  sed -e "s|__IMAGE__|${IMAGE}|g" \
      -e "s|__BUCKET__|${BUCKET}|g" \
      -e "s|__GSA_EMAIL__|${GSA_EMAIL}|g" \
      -e "s|__KSA_NAME__|${KSA_NAME}|g" \
      -e "s|__NAMESPACE__|${NAMESPACE}|g" \
      -e "s|__PROJECT_ID__|${PROJECT_ID}|g" \
      -e "s|__SECRET_PREFIX__|${SECRET_PREFIX}|g" \
      "${f}" > "${RENDERED}/$(basename "${f}")"
done

kubectl apply -f "${RENDERED}/"

# ---- 9. Wait for rollout + external IP ----
kubectl rollout status deployment/math-mistake-tracker \
  -n "${NAMESPACE}" --timeout=5m

echo
echo "Waiting for LoadBalancer external IP..."
for _ in $(seq 1 60); do
  IP="$(kubectl get svc math-mistake-tracker -n "${NAMESPACE}" \
    -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)"
  if [[ -n "${IP}" ]]; then
    echo
    echo "Deployed: http://${IP}"
    exit 0
  fi
  sleep 5
done

echo "WARN: LoadBalancer IP not ready yet. Check with:"
echo "  kubectl get svc math-mistake-tracker -n ${NAMESPACE}"
