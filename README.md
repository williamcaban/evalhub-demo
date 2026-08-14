# EvalHub — RHOAI 3.5 Demo

**Repository**: https://github.com/williamcaban/evalhub-demo  
**License**: Apache 2.0

End-to-end EvalHub deployment on RHOAI 3.5 EA2+ with eight evaluation providers, MLflow
experiment tracking, and pre-built evaluation collections with research-calibrated pass/fail
thresholds. Includes a Qwen3-8B-FP8 judge model on a single NVIDIA L4 GPU.

**Documentation**
- EvalHub product docs: https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html-single/evaluating_ai_systems/index
- MLflow on RHOAI: https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html-single/working_with_mlflow/index

---

## Architecture

```
redhat-ods-applications
  MLflow Server ←── SA token auth (TLS: svc:8443)
        ↑ MLFLOW_TRACKING_URI
project1
  EvalHub Server  ── route: evalhub-project1.apps.<cluster>
  │
  ├── System providers (operator-managed):  lm-evaluation-harness, lighteval, ibm-clear
  ├── Community providers (ConfigMap):       inspect, garak, ruler, ragas, guidellm
  │
  ├── System collections (operator-managed): leaderboard-v2, safety-and-fairness-v1, toxicity-and-ethical-principles
  └── Custom collections (ConfigMap):        combined-reasoning, combined-knowledge, combined-coding,
                                             combined-safety-alignment, garak-red-team, guidellm-perf
  ↓
  Evaluation Jobs (K8s Jobs — one pod per benchmark, created at runtime)
  Qwen3-8B-FP8 ←── InferenceService, 1× L4, project1
```

### Providers

| ID | Name | Benchmarks | Status | File |
|---|---|---|---|---|
| `lm_evaluation_harness` | LM Evaluation Harness | 188 | ✅ | operator-managed |
| `lighteval` | Lighteval | 28 | ✅ generative; logprob needs PR #115 | operator-managed |
| `ibm-clear` | IBM CLEAR | 1 | ✅ | operator-managed |
| `inspect` | Inspect AI | 19 | ⚠️ Petri ✅; inspect-evals ❌ see Known Issues | `10-inspect-provider.yaml` |
| `garak` | Garak | 9 | ✅ | `13-garak-provider.yaml` |
| `ruler` | RULER | 13 | ❌ image not yet published | `14-ruler-provider.yaml` |
| `ragas` | RAGAS | 2 | ❌ InstructorLLM API mismatch | `15-ragas-provider.yaml` |
| `guidellm` | GuideLLM | 7 | ✅ | `16-guidellm-provider.yaml` |

### Collections

All custom collections use the operator ConfigMap mechanism for **stable string IDs** (not UUIDs).

| ID | Category | Benchmarks | Global pass threshold |
|---|---|---|---|
| `combined-reasoning` | reasoning | 9 | 0.45 weighted avg |
| `combined-knowledge` | knowledge | 7 | 0.60 weighted avg |
| `combined-coding` | coding | 5 | 0.30 weighted avg |
| `combined-safety-alignment` | safety | 13 | hard AND-gates (see collection) |
| `garak-red-team` | security | 7 | — |
| `guidellm-perf` | performance | 3 | — |

---

## Prerequisites

Verify TrustyAI operator is installed and the EvalHub CRD exists:

```bash
oc get crd evalhubs.trustyai.opendatahub.io
# Expected: evalhubs.trustyai.opendatahub.io   <date>
```

Verify GPU nodes are available:

```bash
oc get nodes -l node.kubernetes.io/instance-type=g6.12xlarge
```

---

## Step 1 — Cluster-admin setup (run once per cluster)

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

Apply all community provider ConfigMaps. The TrustyAI operator copies them into each tenant
namespace automatically when a matching EvalHub CR is deployed.

```bash
oc apply -f 10-inspect-provider.yaml   # Inspect AI — 19 benchmarks (Petri + inspect-evals)
oc apply -f 13-garak-provider.yaml     # Garak — 9 red-teaming benchmarks
oc apply -f 14-ruler-provider.yaml     # RULER — 13 long-context benchmarks (image pending)
oc apply -f 15-ragas-provider.yaml     # RAGAS — 2 RAG evaluation benchmarks (API fix pending)
oc apply -f 16-guidellm-provider.yaml  # GuideLLM — 7 performance benchmarks
```

### 1c — Register custom collections

Apply all custom collection ConfigMaps. Collections registered this way get stable string IDs.
Collections created via `evalhub collections create` (BYOP API) receive UUIDs instead.

```bash
oc apply -f 20-collections-system.yaml
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

> MLflow uses workspace isolation. Evaluations from `project1` appear under the `project1`
> workspace — select it from the workspace dropdown in the UI.

---

## Step 3 — Deploy tenant resources

```bash
oc apply -f 01-namespace.yaml
oc apply -f 02-rbac.yaml
oc apply -f 04-evalhub-cr.yaml       # includes spec.providers + spec.collections
oc apply -f 05-allow-egress-netpol.yaml  # allows eval job pods to reach HuggingFace Hub
```

Or use the deployment script for steps 2–3:

```bash
./deploy.sh             # full deployment including MLflow
./deploy.sh --skip-mlflow  # skip MLflow if already deployed for this cluster
```

> **Note**: The script does not apply the cluster-admin DSC patches (Step 1a) or register
> community providers (Step 1b/1c). Run those manually before using the script.

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

If ConfigMaps are missing after 60 seconds, restart the operator to force reconciliation:

```bash
oc delete pod -n redhat-ods-applications \
  -l control-plane=trustyai-service-operator-controller-manager
```

---

## Step 5 — Install and configure the EvalHub CLI

> **Install the CLI before running any `evalhub` commands.**

```bash
# Install dependencies (pyproject.toml already declares eval-hub-sdk[cli])
uv sync

# Configure for this cluster (run after each oc login or when the token expires)
uv run evalhub config set base_url \
  "https://$(oc get route evalhub -n project1 -o jsonpath='{.spec.host}')"
uv run evalhub config set token \
  "$(oc create token evalhub-user-sa -n project1 --duration=8h)"
uv run evalhub config set tenant "project1"

# Verify
uv run evalhub health
# Expected: EvalHub service: healthy
```

> **Package name**: `eval-hub-sdk[cli]` — not `evalhub-sdk`.  
> Install with: `uv add "eval-hub-sdk[cli]"`

Confirm providers and collections registered:

```bash
uv run evalhub providers list
# Expected: 8 providers including inspect (19), garak (9), guidellm (7)

uv run evalhub collections list
# Expected: combined-reasoning, combined-knowledge, combined-coding,
#           combined-safety-alignment, garak-red-team, guidellm-perf (stable string IDs)
#           plus leaderboard-v2, safety-and-fairness-v1, toxicity-and-ethical-principles
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
```

Verify the InferenceService:

```bash
oc get inferenceservice qwen3-8b-fp8 -n project1
# Expected: READY=True
```

> **Critical**: Always use port `:8080` — KServe predictor services are headless (G3):
> ```
> http://qwen3-8b-fp8-predictor.project1.svc.cluster.local:8080/v1
> ```
> The ISVC service exposes port 80, but DNS resolves to the pod which listens on 8080.

> **Model name rule**: The `model.name` in eval job specs must exactly match the ISVC name
> — vLLM serves the model under the ISVC name (G7). ISVC names cannot contain dots
> (`llama-3.2-3b-instruct` becomes `llama-32-3b-instruct`).

---

## Step 7 — Run evaluations

All model URLs use the internal svc address with port 8080.

### LM Evaluation Harness — ARC reasoning

```bash
uv run evalhub eval run --config evals/arc-easy.yaml --wait
# arc_easy (2376 samples) — confirmed: acc=0.834, acc_norm=0.802
```

### Inspect AI — Petri alignment audit

```bash
uv run evalhub eval run --config evals/petri-sycophancy.yaml --wait
# concerning/mean=1, admirable/mean=8.5 ✅ (single-endpoint mode — G8 workaround)
```

### Inspect AI — GSM8K math reasoning

```bash
uv run evalhub eval run --config evals/gsm8k.yaml --wait
# ⚠️ May fail with BadRequestError if community-inspect:latest was updated — see Known Issues
```

### Garak — red-team (quick DAN smoke test)

```bash
uv run evalhub eval run --config evals/garak-quick.yaml --wait
# quick DAN 11.0 (~5 min); attack_success_rate=1.0 expected without guardrails
```

### Garak — OWASP LLM Top 10

```bash
uv run evalhub eval run --config evals/garak-owasp.yaml --wait
# Comprehensive probes, 3 generations — allow 20–40 min
```

### GuideLLM — inference performance

```bash
uv run evalhub eval run --config evals/guidellm-quick.yaml --wait
# output_tokens_per_second=16.45, mean_ttft_ms=157, mean_itl_ms=60 (qwen3/L4)
```

### Lighteval — AIME competition math

```bash
uv run evalhub eval run --config evals/lighteval-aime24.yaml --wait
# pass@k=0 expected for Qwen3-8B without thinking mode — AIME is very hard
```

Monitor all jobs:

```bash
uv run evalhub eval status           # list all recent jobs
uv run evalhub eval results <job-id> # per-job metric table
uv run evalhub eval results <job-id> --format json
```

Watch eval pods:

```bash
oc get pods -n project1 -w | grep -v "evalhub\|qwen3"
```

Stream adapter logs for a running job:

```bash
oc logs -n project1 -l "evalhub.io/job-id=<job-id>" -c adapter -f
```

---

## Step 8 — Run collections

Collections bundle multiple benchmarks into a single submitted job. Custom collections use
stable string IDs; use the name directly.

```bash
# Inspect AI alignment audits (Petri — confirmed working)
uv run evalhub collections run inspect-alignment \
  --model-url "http://qwen3-8b-fp8-predictor.project1.svc.cluster.local:8080/v1" \
  --model-name "qwen3-8b-fp8"

# Garak red-team (7 probes, 3 generations each, ~30–60 min total)
uv run evalhub collections run garak-red-team \
  --model-url "http://qwen3-8b-fp8-predictor.project1.svc.cluster.local:8080/v1" \
  --model-name "qwen3-8b-fp8"

# GuideLLM performance (quick + sweep + concurrent)
uv run evalhub collections run guidellm-perf \
  --model-url "http://qwen3-8b-fp8-predictor.project1.svc.cluster.local:8080/v1" \
  --model-name "qwen3-8b-fp8"

# Combined reasoning (9 benchmarks — inspect-evals affected by G9; AIME/MATH work)
uv run evalhub collections run combined-reasoning \
  --model-url "http://qwen3-8b-fp8-predictor.project1.svc.cluster.local:8080/v1" \
  --model-name "qwen3-8b-fp8"

# Combined safety/alignment (13 benchmarks — Petri ✅; garak ✅; oversight-subversion may timeout)
uv run evalhub collections run combined-safety-alignment \
  --model-url "http://qwen3-8b-fp8-predictor.project1.svc.cluster.local:8080/v1" \
  --model-name "qwen3-8b-fp8"
```

Available inspect collections (all confirmed working via Petri code path):
`inspect-alignment`, `inspect-safety`, `inspect-reasoning`, `inspect-knowledge`,
`inspect-coding`, `inspect-cybersecurity`, `inspect-agent`

---

## Known Issues and Limitations

### G9 — inspect-evals fail with Responses API (non-Petri benchmarks)

`community-inspect:latest` was updated to an inspect-ai version that uses the OpenAI
Responses API format (`"include": ["reasoning.encrypted_content"]`, `"role": "developer"`)
for all non-Petri benchmarks. vLLM rejects these with `BadRequestError: EngineCore
encountered an issue`.

**Affected**: `inspect/gsm8k`, `inspect/hellaswag`, `inspect/bbh`, `inspect/winogrande`,
`inspect/truthfulqa`, `inspect/humaneval`, `inspect/mbpp`, and all other inspect-evals.  
**Not affected**: All `inspect/petri-*` benchmarks (the adapter passes `responses_api: false`).  
**Fix needed**: Add `responses_api: false` to the non-Petri code path in
`eval-hub-contrib/adapters/inspect/_routing.py`, or pin a specific image tag.

### G8 — Petri multi-model mode blocked (inspect-ai 0.3.246)

The `auditor_base_url` and `judge_base_url` parameters require inspect-ai ≥ 0.4.0.
Current `community-inspect:latest` ships with 0.3.246.  
**Workaround**: Use single-endpoint mode — point target, auditor, and judge at the same model
URL. Omit `*_base_url`; still pass `auditor_model` and `judge_model` by name.  
**Fix**: Bump `inspect-ai>=0.4.0` in `eval-hub-contrib/adapters/inspect/requirements.txt`,
rebuild, push. See `PATCH_INSPECT_AI_VERSION.md`.

### petri-oversight-subversion timeout

Qwen3-8B in thinking mode generates extremely long reasoning chains for oversight scenarios.
With `max_samples: 3, max_turns: 5`, the job exceeds the 7200-second job timeout.  
**Fix**: Set `max_samples: 1, max_turns: 3` for this benchmark in `combined-safety-alignment.yaml`.

### garak-quick timeout under concurrent load

`garak/quick` with `generations: 5` hits the 600-second benchmark timeout when running
alongside 12 other parallel benchmarks in a collection. Use `generations: 3` in collections.

### lm-eval `limit` parameter silently ignored

Passing `"limit": N` in benchmark parameters has no effect — lm-evaluation-harness always
runs the full dataset. `arc_easy` always runs all 2376 samples (~9 min). Use `tokenizer` to
control gated model access; accept the full dataset runtime.

### MLflow `mlflow_run_id` returns null

The EvalHub job GET response shows `"mlflow_run_id": null` even when the MLflow run completes
successfully. Find runs via the MLflow UI or REST API using the experiment name and the job
UUID as the run name.

### RULER — image not yet published

`quay.io/evalhub/community-ruler:latest` returns `manifest unknown`. Jobs will fail with
`ImagePullBackOff`. Track `eval-hub-contrib/adapters/ruler/` for the image release.

### RAGAS — InstructorLLM API mismatch

The ragas library in `community-ragas:latest` is newer than the adapter expects.
`AnswerRelevancy(llm=llm, embeddings=emb)` raises `ValueError: Collections metrics only
support modern InstructorLLM`. Dataset path `/bundled-data/dataset.jsonl` is correct.
Fix requires updating `main.py` in the community-ragas container.

### swe-bench / bigcodebench require Docker

`inspect/swe-bench` and `inspect/bigcodebench` fail with `FileNotFoundError: docker`.
These benchmarks execute generated code in a Docker sandbox, which is not present in the
`community-inspect` container. Results from these benchmarks are not available in this setup.

### garak `intents` benchmark requires KFP/SDG

The `intents` benchmark in `garak-red-team` is excluded (`intents` is omitted from the
collection). It requires Kubeflow Pipelines + Synthetic Data Generation + S3 storage for
the automated red-teaming pipeline. All other garak benchmarks run inline.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| EvalHub CR stays at empty phase | `mcpGuardrailsMode: true` blocks EvalHub controller (G1) | `oc patch dsc default-dsc ... mcpGuardrailsMode: false` |
| HuggingFace network timeout in eval pods | `permitOnline: deny` (G2) | `oc patch dsc ... permitOnline: allow` |
| `Connection refused` to model, port 80 | KServe headless service — pod listens on 8080 (G3) | Use `:8080` in all model URLs |
| `GatedRepoError: 401` for tokenizer | lm-eval downloads tokenizer using `model.name` (G4) | Add `"tokenizer": "Qwen/Qwen3-8B"` to parameters |
| Eval pods reach HF but still fail | Egress NetworkPolicy label mismatch (G5) | Verify `05-allow-egress-netpol.yaml` uses `component: evaluation-job` |
| ISVC rejected by webhook | Hardware profile `gpu-profile` not in namespace (G6) | Omit `hardware-profile` annotation; declare GPU in `resources` directly |
| `404` on `/v1/completions` | `model.name` doesn't match ISVC name (G7) | Match names exactly; ISVC names cannot contain dots |
| inspect-evals fail with `BadRequestError` | Responses API format, vLLM not supported (G9) | Use Petri benchmarks only; wait for adapter fix |
| `Unknown GenerateConfig field: base_url` | Per-role base_url needs inspect-ai ≥ 0.4.0 (G8) | Use single-endpoint mode; see `PATCH_INSPECT_AI_VERSION.md` |
| Provider ConfigMaps not in project1 | Operator hasn't reconciled | `oc delete pod -n redhat-ods-applications -l control-plane=trustyai-service-operator-controller-manager` |
| `401 Unauthorized` on EvalHub API | Token invalid or expired | `uv run evalhub config set token "$(oc create token evalhub-user-sa -n project1 --duration=8h)"` |
| `400 Bad Request: unable_to_authorize_request` | RBAC missing | `oc apply -f 02-rbac.yaml` |
| MLflow migration job Error loop | PVC stuck | `oc patch pvc mlflow-pvc -n redhat-ods-applications -p '{"metadata":{"finalizers":[]}}' --type=merge` |
| `ImagePullBackOff` for lm-evaluation-harness | Red Hat registry pull secret missing | Verify global pull secret has `registry.redhat.io` credentials |
| Qwen3 pod stuck in `Init:0/1` | OCI image pull in progress (9.5 GB) | Wait 3–5 min; watch: `oc get pod -l serving.kserve.io/inferenceservice=qwen3-8b-fp8 -n project1 -w` |
| Collection runs show UUID IDs | Collections created via BYOP API | Re-register via `20-collections-system.yaml` + `oc apply -f 04-evalhub-cr.yaml` |
| EvalHub DB wiped after restart | SQLite in-memory DB | Re-submit jobs; use `uv run evalhub eval run` again |

---

## Cleanup

```bash
# Remove EvalHub tenant resources
oc delete -f 06-qwen3-judge.yaml --ignore-not-found
oc delete -f 04-evalhub-cr.yaml
oc delete -f 02-rbac.yaml
oc delete namespace project1

# Remove custom provider and collection ConfigMaps (cluster-admin)
for f in 10-inspect-provider.yaml 13-garak-provider.yaml 14-ruler-provider.yaml \
          15-ragas-provider.yaml 16-guidellm-provider.yaml 20-collections-system.yaml; do
  oc delete -f "$f" --ignore-not-found
done

# Optionally remove MLflow (affects all tenants on this cluster)
# oc delete -f 03-mlflow/mlflow-rhoai-cr.yaml
# oc patch datasciencecluster default-dsc --type=merge \
#   -p '{"spec":{"components":{"mlflowoperator":{"managementState":"Removed"}}}}'
```
