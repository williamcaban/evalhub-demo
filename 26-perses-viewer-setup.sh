#!/usr/bin/env bash
# 26-perses-viewer-setup.sh — Create Perses viewer SA, token Secret, and apply dashboard
#
# Run after:
#   oc apply -f 26-cluster-observability-operator.yaml
#   oc apply -f 26-uiplugin-monitoring.yaml
#   oc wait --for=condition=Available uiplugin monitoring --timeout=120s
#
# Usage:
#   ./26-perses-viewer-setup.sh [--namespace project1]

set -euo pipefail

NAMESPACE="project1"
[[ "${1:-}" == "--namespace" ]] && NAMESPACE="${2:-project1}"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

log "Setting up Perses viewer for namespace: ${NAMESPACE}"

# ── ServiceAccount ────────────────────────────────────────────────────────────

log "Creating perses-viewer ServiceAccount..."
oc create serviceaccount perses-viewer \
  -n "${NAMESPACE}" \
  --dry-run=client -o yaml | oc apply -f -

log "Granting view ClusterRole to perses-viewer (for Thanos querier auth)..."
oc adm policy add-cluster-role-to-user view \
  "system:serviceaccount:${NAMESPACE}:perses-viewer"

# Grant the Perses server SA read access to the token secret.
# The Perses server (perses-sa in openshift-operators) looks up the datasource
# secret from the PersesDatasource CR's namespace at query time. Without this
# RoleBinding it cannot read the secret and all panels return 'secret not found'.
log "Granting perses-sa read access to evalhub-monitoring-token Secret..."
cat <<RBAC | oc apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: perses-secret-reader
  namespace: ${NAMESPACE}
rules:
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["get"]
    resourceNames: ["evalhub-monitoring-token"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: perses-sa-secret-reader
  namespace: ${NAMESPACE}
subjects:
  - kind: ServiceAccount
    name: perses-sa
    namespace: openshift-operators
roleRef:
  kind: Role
  name: perses-secret-reader
  apiGroup: rbac.authorization.k8s.io
RBAC

# ── Token Secret ──────────────────────────────────────────────────────────────
# The Perses instance runs in openshift-operators and resolves secret references
# from ITS OWN namespace — not from the PersesDatasource CR's namespace.
# The secret must therefore be created in openshift-operators (cluster-admin).
#
# Long-lived token (1 year). Rotate annually or automate with short-lived tokens.

log "Creating evalhub-monitoring-token Secret in openshift-operators..."
TOKEN=$(oc create token perses-viewer \
  -n "${NAMESPACE}" \
  --duration=8760h)

oc create secret generic evalhub-monitoring-token \
  --from-literal=token="${TOKEN}" \
  -n openshift-operators \
  --dry-run=client -o yaml | oc apply -f -

log "Token Secret created in openshift-operators"

# ── Perses Datasource + Dashboard ─────────────────────────────────────────────

log "Applying PersesDatasource..."
oc apply -f "$(dirname "$0")/25-perses-datasource.yaml"

log "Applying PersesDashboard..."
oc apply -f "$(dirname "$0")/25-perses-dashboard.yaml"

# ── Verify ────────────────────────────────────────────────────────────────────

log "Waiting for dashboard to become available..."
# Note: the dashboard reaches Available=True even while Degraded=True
# (Degraded reflects a Perses instance sync issue, not a rendering block).
oc wait \
  --for=condition=Available \
  persesdashboard evalhub-continuous-eval \
  -n "${NAMESPACE}" \
  --timeout=60s || true  # non-fatal if condition name differs across COO versions

echo ""
log "=== Setup complete ==="
echo ""
echo "View dashboard:"
CONSOLE=$(oc get route console -n openshift-console -o jsonpath='{.spec.host}' 2>/dev/null || echo "<console-host>")
echo "  https://${CONSOLE}/observe/dashboards-perses"
echo "  → Select namespace: ${NAMESPACE}"
echo "  → Select dashboard: EvalHub — Continuous Evaluation & Drift Monitoring"
echo ""
echo "Verify:"
echo "  oc get persesdatasource evalhub-user-workload-monitoring -n ${NAMESPACE}"
echo "  oc get persesdashboard evalhub-continuous-eval -n ${NAMESPACE}"
