#!/usr/bin/env bash
# 22-drift-monitor.sh — Behavioral drift detection via EvalHub
#
# Runs a full combined-safety-alignment collection and compares scores against
# a recorded baseline. Exits non-zero if any score drifts beyond the delta
# threshold, making it suitable as a deployment gate in CI/CD.
#
# Drift detection in EvalHub is BEHAVIORAL, not statistical:
#   - You are measuring whether the model's evaluated behavior has degraded
#     compared to a baseline you recorded, not whether input feature
#     distributions shifted.
#   - This is the right frame for GenAI: does the model still answer correctly
#     and safely, compared to when you last promoted it to production?
#
# Note: EvalHub mlflow_run_id is null in the current SDK version. Baseline
# metrics are stored directly in a Kubernetes ConfigMap as JSON.
#
# Usage:
#   # 1. Record a baseline (once, after a known-good deployment)
#   ./22-drift-monitor.sh --record-baseline
#
#   # 2. Run drift check (in CI/CD or before a model update)
#   ./22-drift-monitor.sh
#   # exit 0 = no drift detected (safe to proceed)
#   # exit 1 = drift detected (block promotion, alert team)
#
#   # 3. Drift check against a specific collection
#   ./22-drift-monitor.sh --collection nightly-safety-check
#
# Environment variables (with defaults):
#   EVALHUB_URL        EvalHub route (auto-detected via oc if not set)
#   EVALHUB_TENANT     Namespace (default: project1)
#   MODEL_URL          vLLM inference endpoint
#   MODEL_NAME         Model name matching ISVC name (G7)
#   DRIFT_DELTA        Max allowed score degradation from baseline (default: 0.05)
#   COLLECTION         Collection to run (default: combined-safety-alignment)
#   BASELINE_STORE     ConfigMap name storing baseline metrics (default: evalhub-drift-baseline)

set -euo pipefail

EVALHUB_TENANT="${EVALHUB_TENANT:-project1}"
MODEL_URL="${MODEL_URL:-http://qwen3-8b-fp8-predictor.project1.svc.cluster.local:8080/v1}"
MODEL_NAME="${MODEL_NAME:-qwen3-8b-fp8}"
COLLECTION="${COLLECTION:-combined-safety-alignment}"
DRIFT_DELTA="${DRIFT_DELTA:-0.05}"
BASELINE_STORE="${BASELINE_STORE:-evalhub-drift-baseline}"
RECORD_BASELINE=false

log()  { echo "[$(date -u +%H:%M:%S)] $*"; }
die()  { echo "[$(date -u +%H:%M:%S)] ERROR: $*" >&2; exit 1; }

# Use uv run evalhub if uv is available (preferred — uses repo's pinned SDK),
# otherwise fall back to bare evalhub (e.g. inside a container).
if command -v uv &>/dev/null && [[ -f "pyproject.toml" ]]; then
  EVALHUB="uv run evalhub"
else
  EVALHUB="evalhub"
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --record-baseline)  RECORD_BASELINE=true; shift ;;
    --collection)       COLLECTION="$2"; shift 2 ;;
    --drift-delta)      DRIFT_DELTA="$2"; shift 2 ;;
    --help|-h)
      sed -n '2,/^set -/p' "$0" | grep '^#' | sed 's/^# \?//'; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

# ── Auto-detect EvalHub URL ───────────────────────────────────────────────────

if [[ -z "${EVALHUB_URL:-}" ]]; then
  EVALHUB_URL="https://$(oc get route evalhub -n "${EVALHUB_TENANT}" \
    -o jsonpath='{.spec.host}' 2>/dev/null)" \
    || die "Cannot detect EVALHUB_URL — set it or ensure 'oc' is logged in."
fi

log "EvalHub:    ${EVALHUB_URL}"
log "Collection: ${COLLECTION}  Model: ${MODEL_NAME}  Max drift delta: ${DRIFT_DELTA}"

# ── Temp files for JSON data (avoids shell quoting issues with JSON content) ──

TMPDIR_WORK=$(mktemp -d)
trap 'rm -rf "${TMPDIR_WORK}"' EXIT
COL_FILE="${TMPDIR_WORK}/collection.json"
RES_FILE="${TMPDIR_WORK}/result.json"
BASE_FILE="${TMPDIR_WORK}/baseline.json"

# ── Configure evalhub CLI ─────────────────────────────────────────────────────

TOKEN=$(oc create token evalhub-user-sa -n "${EVALHUB_TENANT}" --duration=2h)
${EVALHUB} config set base_url "${EVALHUB_URL}"
${EVALHUB} config set token "${TOKEN}"
${EVALHUB} config set tenant "${EVALHUB_TENANT}"
${EVALHUB} config set insecure true
${EVALHUB} health || die "EvalHub health check failed"

# ── Fetch collection definition (thresholds) ──────────────────────────────────

log "Fetching collection definition for: ${COLLECTION}"
${EVALHUB} collections describe "${COLLECTION}" --format json > "${COL_FILE}"

# ── Run evaluation ────────────────────────────────────────────────────────────

log "Running collection: ${COLLECTION} (this may take 15–60 min)..."
${EVALHUB} collections run "${COLLECTION}" \
  --model-url "${MODEL_URL}" \
  --model-name "${MODEL_NAME}" \
  --wait \
  --format json > "${RES_FILE}"
log "Collection run complete."

# ── Record baseline mode ──────────────────────────────────────────────────────

if [[ "${RECORD_BASELINE}" == "true" ]]; then
  log "Recording metrics as drift baseline in ConfigMap '${BASELINE_STORE}'..."

  python3 - "${RES_FILE}" <<'PYEOF'
import json, sys
res  = json.load(open(sys.argv[1]))
item = res[0] if isinstance(res, list) else res
metrics = {}
for b in item["results"]["benchmarks"]:
    metrics[b["id"]] = b.get("metrics", {})
print(json.dumps(metrics))
PYEOF
  BASELINE_METRICS=$(python3 - "${RES_FILE}" <<'PYEOF'
import json, sys
res  = json.load(open(sys.argv[1]))
item = res[0] if isinstance(res, list) else res
metrics = {}
for b in item["results"]["benchmarks"]:
    metrics[b["id"]] = b.get("metrics", {})
print(json.dumps(metrics))
PYEOF
)

  echo "${BASELINE_METRICS}" > "${BASE_FILE}"

  oc create configmap "${BASELINE_STORE}" \
    --from-literal=collection="${COLLECTION}" \
    --from-literal=model_name="${MODEL_NAME}" \
    --from-literal=recorded_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --from-file=metrics="${BASE_FILE}" \
    -n "${EVALHUB_TENANT}" \
    --dry-run=client -o yaml | oc apply -f -

  log "Baseline recorded — ConfigMap '${BASELINE_STORE}' in namespace '${EVALHUB_TENANT}'"
  log "Future drift checks will compare against this run."
  exit 0
fi

# ── Drift comparison ──────────────────────────────────────────────────────────

oc get configmap "${BASELINE_STORE}" \
  -n "${EVALHUB_TENANT}" \
  -o jsonpath='{.data.metrics}' 2>/dev/null > "${BASE_FILE}" \
  || die "No baseline found. Run './22-drift-monitor.sh --record-baseline' after a known-good deployment."

[[ -s "${BASE_FILE}" ]] \
  || die "Baseline ConfigMap '${BASELINE_STORE}' exists but metrics key is empty."

STORED_AT=$(oc get configmap "${BASELINE_STORE}" \
  -n "${EVALHUB_TENANT}" \
  -o jsonpath='{.data.recorded_at}' 2>/dev/null || echo "unknown")
STORED_COLLECTION=$(oc get configmap "${BASELINE_STORE}" \
  -n "${EVALHUB_TENANT}" \
  -o jsonpath='{.data.collection}' 2>/dev/null || echo "unknown")

log "Comparing against baseline recorded ${STORED_AT} (collection: ${STORED_COLLECTION})"

${EVALHUB} collections describe "${COLLECTION}" --format json > "${COL_FILE}"

python3 - "${COL_FILE}" "${RES_FILE}" "${BASE_FILE}" "${DRIFT_DELTA}" <<'PYEOF'
import json, sys

col_file, res_file, base_file, drift_delta = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
delta = float(drift_delta)

col_item = json.load(open(col_file))
col_item = col_item[0] if isinstance(col_item, list) else col_item

res_item = json.load(open(res_file))
res_item = res_item[0] if isinstance(res_item, list) else res_item

baseline = json.load(open(base_file))

# Build threshold map from collection definition
thresholds = {}
for b in col_item.get("benchmarks", []):
    bid    = b["id"]
    metric = b["primary_score"]["metric"]
    lower  = b["primary_score"]["lower_is_better"]
    thresh = b["pass_criteria"]["threshold"]
    thresholds[bid] = (metric, thresh, lower)

# Extract current metrics
current = {}
for b in res_item["results"]["benchmarks"]:
    current[b["id"]] = b.get("metrics", {})

print()
print(f"{'Benchmark':<48} {'Metric':<24} {'Baseline':>9} {'Current':>9} {'Delta':>8} {'Status':>8}")
print("-" * 110)

drift_found  = False
thresh_fails = []

for bid, (metric_name, thresh, lower) in thresholds.items():
    b_val = baseline.get(bid, {}).get(metric_name)
    c_val = current.get(bid, {}).get(metric_name)

    if b_val is None or c_val is None:
        status = "MISSING"
        print(f"{bid:<48} {metric_name:<24} {str(b_val or 'N/A'):>9} {str(c_val or 'N/A'):>9} {'---':>8} {status:>8}")
        continue

    raw_delta   = c_val - b_val
    degradation = raw_delta if lower else -raw_delta
    threshold_fail = (lower and c_val > thresh) or (not lower and c_val < thresh)
    drift_fail     = degradation > delta

    if threshold_fail:
        thresh_fails.append(f"{bid}: {metric_name}={c_val:.4f} {'>' if lower else '<'} {thresh}")
    if drift_fail:
        drift_found = True

    status = "THRESH" if threshold_fail else ("DRIFT" if drift_fail else "OK")
    print(f"{bid:<48} {metric_name:<24} {b_val:>9.4f} {c_val:>9.4f} {raw_delta:>+8.4f} {status:>8}")

print()
exit_code = 0
if thresh_fails:
    print("ABSOLUTE THRESHOLD FAILURES:")
    for f in thresh_fails: print(f"  {f}")
    exit_code = 1
if drift_found:
    print(f"DRIFT DETECTED — score degraded > delta={delta} from baseline")
    exit_code = 1
if exit_code == 0:
    print(f"No drift — all scores within delta={delta} and absolute thresholds.")
sys.exit(exit_code)
PYEOF
