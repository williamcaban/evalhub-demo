# Deploying a Model from the RHOAI Model Catalog

Deploy a model directly from the RHOAI 3.5 AI Hub without a HuggingFace token — models
are served in **OCI ModelCar format** from Red Hat container registries, pulled automatically
using the cluster's global pull secret.

**Documentation reference**  
https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html-single/working_with_the_model_catalog/index

---

## Key difference vs HuggingFace deployment

| | Model Catalog (this guide) | HuggingFace direct |
|---|---|---|
| Model source | `oci://registry.redhat.io/rhai/modelcar-*` | `hf://<org>/<model>` |
| Auth required | Cluster global pull secret (pre-configured) | `HF_TOKEN` Secret per namespace |
| Model format | OCI ModelCar (container image) | HF repository (downloaded at startup) |
| Image pull time | 1–20 min depending on size | Similar (model size) |
| Validated by Red Hat | ✓ (validated models) | Depends |

---

## Models available in this cluster's catalog

Navigate to **AI hub → Models → Catalog**:
`https://rh-ai.apps.ocp.<cluster>.opentlc.com/ai-hub/models/catalog`

| Category | Model | Notes |
|---|---|---|
| Red Hat AI - validated | Apertus-8B-Instruct-2509-FP8-dynamic | FP8, 8B |
| Red Hat AI - validated | DeepSeek-R1-0528-quantized.w4a16 | INT4, large |
| Red Hat AI - validated | Devstral-Small-2-24B-Instruct-2512 | 24B, large |
| Red Hat AI - validated | gemma-2-9b-it | 9B, BF16 |
| Red Hat AI | granite-3.1-8b-lab-v1 | 8B, fully supported |
| Red Hat AI | granite-8b-code-instruct | 8B, fully supported |
| Other | gemma-4-26B-A4B-it | 26B MoE — **large image, needs A100 80GB+** |
| Other | **Qwen3-8B-FP8-dynamic** | **✓ Recommended for L4/A10G (23–24 GB GPU)** |
| Other | all-MiniLM-L6-v2 | Embedding model |
| Other | bge-m3 | Embedding model |

---

## GPU sizing guide

| GPU | VRAM | Recommended models |
|---|---|---|
| NVIDIA L4 (`g6.8xlarge`) | 23 GB | Qwen3-8B-FP8, Granite-8B, Apertus-8B-FP8 |
| NVIDIA A10G | 24 GB | Same as L4 |
| NVIDIA A100-40GB | 40 GB | gemma-2-9b-it, DeepSeek-R1 (quantized) |
| NVIDIA A100-80GB / H100 | 80 GB | gemma-4-26B-A4B-it, larger models |

> ⚠️ **Lesson learned:** `gemma-4-26B-A4B-it` OCI image is very large (>20 GB) and the
> full BF16 model (~52 GB weights) does not fit in a single L4/A10G. Use the
> **NVFP4 variant** or a smaller model on those GPUs.

---

## Practical example: Qwen3-8B-FP8-dynamic on L4

This is the model currently running in the cluster (`my-first-model/qwen3-8b-fp8`).

| Property | Value |
|---|---|
| OCI URI | `oci://registry.redhat.io/rhelai1/modelcar-qwen3-8b-fp8-dynamic:1.5` |
| Image size | **9.5 GB** (pulled in ~1m38s) |
| Weight footprint | **8.8 GiB** loaded into GPU |
| GPU VRAM (L4 23GB) | ~10 GB weights + 13 GB KV cache at max-model-len=32768 |
| Max context | 32,768 tokens |
| Quantization | FP8 dynamic |

**vLLM startup sequence observed:**
```
Loading model weights:     8.8 GiB in 1.59 s
torch.compile (Dynamo):    ~11 s
Graph compilation:         ~40 s
KV cache allocation:       ...
Server ready
```

---

## 5-Step Deployment Wizard (Dashboard)

### Step 1 — Preconfigure deployment

1. **AI hub → Models → Catalog**
2. Find the model (e.g., **Qwen3-8B-FP8-dynamic** under "Other models")
3. Click the model name → **Deploy model**
4. **Project**: select `project1` (or create a new data science project)
5. Click **Next**

### Step 2 — Model details *(read-only)*

Fields are auto-imported from the catalog:

| Field | Value (Qwen3-8B example) |
|---|---|
| Model location | URI |
| URI | `oci://registry.redhat.io/rhelai1/modelcar-qwen3-8b-fp8-dynamic:1.5` |
| Model type | Generative AI model (Example, LLM) |

Click **Next**.

### Step 3 — Model deployment

| Field | L4 GPU recommended value |
|---|---|
| Model deployment name | `qwen3-8b-fp8` (auto-filled) |
| Hardware profile | **gpu-profile** (1 GPU, 8–24 GiB RAM) |
| Deployment resource | Auto-select OR vLLM ServingRuntime |
| Custom runtime arguments | `--max-model-len=32768 --gpu-memory-utilization=0.92` |
| Replicas | 1 |

> **Note:** `default-profile` is CPU-only. Select `gpu-profile` for any LLM.

Click **Next**.

### Step 4 — Advanced settings

| Option | Value |
|---|---|
| Add as AI asset endpoint | ✓ (enables Gen AI studio playground) |
| Use case | `text-generation, reasoning` |
| Require token authentication | ✓ (recommended) |
| Service account name | `llm-user` |

Click **Next**.

### Step 5 — Review → Deploy model

Verify settings and click **Deploy model**.

---

## CLI equivalent (what the wizard does behind the scenes)

The wizard creates two resources. For Qwen3-8B-FP8:

```bash
# 1. ServingRuntime (vLLM container + startup args)
oc apply -f - <<EOF
apiVersion: serving.kserve.io/v1alpha1
kind: ServingRuntime
metadata:
  name: qwen3-8b-fp8
  namespace: my-first-model
  labels:
    opendatahub.io/dashboard: "true"
  annotations:
    opendatahub.io/apiProtocol: REST
    opendatahub.io/template-name: vllm-cuda-runtime-template
spec:
  containers:
  - name: kserve-container
    image: registry.redhat.io/rhaiis/vllm-cuda-rhel9:3.2.4
    command: [python, -m, vllm.entrypoints.openai.api_server]
    args: [--port=8080, --model=/mnt/models, "--served-model-name={{.Name}}"]
    env:
    - name: HF_HOME
      value: /tmp/hf_home
    ports:
    - containerPort: 8080
  multiModel: false
  supportedModelFormats:
  - autoSelect: true
    name: vLLM
EOF

# 2. InferenceService (model config + resources)
oc apply -f - <<EOF
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: qwen3-8b-fp8
  namespace: my-first-model
  labels:
    opendatahub.io/dashboard: "true"
    opendatahub.io/genai-asset: "true"
  annotations:
    modelFormat: vLLM
    opendatahub.io/hardware-profile-name: gpu-profile
    opendatahub.io/model-type: generative
    security.opendatahub.io/enable-auth: "false"
    serving.kserve.io/deploymentMode: Standard
spec:
  predictor:
    minReplicas: 1
    maxReplicas: 1
    model:
      args:
      - --dtype=auto
      - --max-model-len=32768
      - --gpu-memory-utilization=0.92
      - --enable-auto-tool-choice
      - --tool-call-parser=hermes
      modelFormat:
        name: vLLM
      runtime: qwen3-8b-fp8
      storageUri: oci://registry.redhat.io/rhelai1/modelcar-qwen3-8b-fp8-dynamic:1.5
      resources:
        requests: {cpu: "2", memory: 8Gi, "nvidia.com/gpu": "1"}
        limits:   {cpu: "8", memory: 24Gi, "nvidia.com/gpu": "1"}
EOF
```

---

## Monitor deployment

```bash
# Watch pod status
oc get pods -n my-first-model -w

# Check InferenceService ready
oc get inferenceservice qwen3-8b-fp8 -n my-first-model

# Watch vLLM startup logs
oc logs -n my-first-model \
  -l serving.kserve.io/inferenceservice=qwen3-8b-fp8 \
  -c kserve-container -f
```

**Expected timeline:**
1. `Init:0/1` — pulling OCI ModelCar (~1m38s for 9.5 GB image)
2. `Running 2/3` — init complete, vLLM loading weights (~2s) + compiling (~50s)
3. `Running 3/3` — KV cache allocated, server ready
4. ISVC `Ready: True`

---

## Test the inference endpoint

```bash
INFER_URL=$(oc get inferenceservice qwen3-8b-fp8 -n my-first-model \
  -o jsonpath='{.status.address.url}')

# List models (no auth on this deployment)
curl -s "${INFER_URL}/v1/models" | python3 -m json.tool

# Chat completion
curl -s -X POST "${INFER_URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-8b-fp8",
    "messages": [{"role": "user", "content": "What is Red Hat OpenShift AI?"}],
    "max_tokens": 256
  }' | python3 -m json.tool
```

---

## Use as EvalHub target

```bash
EVALHUB_HOST=$(oc get route evalhub -n project1 -o jsonpath='{.spec.host}')
EVALHUB_TOKEN=$(oc create token evalhub-user-sa -n project1 --duration=1h)
INFER_URL=$(oc get inferenceservice qwen3-8b-fp8 -n my-first-model \
  -o jsonpath='{.status.address.url}')

curl -sk -X POST \
  -H "Authorization: Bearer ${EVALHUB_TOKEN}" \
  -H "X-Tenant: project1" \
  -H "Content-Type: application/json" \
  "https://${EVALHUB_HOST}/api/v1/evaluations/jobs" \
  -d "{
    \"name\": \"qwen3-lmeval-arc\",
    \"model\": {
      \"url\": \"${INFER_URL}/v1\",
      \"name\": \"qwen3-8b-fp8\"
    },
    \"benchmarks\": [{
      \"id\": \"arc_easy\",
      \"provider_id\": \"lm-evaluation-harness\"
    }]
  }"
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Pod stuck `Terminating` during image pull | containerd download can't be cancelled cleanly | `oc delete pod <name> -n <ns> --force --grace-period=0` |
| Qwen3 pod `Pending: Insufficient nvidia.com/gpu` | Previous pod hasn't released GPU | Force-delete terminating pod first |
| `gemma-4-26B-A4B-it` image pull never completes | Image is very large (>20 GB) | Switch to a smaller model (Qwen3-8B or similar) |
| vLLM OOM at startup | Model too large for GPU | Reduce `--max-model-len` or use a quantized variant |
| Project not in dropdown | Namespace not recognized by RHOAI | Label it: `oc label ns <name> opendatahub.io/dashboard=true` |
| `registry.redhat.io` pull fails | Cluster pull secret missing credentials | Check: `oc get secret pull-secret -n openshift-config` |
