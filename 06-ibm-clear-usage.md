# IBM CLEAR Provider — EvalHub Usage Reference

Provider ID: **`ibm-clear`**  
Benchmark ID: **`agentic-evaluation`**  
Image: `quay.io/evalhub/community-ibm-clear:v0.2.0`  
Blog reference: [From Traces to Insights: Agentic Evaluation with IBM CLEAR and Red Hat EvalHub on OpenShift AI](https://docs.google.com/document/d/1thbJqmgJQXwdA-Y4hnEc2go1i-5jaoewL8oyjdEKU1o)

---

## What CLEAR does

IBM CLEAR (Comprehensive LLM Error Analysis and Reporting) evaluates agentic AI systems
from MLflow traces. For every agent interaction it:

1. Scores reasoning quality, relevance, and tool usage using a judge LLM  
2. Generates a short critique  
3. Clusters critiques into a ranked list of recurring failure patterns — each with
   frequency, severity, and attribution to the specific agent step where it occurs

Output: per-node scores + ranked issue catalog + interactive HTML report — all stored
back into MLflow as artifacts.

## Supported combinations

| `agent_framework` | `observability_framework` | Supported |
|---|---|---|
| `langgraph` | `mlflow` | ✓ |
| `langgraph` | `langfuse` | ✓ |
| `crewai` | `langfuse` | ✓ |
| `crewai` | `mlflow` | ✗ |

---

## Prerequisites

### 1 — Store your judge LLM API key

```bash
oc create secret generic judge-llm-api-key \
  -n project1 \
  --from-literal=api-key=<YOUR_API_KEY> \
  --from-literal=OPENAI_API_KEY=<YOUR_API_KEY>
```

Both keys are required:
- `api-key` → EvalHub credential resolver (mounted at `/var/run/secrets/model/api-key`)
- `OPENAI_API_KEY` → LiteLLM uses this when routing calls through the OpenAI-compatible interface

For non-OpenAI providers, also add the provider-specific key:
```bash
# Google Gemini
--from-literal=GOOGLE_API_KEY=<key>
# Anthropic Claude
--from-literal=ANTHROPIC_API_KEY=<key>
```

### 2 — Upload agent traces to MLflow

Traces must follow the CLEAR MLflow span hierarchy:

```
AGENT (root)
  CHAIN (LangGraph/orchestration)
    CHAT_MODEL (research_node)     ← scored by CLEAR
      TOOL (tool_name)             ← scored in SPARC mode
    CHAT_MODEL (analysis_node)     ← scored by CLEAR
```

A span is scored as an LLM call if **any** of these are true:
- `span_type` is `CHAT_MODEL`, `MODEL`, or `GENERATION`
- Has a `gen_ai.operation.name` attribute
- Has `choices` in its `outputs` dict

---

## Evaluation Modes

### Standard mode (`separate_tools: false`)
Each agent step is scored **holistically** — reasoning text and tool calls in one step
are scored together as a single interaction. Best for: overall step quality score.

### SPARC mode (`separate_tools: true`)
Tool calls are **split into separate rows** before scoring:
- Reasoning rows: scored for reasoning quality, relevance, coherence
- Tool-call rows: scored for argument correctness, tool selection appropriateness

Best for: debugging whether issues are in reasoning vs tool usage.

---

## Job Submission

### Environment setup

```bash
EVALHUB_HOST=$(oc get route evalhub -n project1 -o jsonpath='{.spec.host}')
EVALHUB_URL="https://${EVALHUB_HOST}"
TOKEN=$(oc create token evalhub-user-sa -n project1 --duration=1h)
```

### Submit — Standard mode

```bash
curl -sk -X POST "${EVALHUB_URL}/api/v1/evaluations/jobs" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Tenant: project1" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "clear-standard-eval",
    "model": {
      "url": "<YOUR_JUDGE_MODEL_ENDPOINT>",
      "name": "<YOUR_MODEL_NAME>",
      "auth": {"secret_ref": "judge-llm-api-key"}
    },
    "experiment_name": "clear-eval-results",
    "benchmarks": [{
      "id": "agentic-evaluation",
      "provider_id": "ibm-clear",
      "parameters": {
        "mlflow_traces_experiment_name": "research-agent-traces",
        "mlflow_experiment_name": "clear-eval-results",
        "mlflow_workspace": "project1",
        "eval_model_name": "<YOUR_MODEL_NAME>",
        "provider": "openai",
        "inference_backend": "litellm",
        "separate_tools": false,
        "agent_framework": "langgraph",
        "observability_framework": "mlflow"
      }
    }]
  }'
```

### Submit — SPARC mode (tool call analysis)

Same as above but with `"separate_tools": true` and a different experiment name:

```bash
curl -sk -X POST "${EVALHUB_URL}/api/v1/evaluations/jobs" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Tenant: project1" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "clear-sparc-eval",
    "model": {
      "url": "<YOUR_JUDGE_MODEL_ENDPOINT>",
      "name": "<YOUR_MODEL_NAME>",
      "auth": {"secret_ref": "judge-llm-api-key"}
    },
    "experiment_name": "clear-eval-sparc-results",
    "benchmarks": [{
      "id": "agentic-evaluation",
      "provider_id": "ibm-clear",
      "parameters": {
        "mlflow_traces_experiment_name": "research-agent-traces",
        "mlflow_experiment_name": "clear-eval-sparc-results",
        "mlflow_workspace": "project1",
        "eval_model_name": "<YOUR_MODEL_NAME>",
        "provider": "openai",
        "inference_backend": "litellm",
        "separate_tools": true,
        "agent_framework": "langgraph",
        "observability_framework": "mlflow"
      }
    }]
  }'
```

### Example: Google Gemini 2.5 Flash as judge

```json
{
  "model": {
    "url": "https://generativelanguage.googleapis.com/v1beta/openai",
    "name": "gemini-2.5-flash",
    "auth": {"secret_ref": "judge-llm-api-key"}
  }
}
```

### Example: self-hosted vLLM

```json
{
  "model": {
    "url": "http://vllm-server.my-namespace.svc:8000/v1",
    "name": "meta-llama/Llama-3.1-8B-Instruct",
    "auth": {"secret_ref": "judge-llm-api-key"}
  }
}
```

---

## Parameter Reference

| Parameter | Description |
|---|---|
| `model.url` | OpenAI-compatible `/v1` base URL for the judge LLM |
| `model.name` | Model identifier passed in API calls |
| `model.auth.secret_ref` | Name of the OpenShift Secret containing the API key |
| `experiment_name` | Top-level MLflow experiment for saving results |
| `mlflow_traces_experiment_name` | MLflow experiment to **fetch** input traces from |
| `mlflow_experiment_name` | MLflow experiment to **save** evaluation results to |
| `mlflow_workspace` | MLflow workspace (= OpenShift namespace for RBAC) |
| `eval_model_name` | Bare model name — CLEAR builds `{provider}/{eval_model_name}` for LiteLLM |
| `provider` | LiteLLM provider prefix (`openai`, `anthropic`, `gemini`) |
| `inference_backend` | `litellm` (default/recommended), `langchain`, or `endpoint` (legacy) |
| `separate_tools` | `false` = standard mode; `true` = SPARC (tool-call analysis) |
| `agent_framework` | `langgraph` or `crewai` — controls how CLEAR parses trace spans |
| `observability_framework` | `mlflow` or `langfuse` — sets trace source format |
| `max_examples_to_analyze` | Optional: cap trace count for fast iteration (e.g., `5`) |

---

## Monitor and retrieve results

```bash
# Get job ID from submission response, then:
JOB_ID="eval-a1b2c3d4"

curl -sk "${EVALHUB_URL}/api/v1/evaluations/jobs/${JOB_ID}" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Tenant: project1" \
  | python3 -c "import json,sys; j=json.load(sys.stdin); print('State:', j['status']['state'])"
```

---

## MLflow Artifacts (per run)

| Artifact | Description |
|---|---|
| `clear_results.json` | Full structured results: per-interaction scores + complete issue catalog |
| `metrics_summary.json` | Aggregated metrics in compact JSON |
| `clear_results.html` | Interactive HTML dashboard with drill-downs |
| `clear_results.dashboard_data.json` | Data backing the HTML dashboard |

### Key metrics logged

| Metric | Meaning |
|---|---|
| `average_score` | Average quality score across all interactions (0.0–1.0, higher is better) |
| `total_interactions` | Total LLM interactions analyzed |
| `total_issues` | Unique failure patterns discovered |
| `interactions_with_issues` | Count of interactions flagged with at least one issue |
| `agent.<node_name>.avg_score` | Per-agent-step average score |

---

## Tips

- **Start standard, then try SPARC.** If tool-related issues appear in standard results, SPARC reveals whether it's tool selection or argument quality.
- **Pin your judge model version** (e.g., `gemini-2.5-flash`, not `latest`) so score changes reflect agent behavior, not model drift.
- **Limit traces during iteration**: set `"max_examples_to_analyze": 5` to keep runs under a minute.
- **Use the same `mlflow_workspace`** as where EvalHub is running so the SA token has access.
