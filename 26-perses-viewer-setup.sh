#!/usr/bin/env bash
# 26-perses-viewer-setup.sh — Apply EvalHub Perses datasource and dashboard
#
# Run after:
#   oc apply -f 26-cluster-observability-operator.yaml
#   oc apply -f 26-uiplugin-monitoring.yaml
#   oc wait --for=condition=Available uiplugin monitoring --timeout=120s
#
# Authentication: no static secret or ServiceAccount needed. The Perses instance
# uses kubernetesAuth (enabled by COO) to forward the user's Kubernetes session
# token when proxying requests to the Thanos querier at port 9092. The user
# viewing the dashboard must have 'view' permission in project1.
#
# Usage:
#   ./26-perses-viewer-setup.sh [--namespace project1]

set -euo pipefail

NAMESPACE="project1"
[[ "${1:-}" == "--namespace" ]] && NAMESPACE="${2:-project1}"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

log "Setting up EvalHub Perses dashboard for namespace: ${NAMESPACE}"

# ── Perses Datasource + Dashboard ─────────────────────────────────────────────

log "Applying PersesDatasource..."
oc apply -f "$(dirname "$0")/25-perses-datasource.yaml"

log "Applying PersesDashboard..."
oc apply -f "$(dirname "$0")/25-perses-dashboard.yaml"

# ── Verify ────────────────────────────────────────────────────────────────────

log "Checking datasource status..."
sleep 5
oc get persesdatasource evalhub-user-workload-monitoring \
  -n "${NAMESPACE}" \
  -o jsonpath='{.status.conditions[*].message}' && echo ""

log "Checking dashboard status..."
oc get persesdashboard evalhub-continuous-eval \
  -n "${NAMESPACE}" \
  -o jsonpath='{.status.conditions[*].type} {.status.conditions[*].status}' && echo ""

echo ""
log "=== Setup complete ==="
CONSOLE=$(oc get route console -n openshift-console -o jsonpath='{.spec.host}' 2>/dev/null || echo "<console-host>")
echo ""
echo "View dashboard:"
echo "  https://${CONSOLE}/observe/dashboards-perses"
echo "  → Select namespace: ${NAMESPACE}"
echo "  → Select dashboard: EvalHub — Continuous Evaluation & Drift Monitoring"
echo ""
echo "Verify:"
echo "  oc get persesdatasource evalhub-user-workload-monitoring -n ${NAMESPACE}"
echo "  oc get persesdashboard evalhub-continuous-eval -n ${NAMESPACE}"
