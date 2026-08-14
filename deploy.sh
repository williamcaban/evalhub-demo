#!/usr/bin/env bash
# deploy.sh — EvalHub tenant deployment for project1 on RHOAI 3.5
# Follows: https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html-single/working_with_mlflow/index
#          https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html-single/evaluating_ai_systems/index
#
# Usage: ./deploy.sh [--skip-mlflow]
#   --skip-mlflow: skip MLflow deployment (if already done for this cluster)
set -euo pipefail

SKIP_MLFLOW=false
[[ "${1:-}" == "--skip-mlflow" ]] && SKIP_MLFLOW=true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACE="project1"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

# ── Pre-flight checks ─────────────────────────────────────────────────────────
log "Pre-flight checks"
oc whoami &>/dev/null || die "Not logged in. Run: oc login --token=<token> --server=<url>"
oc get crd evalhubs.trustyai.opendatahub.io &>/dev/null || die "EvalHub CRD not found. Is RHOAI 3.5 with TrustyAI operator installed?"

# ── Step 1: Enable MLflow Operator ───────────────────────────────────────────
if [[ "$SKIP_MLFLOW" == "false" ]]; then
  log "Step 1/5 — Enabling RHOAI MLflow Operator"
  oc patch datasciencecluster default-dsc --type=merge \
    -p '{"spec":{"components":{"mlflowoperator":{"managementState":"Managed"}}}}' 2>&1

  log "  Waiting for mlflow-operator pod to start (up to 60s)..."
  for i in $(seq 1 12); do
    RUNNING=$(oc get pods -n redhat-ods-applications -l "app=mlflow-operator" \
      --field-selector status.phase=Running --no-headers 2>/dev/null | wc -l)
    [[ "$RUNNING" -gt 0 ]] && break
    sleep 5
  done

  log "Step 2/5 — Deploying RHOAI MLflow CR (cluster-scoped, in redhat-ods-applications)"
  # MLflow is cluster-scoped — only one instance per cluster
  # Check if it already exists
  if oc get mlflow mlflow -n redhat-ods-applications &>/dev/null; then
    log "  MLflow CR already exists — skipping"
  else
    oc apply -f "$SCRIPT_DIR/03-mlflow/mlflow-rhoai-cr.yaml"
    log "  MLflow CR created. Waiting for Available=True (up to 3 min)..."
    for i in $(seq 1 36); do
      STATUS=$(oc get mlflow mlflow -n redhat-ods-applications \
        -o jsonpath='{.status.conditions[?(@.type=="Available")].status}' 2>/dev/null || echo "")
      [[ "$STATUS" == "True" ]] && break
      echo -n "."
      sleep 5
    done
    echo ""
  fi

  MLF_URL=$(oc get mlflow mlflow -n redhat-ods-applications \
    -o jsonpath='{.status.address.url}' 2>/dev/null || echo "unknown")
  log "  MLflow internal URL: $MLF_URL"
else
  log "Step 1-2/5 — Skipping MLflow (--skip-mlflow set)"
fi

# ── Step 3: Namespace ─────────────────────────────────────────────────────────
log "Step 3/5 — Creating namespace: $NAMESPACE"
oc apply -f "$SCRIPT_DIR/01-namespace.yaml"

# ── Step 4: RBAC ─────────────────────────────────────────────────────────────
log "Step 4/5 — Applying RBAC (evalhub-user-sa + pseudo-resource Role)"
oc apply -f "$SCRIPT_DIR/02-rbac.yaml"

# ── Step 5: EvalHub CR + Egress NetPol ──────────────────────────────────────
log "Step 5/5 — Deploying EvalHub CR and egress NetworkPolicy"
oc apply -f "$SCRIPT_DIR/04-evalhub-cr.yaml"
# Allow eval job pods (label: component=evaluation-job) to reach HuggingFace Hub
oc apply -f "$SCRIPT_DIR/05-allow-egress-netpol.yaml"

log "  Waiting for EvalHub to become Ready (up to 2 minutes)..."
for i in $(seq 1 24); do
  PHASE=$(oc get evalhub evalhub -n "$NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
  [[ "$PHASE" == "Ready" ]] && break
  echo -n "."
  sleep 5
done
echo ""

# ── Verification ──────────────────────────────────────────────────────────────
echo ""
log "=== Deployment Complete ==="

echo ""
echo "EvalHub status:"
oc get evalhub evalhub -n "$NAMESPACE" \
  -o custom-columns="NAME:.metadata.name,PHASE:.status.phase,READY:.status.ready,PROVIDERS:.status.activeProviders"

echo ""
echo "Provider ConfigMaps:"
oc get configmap -n "$NAMESPACE" --no-headers 2>/dev/null | grep evalhub-provider || echo "  (none — reconcile may still be in progress)"

echo ""
EVALHUB_HOST=$(oc get route evalhub -n "$NAMESPACE" -o jsonpath='{.spec.host}' 2>/dev/null || echo "not-created")
echo "EvalHub API: https://${EVALHUB_HOST}"
echo "MLflow URL:  $(oc get mlflow mlflow -n redhat-ods-applications -o jsonpath='{.status.address.url}' 2>/dev/null || echo 'check: oc get mlflow -A')"

echo ""
echo "Quick test (copy-paste):"
echo "  EVALHUB_HOST=${EVALHUB_HOST}"
echo "  TOKEN=\$(oc create token evalhub-user-sa -n ${NAMESPACE} --duration=1h)"
echo "  curl -sk -H \"Authorization: Bearer \$TOKEN\" -H \"X-Tenant: ${NAMESPACE}\" \\"
echo "    \"https://\${EVALHUB_HOST}/api/v1/evaluations/providers\" | python3 -m json.tool"
