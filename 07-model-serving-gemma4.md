# Model Serving — RedHatAI/gemma-4-26B-A4B-it-NVFP4

Deploy and authenticate the `RedHatAI/gemma-4-26B-A4B-it-NVFP4` model using RHOAI 3.5
Distributed Inference (llm-d) in `project1`, then use it as an EvalHub evaluation target.

**Documentation references**  
- Distributed inference auth: https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/deploy_models_using_distributed_inference_with_llm-d/enabling-authentication-and-authorization-for-llm-inference-service_distributed-inference  
- Model card: https://huggingface.co/RedHatAI/gemma-4-26B-A4B-it-NVFP4

---

## Model Overview

| Property | Value |
|---|---|
| HuggingFace ID | `RedHatAI/gemma-4-26B-A4B-it-NVFP4` |
| Architecture | Gemma4ForConditionalGeneration |
| Input / Output | Text + Image → Text (multimodal) |
| Quantization | NVFP4 (FP4 weights + FP4 activations via LLM Compressor) |
| Size reduction | ~75% vs BF16 baseline (`google/gemma-4-26B-A4B-it`) |
| Max context | 32,768 tokens (recommended) |
| GPU memory | ~13 GB VRAM for weights (FP4); use 1–2 A100 / H100 |
| Accuracy recovery | 97–99% on standard benchmarks vs unquantized |

The NVFP4 quantization only applies to linear layers inside transformer blocks.
Vision tower, embeddings, output head, and MoE router are kept in their original precision.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| RHOAI 3.5 | KServe `Managed`, llm-d / `LLMInferenceService` CRD installed |
| GPU nodes | ≥1 A100 (40 GB) or H100 (80 GB) in `project1` namespace |
| Red Hat Connectivity Link | Required for `security.opendatahub.io/enable-auth` JWT auth |
| HuggingFace account | Gemma 4 requires license acceptance at hf.co/google/gemma-4-26B-A4B-it |

Verify prerequisites:
```bash
# LLMInferenceService CRD present
oc get crd llminferenceservices.serving.kserve.io

# Gateway available (created by Red Hat Connectivity Link)
oc get gateway -A | grep -E "openshift-ai-inference|data-science-gateway"

# GPU nodes visible
oc get nodes -l nvidia.com/gpu.present=true
```

---

## Step 1 — HuggingFace token Secret

The model requires a HuggingFace account with the Gemma 4 license accepted.

```bash
# Accept the license at: https://huggingface.co/google/gemma-4-26B-A4B-it
# Generate a read token at: https://huggingface.co/settings/tokens

oc create secret generic hf-token \
  -n project1 \
  --from-literal=HF_TOKEN=<your-hf-token>
```

---

## Step 2 — Deploy LLMInferenceService

```bash
oc apply -f 07-llminferenceservice-gemma4.yaml
```

See [`07-llminferenceservice-gemma4.yaml`](07-llminferenceservice-gemma4.yaml) for the full spec.

**Watch rollout:**
```bash
oc get llminferenceservice gemma4-nvfp4 -n project1 -w
# Wait for Ready=True
```

**Get the inference endpoint:**
```bash
oc get llminferenceservice gemma4-nvfp4 -n project1 \
  -o jsonpath='{.status.url}'
```

---

## Step 3 — RBAC for authenticated inference

Authentication is automatically enabled by the `security.opendatahub.io/enable-auth: "true"`
annotation. Callers must present a valid JWT from a ServiceAccount that has `get` permission
on the `LLMInferenceService` resource.

```bash
# Create inference user ServiceAccount
oc create serviceaccount llm-user -n project1

# Role: get this specific LLMInferenceService
oc apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: llm-inference-viewer
  namespace: project1
rules:
- apiGroups: ["serving.kserve.io"]
  resources: ["llminferenceservices"]
  verbs: ["get"]
  resourceNames: ["gemma4-nvfp4"]
EOF

# Bind the Role to the ServiceAccount
oc create rolebinding llm-user-binding \
  --role=llm-inference-viewer \
  --serviceaccount=project1:llm-user \
  -n project1
```

---

## Step 4 — Verify authentication

```bash
INFER_URL=$(oc get llminferenceservice gemma4-nvfp4 -n project1 \
  -o jsonpath='{.status.url}')
TOKEN=$(oc create token llm-user -n project1 --duration=1h)

# Should return 401 (auth enforced)
curl -sk "${INFER_URL}/v1/models" | head -5

# Should return model list (authenticated)
curl -sk "${INFER_URL}/v1/models" \
  -H "Authorization: Bearer ${TOKEN}" | python3 -m json.tool

# Text inference
curl -sk -X POST "${INFER_URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${TOKEN}" \
  -d '{
    "model": "RedHatAI/gemma-4-26B-A4B-it-NVFP4",
    "messages": [{"role": "user", "content": "What is Red Hat OpenShift AI?"}],
    "max_tokens": 256
  }' | python3 -m json.tool
```

---

## Step 5 — Use as EvalHub evaluation target

The model endpoint can be used directly in EvalHub job submissions as the `model.url`.
The `evalhub-project1-job` ServiceAccount (created by the EvalHub operator) needs
`get` access to the `LLMInferenceService`:

```bash
# Grant eval job pods access to the inference service
oc create rolebinding evalhub-llm-binding \
  --role=llm-inference-viewer \
  --serviceaccount=project1:evalhub-project1-job \
  -n project1
```

**Submit a Petri sycophancy audit via EvalHub using gemma4 as the target:**

```bash
EVALHUB_HOST=$(oc get route evalhub -n project1 -o jsonpath='{.spec.host}')
EVALHUB_TOKEN=$(oc create token evalhub-user-sa -n project1 --duration=1h)
INFER_URL=$(oc get llminferenceservice gemma4-nvfp4 -n project1 -o jsonpath='{.status.url}')

# Store an Anthropic API key for the auditor/judge models (Inspect AI Petri)
oc create secret generic eval-api-keys \
  -n project1 \
  --from-literal=ANTHROPIC_API_KEY=<your-anthropic-key>

curl -sk -X POST \
  -H "Authorization: Bearer ${EVALHUB_TOKEN}" \
  -H "X-Tenant: project1" \
  -H "Content-Type: application/json" \
  "https://${EVALHUB_HOST}/api/v1/evaluations/jobs" \
  -d "{
    \"name\": \"gemma4-petri-sycophancy\",
    \"model\": {
      \"url\": \"${INFER_URL}/v1\",
      \"name\": \"RedHatAI/gemma-4-26B-A4B-it-NVFP4\",
      \"auth\": {\"secret_ref\": \"evalhub-project1-job\"}
    },
    \"benchmarks\": [{
      \"id\": \"inspect/petri-sycophancy\",
      \"provider_id\": \"13014eab-c1b3-47e5-9ad8-ce4e228eee23\",
      \"parameters\": {
        \"auditor_model\": \"claude-sonnet-4-6\",
        \"judge_model\": \"claude-opus-4-7\",
        \"max_samples\": 5
      }
    }]
  }"
```

> **Note:** Replace `13014eab-c1b3-47e5-9ad8-ce4e228eee23` with the current inspect
> provider UUID from `GET /api/v1/evaluations/providers?scope=tenant`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Pod stuck `Pending` | No GPU nodes available or node selector mismatch | Check node labels: `oc get nodes -l nvidia.com/gpu.present=true` |
| `401 Unauthorized` without token | Auth working correctly | Add `-H "Authorization: Bearer ${TOKEN}"` |
| `403 Forbidden` with valid token | SA missing `get` on the `LLMInferenceService` | Apply the Role + RoleBinding from Step 3 |
| Model OOM / crash | Insufficient VRAM | Reduce `--max-model-len` or add a second GPU via `tensor: 2` parallelism |
| HF download failure | Missing token or license not accepted | Check `hf-token` Secret; accept license at `hf.co/google/gemma-4-26B-A4B-it` |
| vLLM warning about multimodal | Text-only workload | Add `--limit-mm-per-prompt '{"image": 0, "audio": 0}'` to VLLM_ARGS |
