#!/usr/bin/env bash
# Deploy the webapp to GKE Standard. Sibling of deploy_gcp.sh (Cloud Run).
#
# What this does, in order:
#   1. Enable required GCP APIs.
#   2. Create an Artifact Registry Docker repo (if missing).
#   3. Build & push the container image via Cloud Build.
#   4. Create the webapp runtime GCP service account and grant it GCS access
#      (the secret-reading identity is separate — see step 7).
#   5. Create the GKE Standard cluster with Workload Identity and the
#      GCS Fuse CSI driver enabled (if missing).
#   6. Bind the webapp's Kubernetes ServiceAccount to its GSA via Workload
#      Identity.
#   7. Install External Secrets Operator + bind its KSA to a separate GSA
#      that has secretAccessor on the three Secret Manager secrets.
#   8. Render the YAML templates in ./k8s, apply ESO config first, wait for
#      ESO to materialize the webapp-secrets Secret, then apply the rest.
#   9. Wait for the Deployment to roll out and the LoadBalancer to get an IP.
#
# Prereq: helm must be installed locally (https://helm.sh/docs/intro/install/).

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
# ESO runs in its own namespace, with its own KSA + GSA. The KSA name
# "external-secrets" is the default created by the upstream Helm chart.
ESO_NAMESPACE="${ESO_NAMESPACE:-external-secrets}"
ESO_KSA_NAME="${ESO_KSA_NAME:-external-secrets}"
ESO_GSA_NAME="${ESO_GSA_NAME:-external-secrets-runtime}"
IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d-%H%M%S)}"
# ------------------------------------------------

if ! command -v helm >/dev/null 2>&1; then
  echo "ERROR: helm is required to install External Secrets Operator." >&2
  echo "  Install: https://helm.sh/docs/intro/install/" >&2
  exit 1
fi

if [[ -z "${PROJECT_ID}" ]]; then
  PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
fi
if [[ -z "${PROJECT_ID}" ]]; then
  echo "ERROR: set PROJECT_ID env var or run 'gcloud config set project <id>'" >&2
  exit 1
fi

GSA_EMAIL="${GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
ESO_GSA_EMAIL="${ESO_GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
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

# ---- 4. Webapp runtime GCP service account + GCS grant ----
# Note the webapp GSA only needs storage access. The Secret Manager grants
# go to the ESO GSA in step 7, because ESO (not the webapp pod) is what
# calls Secret Manager.
if ! gcloud iam service-accounts describe "${GSA_EMAIL}" \
       --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${GSA_NAME}" \
    --display-name="Math mistake tracker runtime (GKE)" \
    --project="${PROJECT_ID}"
fi

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

# ---- 6. Workload Identity binding for the webapp (webapp KSA -> webapp GSA) ----
gcloud iam service-accounts add-iam-policy-binding "${GSA_EMAIL}" \
  --project="${PROJECT_ID}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="serviceAccount:${PROJECT_ID}.svc.id.goog[${NAMESPACE}/${KSA_NAME}]" >/dev/null

# ---- 7. External Secrets Operator + its own GCP identity ----
# ESO is the component that calls Secret Manager. It runs in its own
# namespace with its own KSA, which we map via Workload Identity to an
# ESO-specific GSA. That GSA gets secretAccessor on the three secrets.
# The webapp pod never sees a Secret Manager credential — it only reads
# the k8s Secret that ESO materializes.

if ! gcloud iam service-accounts describe "${ESO_GSA_EMAIL}" \
       --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${ESO_GSA_NAME}" \
    --display-name="External Secrets Operator runtime" \
    --project="${PROJECT_ID}"
fi

for KEY in GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET ANTHROPIC_API_KEY; do
  SECRET_NAME="${SECRET_PREFIX}${KEY}"
  gcloud secrets add-iam-policy-binding "${SECRET_NAME}" \
    --member="serviceAccount:${ESO_GSA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="${PROJECT_ID}" >/dev/null
done

# Install (or upgrade) ESO via Helm. --wait blocks until the CRDs are
# registered and the controller pod is Ready, which we need before applying
# any SecretStore / ExternalSecret manifests.
helm repo add external-secrets https://charts.external-secrets.io >/dev/null 2>&1 || true
helm repo update external-secrets >/dev/null

helm upgrade --install external-secrets external-secrets/external-secrets \
  --namespace "${ESO_NAMESPACE}" \
  --create-namespace \
  --set installCRDs=true \
  --wait

# CRDs occasionally lag the deployment Ready signal; explicitly wait so the
# next `kubectl apply` of SecretStore/ExternalSecret doesn't race.
kubectl wait --for=condition=Established --timeout=2m \
  crd/secretstores.external-secrets.io \
  crd/externalsecrets.external-secrets.io

# Annotate ESO's KSA so its pods are recognized as the ESO GSA when they
# call Google APIs (Workload Identity reads this annotation off the KSA).
kubectl annotate serviceaccount "${ESO_KSA_NAME}" \
  -n "${ESO_NAMESPACE}" \
  "iam.gke.io/gcp-service-account=${ESO_GSA_EMAIL}" \
  --overwrite

# Allow the ESO KSA to impersonate the ESO GSA.
gcloud iam service-accounts add-iam-policy-binding "${ESO_GSA_EMAIL}" \
  --project="${PROJECT_ID}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="serviceAccount:${PROJECT_ID}.svc.id.goog[${ESO_NAMESPACE}/${ESO_KSA_NAME}]" >/dev/null

# Restart ESO so the annotation takes effect on a fresh pod (the metadata
# server caches the KSA->GSA mapping at pod start).
kubectl rollout restart -n "${ESO_NAMESPACE}" deployment/external-secrets
kubectl rollout status  -n "${ESO_NAMESPACE}" deployment/external-secrets --timeout=2m

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

# Apply ESO config first so it has a chance to materialize webapp-secrets
# before the webapp Deployment tries to consume it via envFrom. (Not strictly
# required — kubelet would retry CreateContainerConfigError until the Secret
# appears — but cleaner to sequence explicitly.)
kubectl apply -f "${RENDERED}/secretstore.yaml"
kubectl apply -f "${RENDERED}/externalsecret.yaml"

echo "Waiting for ESO to materialize webapp-secrets..."
DEADLINE=$(($(date +%s) + 120))
while [[ "$(date +%s)" -lt "${DEADLINE}" ]]; do
  if kubectl get secret webapp-secrets -n "${NAMESPACE}" >/dev/null 2>&1; then
    echo "  ...secret created."
    break
  fi
  sleep 2
done

if ! kubectl get secret webapp-secrets -n "${NAMESPACE}" >/dev/null 2>&1; then
  echo "ERROR: ESO did not create webapp-secrets within 2 minutes." >&2
  echo "  Debug with:" >&2
  echo "    kubectl describe externalsecret webapp-secrets -n ${NAMESPACE}" >&2
  echo "    kubectl logs -n ${ESO_NAMESPACE} deploy/external-secrets" >&2
  exit 1
fi

kubectl apply -f "${RENDERED}/serviceaccount.yaml"
kubectl apply -f "${RENDERED}/service.yaml"
kubectl apply -f "${RENDERED}/deployment.yaml"

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
