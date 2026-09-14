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
updated: 2026-09-14
---

# EvalHub and MLflow lessons learned

## What the supported RHOAI 3.5 configuration requires

The official RHOAI 3.5 guide defines MLflow tracking through the EvalHub
deployment configuration, not through extra fields in each job:

- `MLFLOW_TRACKING_URI` points to the reachable MLflow tracking server.
- `MLFLOW_CA_CERT_PATH` points to the service CA for TLS verification.
- `MLFLOW_TOKEN_PATH` points to the projected ServiceAccount token.
- `MLFLOW_WORKSPACE` identifies the tenant's MLflow workspace; in this demo it
  is `project1`.
- A tracked job contains `experiment: { name: ... }`.
- EvalHub and MLflow use namespace-based tenancy. The tenant namespace must be
  registered with `evalhub.trustyai.opendatahub.io/tenant=` and the caller must
  have EvalHub and MLflow RBAC permissions.

The server-side result is verified from the EvalHub job response, especially
`results.mlflow_experiment_url` and `results.mlflow_run_id`. The MLflow UI is
not the only source of truth.

## What we observed in the workshop cluster

The EvalHub deployment had the documented tracking URI, CA path, token path,
and workspace. Direct MLflow requests with the `X-MLFLOW-WORKSPACE: project1`
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

## Official references

- [RHOAI 3.5: Configure MLflow experiment tracking for evaluation jobs](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/evaluating_ai_systems/index)
- [RHOAI 3.5: MLflow workspaces](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/working_with_mlflow/about-mlflow_mlflow)
- [RHOAI 3.5 release notes: MLflow result-storage defect](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html-single/release_notes/index)
