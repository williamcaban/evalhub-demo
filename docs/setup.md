# EvalHub Setup Guide — RHOAI 3.5

Full step-by-step setup for the evalhub-demo on RHOAI 3.5 EA2+.  
See [README.md](../README.md) for an overview of what's included.

---

## Prerequisites

Verify the TrustyAI operator is installed and the EvalHub CRD exists:

```bash
oc get crd evalhubs.trustyai.opendatahub.io
# Expected: evalhubs.trustyai.opendatahub.io   <date>
```

Verify GPU nodes are available:

```bash
oc get nodes -l node.kubernetes.io/instance-type=g6.12xlarge
```

---

## Step 1 — Cluster-admin setup (once per cluster)

These patches and provider registrations require cluster-admin. They persist across tenant deployments.

### 1a — DSC patches

```bash
# Enable MLflow operator
oc patch datasciencecluster default-dsc --type=merge \
  -p '{"spec":{"components":{"mlflowoperator":{"managementState":"Managed"}}}}'

# Disable MCP guardrails mode — required for EvalHub controller to activate (G1)
oc patch datasciencecluster default-dsc --type=merge \
  -p '{"spec":{"components":{"trustyai":{"mcpGuardrailsMode":false}}}}'

# Allow lm-evaluation-harness jobs to reach HuggingFace Hub for datasets (G2)
oc patch datasciencecluster default-dsc --type=merge \
  -p '{"spec":{"components":{"trustyai":{"eval":{"lmeval":{"permitOnline":"allow"}}}}}}'
```

### 1b — Register community providers

The TrustyAI operator copies provider ConfigMaps into each tenant namespace automatically when a matching EvalHub CR is deployed.

```bash
oc apply -f 10-inspect-provider.yaml   # Inspect AI — 20 benchmarks (Petri + inspect-evals)
oc apply -f 13-garak-provider.yaml     # Garak — 9 red-teaming benchmarks
oc apply -f 14-ruler-provider.yaml     # RULER — 13 long-context benchmarks (image pending)
oc apply -f 15-ragas-provider.yaml     # RAGAS — 2 RAG evaluation benchmarks (API fix pending)
oc apply -f 16-guidellm-provider.yaml  # GuideLLM — 7 performance benchmarks
```

### 1c — Register custom collections

Collections registered via operator ConfigMap get stable string IDs (not UUIDs).

```bash
oc apply -f 20-collections-system.yaml   # combined-reasoning, knowledge, coding, safety, garak, guidellm
oc apply -f 21-collections-day2.yaml     # nightly-safety-check (Day 2 continuous eval)
```

---

## Step 2 — Deploy MLflow (cluster-scoped, once per cluster)

```bash
oc apply -f 03-mlflow/mlflow-rhoai-cr.yaml

oc wait deployment/mlflow \
  -n redhat-ods-applications \
  --for=condition=Available --timeout=180s

# Verify internal URL
oc get mlflow mlflow -n redhat-ods-applications \
  -o jsonpath='{.status.address.url}'
# Expected: https://mlflow.redhat-ods-applications.svc:8443/mlflow
```

Expose the MLflow UI externally:

```bash
oc create route passthrough mlflow \
  -n redhat-ods-applications \
  --service=mlflow \
  --port=8443

echo "MLflow UI: https://$(oc get route mlflow -n redhat-ods-applications \
  -o jsonpath='{.spec.host}')/mlflow"
```

> MLflow uses workspace isolation. Evaluations from `project1` appear under the `project1` workspace — select it from the workspace dropdown in the UI.

---

## Step 3 — Deploy tenant resources

```bash
oc apply -f 01-namespace.yaml
oc apply -f 02-rbac.yaml
oc apply -f 04-evalhub-cr.yaml          # includes spec.providers + spec.collections
oc apply -f 05-allow-egress-netpol.yaml  # allows eval job pods to reach HuggingFace Hub
```

Or use the deployment script for steps 2–3:

```bash
./deploy.sh               # full deployment including MLflow
./deploy.sh --skip-mlflow # skip MLflow if already deployed for this cluster
```

> **Note**: The script does not apply the cluster-admin DSC patches (Step 1a) or register community providers (Step 1b/1c). Run those manually first.

---

## Step 4 — Wait for EvalHub

```bash
oc wait deployment/evalhub \
  -n project1 \
  --for=condition=Available --timeout=120s
```

Verify provider and collection ConfigMaps were copied into `project1`:

```bash
oc get configmap -n project1 | grep evalhub-provider
# Expected: evalhub-provider-inspect, evalhub-provider-garak, evalhub-provider-guidellm, …

oc get configmap -n project1 | grep evalhub-collection
# Expected: evalhub-collection-combined-reasoning, evalhub-collection-garak-red-team, …
```

If ConfigMaps are missing after 60 seconds, restart the operator:

```bash
oc delete pod -n redhat-ods-applications \
  -l control-plane=trustyai-service-operator-controller-manager
```

---

## Step 5 — Install and configure the EvalHub CLI

```bash
# Install dependencies (pyproject.toml already declares eval-hub-sdk[cli])
uv sync

# Configure for this cluster (run after each oc login or when the token expires)
uv run evalhub config set base_url \
  "https://$(oc get route evalhub -n project1 -o jsonpath='{.spec.host}')"
uv run evalhub config set token \
  "$(oc create token evalhub-user-sa -n project1 --duration=8h)"
uv run evalhub config set tenant "project1"
uv run evalhub config set insecure true   # self-signed cluster cert

# Verify
uv run evalhub health
# Expected: EvalHub service: healthy
```

> **Package name**: `eval-hub-sdk[cli]` — not `evalhub-sdk`.

Confirm providers and collections:

```bash
uv run evalhub providers list
uv run evalhub collections list
```

---

## Step 6 — Deploy the inference model

```bash
oc apply -f 06-qwen3-judge.yaml

# Wait for predictor pod (image pull + vLLM startup: 3–5 min on first run)
oc wait pod \
  -l serving.kserve.io/inferenceservice=qwen3-8b-fp8 \
  -n project1 \
  --for=condition=Ready --timeout=600s

oc get inferenceservice qwen3-8b-fp8 -n project1
# Expected: READY=True
```

> **Critical — port 8080**: KServe predictor services are headless. Always use `:8080`:
> ```
> http://qwen3-8b-fp8-predictor.project1.svc.cluster.local:8080/v1
> ```

> **Model name**: `model.name` in eval job specs must exactly match the ISVC name. ISVC names cannot contain dots (`llama-3.2-3b` → `llama-32-3b`).

---

## Step 7 — Run individual evaluations

All model URLs use the internal svc address with port 8080.

```bash
# LM Evaluation Harness — ARC reasoning (~9 min, 2376 samples)
uv run evalhub eval run --config evals/arc-easy.yaml --wait
# Confirmed: acc=0.834, acc_norm=0.802

# Inspect AI — Petri alignment audit (~8 min, single-endpoint mode)
uv run evalhub eval run --config evals/petri-sycophancy.yaml --wait

# Garak — DAN quick red-team (~5 min)
uv run evalhub eval run --config evals/garak-quick.yaml --wait
# attack_success_rate=1.0 expected without guardrails

# Garak — OWASP LLM Top 10 (20–40 min)
uv run evalhub eval run --config evals/garak-owasp.yaml --wait

# GuideLLM — inference performance benchmark
uv run evalhub eval run --config evals/guidellm-quick.yaml --wait
```

Monitor jobs:

```bash
uv run evalhub eval status                       # list recent jobs
uv run evalhub eval results <job-id>             # metric table
uv run evalhub eval results <job-id> --format json
oc get pods -n project1 -w | grep -v "evalhub\|qwen3"   # watch eval pods
```

---

## Step 8 — Run collections

Collections bundle multiple benchmarks into a single job submission.

```bash
# Nightly safety check (5 benchmarks, ~12-15 min)
uv run evalhub collections run nightly-safety-check \
  --model-url "http://qwen3-8b-fp8-predictor.project1.svc.cluster.local:8080/v1" \
  --model-name "qwen3-8b-fp8" --wait

# Full safety + alignment suite (13 benchmarks, ~60-90 min)
uv run evalhub collections run combined-safety-alignment \
  --model-url "http://qwen3-8b-fp8-predictor.project1.svc.cluster.local:8080/v1" \
  --model-name "qwen3-8b-fp8"

# Garak red-team (7 probes, 3 generations each, ~30-60 min)
uv run evalhub collections run garak-red-team \
  --model-url "http://qwen3-8b-fp8-predictor.project1.svc.cluster.local:8080/v1" \
  --model-name "qwen3-8b-fp8"

# GuideLLM performance
uv run evalhub collections run guidellm-perf \
  --model-url "http://qwen3-8b-fp8-predictor.project1.svc.cluster.local:8080/v1" \
  --model-name "qwen3-8b-fp8"
```

Available Inspect AI collections (all confirmed working via Petri code path):
`inspect-alignment`, `inspect-safety`, `inspect-reasoning`, `inspect-knowledge`, `inspect-coding`, `inspect-cybersecurity`, `inspect-agent`

---

## Day 2 Operations

See **[workshop-day2-continuous-eval.md](workshop-day2-continuous-eval.md)** for the full guide, including:

- Nightly CronJob setup with threshold gates (`21-continuous-eval-cronjob.yaml`)
- Drift baseline recording and comparison (`22-drift-monitor.sh`)
- Pushgateway for per-benchmark Prometheus metrics (`24-pushgateway.yaml`)
- PrometheusRules for alerting (`23-alerting.yaml`, `23-alerting-cluster.yaml`)
- Perses dashboard in the OpenShift Console (`25-perses-*.yaml`, `26-*`)

Quick start for Day 2:

```bash
# Register Day 2 collection and apply CronJob
oc apply -f 21-collections-day2.yaml
oc create secret generic evalhub-runner-config -n project1 \
  --from-literal=evalhub_url="https://$(oc get route evalhub -n project1 -o jsonpath='{.spec.host}')" \
  --from-literal=model_url="http://qwen3-8b-fp8-predictor.project1.svc.cluster.local:8080/v1" \
  --from-literal=model_name="qwen3-8b-fp8"
oc apply -f 21-continuous-eval-cronjob.yaml

# Record a baseline after a known-good run
COLLECTION=nightly-safety-check ./22-drift-monitor.sh --record-baseline

# Run drift check
COLLECTION=nightly-safety-check ./22-drift-monitor.sh
```

See **[monitoring-setup.md](monitoring-setup.md)** for Prometheus alerting and Perses dashboard setup.

---

## Known Issues

### G9 — inspect-evals fail with Responses API

`community-inspect:latest` uses inspect-ai with the OpenAI Responses API format for all non-Petri benchmarks. vLLM rejects these with `BadRequestError`.

**Affected**: `inspect/gsm8k`, `inspect/hellaswag`, `inspect/bbh`, `inspect/winogrande`, `inspect/truthfulqa`, `inspect/humaneval`, `inspect/mbpp`.  
**Not affected**: All `inspect/petri-*` benchmarks.  
**Fix**: Add `responses_api: false` to non-Petri code path in `eval-hub-contrib/adapters/inspect/_routing.py`.

### G8 — Petri multi-model mode blocked (inspect-ai 0.3.246)

`auditor_base_url` / `judge_base_url` require inspect-ai ≥ 0.4.0.  
**Workaround**: Use single-endpoint mode — omit `*_base_url`, pass `auditor_model` and `judge_model` by name. See `PATCH_INSPECT_AI_VERSION.md`.

### petri-oversight-subversion timeout

Qwen3-8B in thinking mode exceeds the 7200-second job timeout on oversight scenarios.  
**Fix**: Set `max_samples: 1, max_turns: 3` in `combined-safety-alignment.yaml`.

### RULER — image not yet published

`quay.io/evalhub/community-ruler:latest` returns `manifest unknown`.

### RAGAS — InstructorLLM API mismatch

`community-ragas:latest` raises `ValueError: Collections metrics only support modern InstructorLLM`. Fix requires updating `main.py` in the container.

### MLflow `mlflow_run_id` null

The EvalHub job response always shows `mlflow_run_id: null`. Find runs in the MLflow UI using the experiment name and job UUID as the run name.

### lm-eval `limit` parameter silently ignored

`arc_easy` always runs all 2376 samples regardless of `limit` parameter (~9 min).

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| EvalHub CR stays at empty phase | `mcpGuardrailsMode: true` blocks EvalHub controller (G1) | `oc patch dsc default-dsc --type=merge -p '{"spec":{"components":{"trustyai":{"mcpGuardrailsMode":false}}}}'` |
| HuggingFace timeout in eval pods | `permitOnline: deny` (G2) | `oc patch dsc ... permitOnline: allow` |
| `Connection refused` to model | KServe headless — pod listens on 8080 not 80 (G3) | Use `:8080` in all model URLs |
| `GatedRepoError: 401` for tokenizer | lm-eval downloads tokenizer using `model.name` (G4) | Add `"tokenizer": "Qwen/Qwen3-8B"` to parameters |
| Eval pods fail after HF download | Egress NetworkPolicy label mismatch (G5) | Verify `05-allow-egress-netpol.yaml` uses `component: evaluation-job` |
| ISVC rejected by webhook | Hardware profile not in namespace (G6) | Omit `hardware-profile` annotation; declare GPU directly in `resources` |
| `404` on `/v1/completions` | `model.name` doesn't match ISVC name (G7) | Match names exactly; no dots allowed in ISVC names |
| inspect-evals fail with `BadRequestError` | Responses API, vLLM unsupported (G9) | Use Petri benchmarks only |
| `Unknown GenerateConfig field: base_url` | Needs inspect-ai ≥ 0.4.0 (G8) | Use single-endpoint mode |
| Provider ConfigMaps missing from project1 | Operator hasn't reconciled | `oc delete pod -n redhat-ods-applications -l control-plane=trustyai-service-operator-controller-manager` |
| `401 Unauthorized` on EvalHub API | Token expired | `uv run evalhub config set token "$(oc create token evalhub-user-sa -n project1 --duration=8h)"` |
| `400 Bad Request: unable_to_authorize_request` | RBAC missing | `oc apply -f 02-rbac.yaml` |
| Collection runs show UUID IDs | Collections created via BYOP API | Re-register via `20-collections-system.yaml` + `oc apply -f 04-evalhub-cr.yaml` |
| EvalHub DB wiped after restart | SQLite in-memory DB | Re-submit jobs |
| MLflow PVC stuck | Migration job error loop | `oc patch pvc mlflow-pvc -n redhat-ods-applications -p '{"metadata":{"finalizers":[]}}' --type=merge` |

---

## Cleanup

```bash
# Remove Day 2 resources (if deployed)
oc delete cronjob nightly-safety-eval -n project1 --ignore-not-found
oc delete secret evalhub-runner-config -n project1 --ignore-not-found
oc delete configmap evalhub-drift-baseline -n project1 --ignore-not-found
oc delete deployment prometheus-pushgateway -n project1 --ignore-not-found
oc delete svc prometheus-pushgateway -n project1 --ignore-not-found
oc delete servicemonitor evalhub-pushgateway -n project1 --ignore-not-found

# Remove alerting rules
oc delete prometheusrule evalhub-availability-alerts -n project1 --ignore-not-found
oc delete prometheusrule evalhub-eval-job-alerts -n openshift-monitoring --ignore-not-found

# Remove Perses dashboard (if deployed)
oc delete persesdashboard evalhub-continuous-eval -n project1 --ignore-not-found
oc delete persesdatasource evalhub-user-workload-monitoring -n project1 --ignore-not-found

# Remove EvalHub tenant resources
oc delete -f 06-qwen3-judge.yaml --ignore-not-found
oc delete -f 04-evalhub-cr.yaml
oc delete -f 02-rbac.yaml
oc delete namespace project1

# Remove community providers and collections (cluster-admin)
for f in 10-inspect-provider.yaml 13-garak-provider.yaml 14-ruler-provider.yaml \
          15-ragas-provider.yaml 16-guidellm-provider.yaml \
          20-collections-system.yaml 21-collections-day2.yaml; do
  oc delete -f "$f" --ignore-not-found
done

# Optionally remove MLflow (affects all tenants on this cluster)
# oc delete -f 03-mlflow/mlflow-rhoai-cr.yaml
# oc patch datasciencecluster default-dsc --type=merge \
#   -p '{"spec":{"components":{"mlflowoperator":{"managementState":"Removed"}}}}'
```
