#!/usr/bin/env bash
# 11-eval-arc-easy.sh — Submit an ARC Easy benchmark via EvalHub lm-evaluation-harness
#
# Evaluates qwen3-8b-fp8 (deployed in project1) on the ARC Easy reasoning benchmark.
# lm-evaluation-harness pulls the dataset from HuggingFace (online mode required).
#
# Prerequisites:
#   - EvalHub running in project1 (04-evalhub-cr.yaml applied)
#   - qwen3-8b-fp8 InferenceService ready in project1 (06-qwen3-judge.yaml applied)
#   - permitOnline: allow set in DSC (see README cluster-admin setup)
#
# Usage: ./11-eval-arc-easy.sh
set -euo pipefail

# ── Variables ──────────────────────────────────────────────────────────────────
EVALHUB_HOST=$(oc get route evalhub -n project1 -o jsonpath='{.spec.host}')
MODEL_URL=http://qwen3-8b-fp8-predictor.project1.svc.cluster.local:8080
MODEL_NAME=qwen3-8b-fp8
NAMESPACE=project1
# Qwen/Qwen3-8B is public on HuggingFace — no HF token needed for tokenizer download
TOKENIZER=Qwen/Qwen3-8B
# arc_easy benchmark: 2376 samples — runtime ~9 min on a single L4 GPU (Qwen3-8B-FP8)
# Note: the lm-evaluation-harness adapter ignores a 'limit' parameter — full dataset always runs

# ── Token ──────────────────────────────────────────────────────────────────────
echo "Generating EvalHub token..."
TOKEN=$(oc create token evalhub-user-sa -n "${NAMESPACE}" --duration=4h)

# ── Submit job ─────────────────────────────────────────────────────────────────
echo "Submitting arc_easy evaluation (${LIMIT} samples)..."
RESPONSE=$(curl -sk -X POST \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Tenant: ${NAMESPACE}" \
  -H "Content-Type: application/json" \
  "https://${EVALHUB_HOST}/api/v1/evaluations/jobs" \
  -d "{
    \"name\": \"arc-easy-qwen3\",
    \"experiment\": {\"name\": \"evalhub-lmeval-evals\"},
    \"model\": {
      \"url\": \"${MODEL_URL}/v1\",
      \"name\": \"${MODEL_NAME}\"
    },
    \"benchmarks\": [{
      \"id\": \"arc_easy\",
      \"provider_id\": \"lm_evaluation_harness\",
      \"parameters\": {
        \"tokenizer\": \"${TOKENIZER}\"
      }
    }]
  }")

# ── Extract job ID ─────────────────────────────────────────────────────────────
JOB_ID=$(echo "${RESPONSE}" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('resource',{}).get('id','ERROR'))")

if [[ "${JOB_ID}" == "ERROR" ]]; then
  echo "ERROR: Job submission failed. Response:"
  echo "${RESPONSE}"
  exit 1
fi

echo ""
echo "Job submitted successfully."
echo "Job ID: ${JOB_ID}"
echo ""

# ── Poll status ────────────────────────────────────────────────────────────────
echo "Checking initial status..."
curl -sk \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Tenant: ${NAMESPACE}" \
  "https://${EVALHUB_HOST}/api/v1/evaluations/jobs/${JOB_ID}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); \
    print('State:', d['status']['state'])"

echo ""
echo "── How to monitor ──────────────────────────────────────────────────────────"
echo ""
echo "Watch pods:"
echo "  oc get pods -n ${NAMESPACE} -w 2>/dev/null | grep -v evalhub"
echo ""
echo "Poll job status:"
cat <<EOF
  TOKEN=\$(oc create token evalhub-user-sa -n ${NAMESPACE} --duration=4h)
  curl -sk \\
    -H "Authorization: Bearer \${TOKEN}" \\
    -H "X-Tenant: ${NAMESPACE}" \\
    "https://${EVALHUB_HOST}/api/v1/evaluations/jobs/${JOB_ID}" \\
    | python3 -c "import sys,json; d=json.load(sys.stdin); \\
      print(d['name'], '->', d['status']['state'])"
EOF
echo ""
echo "Retrieve results when complete:"
cat <<EOF
  curl -sk \\
    -H "Authorization: Bearer \${TOKEN}" \\
    -H "X-Tenant: ${NAMESPACE}" \\
    "https://${EVALHUB_HOST}/api/v1/evaluations/jobs/${JOB_ID}" \\
    | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('State:', d['status']['state'])
for b in d.get('results', {}).get('benchmarks', []):
    print('Benchmark:', b['id'])
    for k, v in b.get('metrics', {}).items():
        if 'stderr' not in k:
            print(f'  {k}: {v}')
"
EOF
