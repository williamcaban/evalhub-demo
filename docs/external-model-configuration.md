# External model configuration

This demo can use the cluster's MaaS external models instead of a locally
deployed GPU model. The current workshop cluster exposes:

| Model | OpenAI-compatible base URL |
|---|---|
| `gpt-oss-120b` | `https://maas.apps.cluster-2n2gw.dyn.redhatworkshops.io/external-models/gpt-oss-120b/v1` |
| `qwen3-235b` | `https://maas.apps.cluster-2n2gw.dyn.redhatworkshops.io/external-models/qwen3-235b/v1` |

The model-specific `/external-models/<model>/v1` path is important. The plain
`/v1` gateway path requires an `X-Gateway-Model-Name` header, which EvalHub's
standard model configuration does not currently add.

## Authentication

The MaaS gateway accepts an OpenShift service-account token. Create a
namespace-local Secret for EvalHub jobs; do not commit the token or print it in
logs:

```bash
TOKEN_FILE="$(mktemp /tmp/maas-model-token.XXXXXX)"
trap 'rm -f "$TOKEN_FILE"' EXIT
oc create token evalhub-user-sa -n project1 --duration=720h > "$TOKEN_FILE"
oc create secret generic maas-model-token -n project1 \
  --from-file=api-key="$TOKEN_FILE" \
  --from-file=OPENAI_API_KEY="$TOKEN_FILE" \
  --dry-run=client -o yaml | oc apply -f -
```

The `720h` duration is a convenience for a lab cluster, not a production
credential policy. Rotate the Secret before the token expires. The service
account must be authorized by the MaaS gateway's model-access policy.

## EvalHub job configuration

Set the model URL, model name, and Secret reference in every job config:

```yaml
model:
  url: https://maas.apps.cluster-2n2gw.dyn.redhatworkshops.io/external-models/gpt-oss-120b/v1
  name: gpt-oss-120b
  auth:
    secret_ref: maas-model-token
```

To switch to Qwen, change both `url` and `name`:

```yaml
model:
  url: https://maas.apps.cluster-2n2gw.dyn.redhatworkshops.io/external-models/qwen3-235b/v1
  name: qwen3-235b
  auth:
    secret_ref: maas-model-token
```

The runnable files under `evals/` currently default to `gpt-oss-120b`.

## Validate the gateway before submitting an evaluation

Use a short-lived service-account token and a minimal request. The token value
should never be echoed:

```bash
TOKEN="$(oc create token evalhub-user-sa -n project1 --duration=10m)"
curl -skS "https://maas.apps.cluster-2n2gw.dyn.redhatworkshops.io/external-models/gpt-oss-120b/v1/chat/completions" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-oss-120b","messages":[{"role":"user","content":"Reply with OK only."}],"max_tokens":4}'
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
- MLflow workspace isolation is enabled, but RHOAI 3.5 has a documented
  EvalHub defect where jobs with an `experiment` block can fail while saving
  results to MLflow. This is not fixed by changing the model URL or by adding
  `mlflow_workspace` to the job payload. Until the cluster is upgraded to a
  build containing the fix, use the no-experiment smoke path for execution
  validation and treat MLflow-backed runs as unavailable.
- A tracked job must contain only the documented experiment shape:
  `experiment: { name: <experiment-name> }`. The tenant/workspace comes from
  the EvalHub deployment (`MLFLOW_WORKSPACE=project1`) and MLflow RBAC; do not
  add a second workspace field to the job request.
- The MaaS gateway's provider credential Secret is not the same thing as the
  client credential used by EvalHub. The provider Secret in `external-models`
  may not be accepted as a caller API key; the OpenShift service-account token
  pattern above is the working lab configuration.
