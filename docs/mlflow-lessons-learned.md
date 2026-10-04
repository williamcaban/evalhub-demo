---
title: EvalHub and MLflow lessons learned on RHOAI 3.5
type: project-state
workspace: evalhub-demo
visibility: shared
tokens: ~900
tags: [evalhub, mlflow, rhoai-3.5, openshift, troubleshooting]
refs:
  - ./setup.md
  - ./external-model-configuration.md
updated: 2026-09-21
---

# EvalHub and MLflow lessons learned

## What the supported RHOAI 3.5 configuration requires

The official RHOAI 3.5 guide defines MLflow tracking through the EvalHub
deployment configuration, not through extra fields in each job:

- `MLFLOW_TRACKING_URI` points to the reachable MLflow tracking server.
- `MLFLOW_CA_CERT_PATH` points to the service CA for TLS verification.
- `MLFLOW_TOKEN_PATH` points to the projected ServiceAccount token.
- `MLFLOW_WORKSPACE` identifies the tenant's MLflow workspace; in this demo it
  is `hermes-sandbox`.
- A tracked job contains `experiment: { name: ... }`.
- EvalHub and MLflow use namespace-based tenancy. The tenant namespace must be
  registered with `evalhub.trustyai.opendatahub.io/tenant=` and the caller must
  have EvalHub and MLflow RBAC permissions.

The server-side result is verified from the EvalHub job response, especially
`results.mlflow_experiment_url` and `results.mlflow_run_id`. The MLflow UI is
not the only source of truth.

## What we observed in the workshop cluster

The EvalHub deployment had the documented tracking URI, CA path, token path,
and workspace. Direct MLflow requests with the `X-MLFLOW-WORKSPACE: hermes-sandbox`
header worked, proving that the MLflow server and workspace endpoint were
reachable. However, a tracked EvalHub submission failed with:

```
Workspace context is required for this request.
```

The EvalHub server log also reported that workspaces were not enabled and
ignored the workspace. Adding the MLflow workspace namespace label did not
change that behavior.

RHOAI 3.5 release notes document this exact class of defect: an evaluation can
complete and then fail while saving results to MLflow; the documented
workaround is to omit the MLflow experiment. Therefore, this cluster behavior
is a product-version defect, not an external-model authentication problem.

## Operational rules for future runs

1. Validate the MaaS endpoint independently with a short-lived ServiceAccount
   token before debugging EvalHub.
2. Keep the model Secret in the tenant namespace and reference it with
   `model.auth.secret_ref`.
3. Use the documented `experiment.name` field only; do not invent a nested
   workspace override.
4. If tracked submission fails with the workspace-context error, capture the
   EvalHub server log and RHOAI version, then use the no-experiment path only
   for runtime validation. Do not claim that results were stored in MLflow.
5. After upgrading, rerun a one-sample tracked smoke evaluation and verify both
   the EvalHub result fields and the MLflow workspace UI before launching the
   10-sample or nightly suite.

## CLI and benchmark parameter lessons

- For LightEval sample limits, use `parameters.num_examples: N`. The upstream
  LightEval adapter translates this field to LightEval's `--max-samples` flag.
  `parameters.limit` is not the adapter's sample-count control and must not be
  used for standalone LightEval examples.
- `evalhub collections run` submits a collection without an MLflow experiment
  option. Use `evalhub eval run --config <file>` when MLflow tracking is
  required, and include `experiment.name` in the YAML.
- Validate the submitted job, not only the local YAML: check
  `resource.mlflow_experiment_id`, `results.mlflow_run_id`, and the benchmark's
  reported sample count.
- For Inspect/Petri multi-endpoint runs, upgrading the provider image to
  `inspect-ai 0.3.263` is necessary but does not fix an adapter that places
  role-specific `base_url` or `api_key` values in `GenerateConfig`. Until the
  adapter fix is available, use one endpoint/model for target, auditor, and
  judge.
- In this demo's tenant namespace, set `spec.tenancy: single` on the EvalHub
  resource. A `multi` EvalHub in a namespace labeled as an RHOAI tenant is
  rejected by the controller after a restart.

## Direct MlflowClient access (bypassing EvalHub server) — a separate code path that works

The defect above is specifically in **EvalHub-server-mediated** MLflow result commit: a full
EvalHub CR/server receives a tracked job submission and fails to commit the result to MLflow with
the workspace-context error, even with correct config.

A **different** code path — an adapter or client calling MLflow directly via the eval-hub-sdk's own
`MlflowClient` (e.g. an adapter running in `EVALHUB_MODE=local`, with no EvalHub CR/server deployed
or involved at all) — was verified working end-to-end on a separate cluster (wbos, 2026-09-21): a
real run, metrics, params, and an artifact all logged, then independently re-fetched via the MLflow
API to confirm. **Do not read that success as evidence the EvalHub-server defect above is fixed** —
it is a genuinely different code path and was not exercised.

What made the direct path work, once diagnosed:

1. **The tracking URI needs the `/mlflow` path prefix.** Use the MLflow CR's own
   `status.address.url` (`oc get mlflow mlflow -n redhat-ods-applications -o
   jsonpath='{.status.address.url}'`) rather than assuming `https://mlflow.<ns>.svc:8443`. Omitting
   the prefix produces a generic Flask 404 that is easy to misdiagnose as the workspace-context
   defect above — it isn't; it's just a wrong base URL, and re-testing with the correct prefix
   resolves it cleanly.
2. `MLFLOW_WORKSPACE=<namespace>` just needs to name a real k8s namespace for a direct client — no
   extra workspace-provisioning step was needed once the URL was correct.
3. **RBAC**: `02-rbac.yaml`'s existing `mlflow.kubeflow.org` grant now includes both `experiments`
   and `runs` (fixed 2026-09-21) — a direct `MlflowClient` caller needs `runs` too
   (`get/list/create/update`), or `POST /runs/create` 403s right after experiment lookup succeeds.
   This wasn't needed before because EvalHub-server-mediated logging uses EvalHub's own elevated
   service account for the actual MLflow calls, not the tenant RBAC this file grants.

## Official references

- [RHOAI 3.5: Configure MLflow experiment tracking for evaluation jobs](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/evaluating_ai_systems/index)
- [RHOAI 3.5: MLflow workspaces](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/working_with_mlflow/about-mlflow_mlflow)
- [RHOAI 3.5 release notes: MLflow result-storage defect](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html-single/release_notes/index)
