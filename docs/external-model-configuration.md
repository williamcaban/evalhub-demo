# External model configuration

This demo can use the cluster's MaaS external models instead of a locally
deployed GPU model. The current workshop cluster exposes:

| Model | OpenAI-compatible base URL |
|---|---|
| `qwen38-27b` | `https://maas.apps.ocp.cloud.rhai-tmm.dev/prelude-maas/qwen38-27b/v1` |
| `qwen38-27b` | `https://maas.apps.ocp.cloud.rhai-tmm.dev/prelude-maas/qwen38-27b/v1` |

The model-specific `/external-models/<model>/v1` path is important. The plain
`/v1` gateway path requires an `X-Gateway-Model-Name` header, which EvalHub's
standard model configuration does not currently add.

## Authentication

The MaaS gateway does NOT accept OpenShift service-account tokens (verified on
the TMM gateway: SA-token requests return 401). It requires an API key passed
as `Authorization: Bearer`. Create a namespace-local Secret for EvalHub jobs
from the `MAAS_API_KEY` environment variable; never commit the key or print it
in logs:

```bash
oc create secret generic maas-model-token -n project1 \
  --from-literal=api-key="$MAAS_API_KEY" \
  --from-literal=OPENAI_API_KEY="$MAAS_API_KEY" \
  --dry-run=client -o yaml | oc apply -f -
```

Set an expiry reminder on the key per the gateway's credential policy and
rotate the Secret before it expires. The key must be authorized by the MaaS
gateway's model-access policy.

## EvalHub job configuration

Set the model URL, model name, and Secret reference in every job config:

```yaml
model:
  url: https://maas.apps.ocp.cloud.rhai-tmm.dev/prelude-maas/qwen38-27b/v1
  name: qwen38-27b
  auth:
    secret_ref: maas-model-token
```

To switch to Qwen, change both `url` and `name`:

```yaml
model:
  url: https://maas.apps.ocp.cloud.rhai-tmm.dev/prelude-maas/qwen38-27b/v1
  name: qwen38-27b
  auth:
    secret_ref: maas-model-token
```

The runnable files under `evals/` currently default to `qwen38-27b`.

## Validate the gateway before submitting an evaluation

Use a minimal request with the API key from the Secret. The key value should
never be echoed:

```bash
KEY="$(oc get secret maas-model-token -n project1 -o jsonpath='{.data.api-key}' | base64 -d)"
curl -skS "https://maas.apps.ocp.cloud.rhai-tmm.dev/prelude-maas/qwen38-27b/v1/chat/completions" \
  -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen38-27b","messages":[{"role":"user","content":"Reply with OK only."}],"max_tokens":4}'
```

Repeat with the Qwen URL and model name when switching models.

## Submit a config-based evaluation

```bash
EVALHUB_ROUTE="$(oc get route evalhub -n project1 -o jsonpath='{.spec.host}')"
TOKEN="$(oc create token evalhub-user-sa -n project1 --duration=1h)"
evalhub config set base_url "https://${EVALHUB_ROUTE}"
evalhub config set token "$TOKEN"
evalhub config set tenant project1
evalhub config set insecure true
evalhub eval run --config evals/gsm8k.yaml --wait
```

The config-based path is preferred because it carries `auth.secret_ref` with
the job. The current `evalhub collections run` CLI does not expose a model-auth
secret option; use a config-based job or submit the API JSON directly when an
external model requires authentication.

## Operational notes

- The demo cluster has no NVIDIA GPU capacity, so do not apply the local
  `06-qwen3-judge.yaml` manifest unless GPU nodes are added.
- Keep the EvalHub model Secret in `project1`; the EvalHub controller resolves
  `model.auth.secret_ref` there when creating evaluation jobs.
- MLflow workspace isolation is enabled. A RHOAI 3.5 GA defect (RHOAIENG-66859)
  could make jobs with an `experiment` block fail while saving results to
  MLflow; it was **resolved in RHOAI 3.5 EA2** (verified on wbos,
  rhods-operator 3.5.1 — evals now commit to MLflow). Keep the documented
  experiment shape (`experiment: { name: <name> }`); the no-experiment smoke
  path remains for completion-only validation.
- A tracked job must contain only the documented experiment shape:
  `experiment: { name: <experiment-name> }`. The tenant/workspace comes from
  the EvalHub deployment (`MLFLOW_WORKSPACE=project1`) and MLflow RBAC; do not
  add a second workspace field to the job request.
- The MaaS gateway's provider credential Secret is not the same thing as the
  client credential used by EvalHub. The provider Secret in `external-models`
  may not be accepted as a caller API key; the `MAAS_API_KEY` Bearer pattern
  above is the working lab configuration (OpenShift SA tokens are rejected
  with 401 by the gateway — verified on TMM).
