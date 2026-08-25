# EvalHub — RHOAI 3.5 Demo

End-to-end EvalHub deployment on RHOAI 3.5 EA2+ with eight evaluation providers, MLflow
experiment tracking, research-calibrated evaluation collections, continuous safety evaluation
via CronJob, drift monitoring, Prometheus alerting, and a Perses dashboard in the OpenShift
Console. Tested on a single NVIDIA L4 GPU with Qwen3-8B-FP8 as the judge model.

**Product docs**: [EvalHub](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html-single/evaluating_ai_systems/index) · [MLflow on RHOAI](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html-single/working_with_mlflow/index)

![EvalHub Continuous Evaluation & Drift Monitoring dashboard](docs/assets/evalhub-continuous-eval-drift-3_5ea2.png)

---

## What's included

| Area | Files | Description |
|---|---|---|
| Core deployment | `01–06`, `deploy.sh` | Namespace, RBAC, MLflow, EvalHub CR, network policy, model serving |
| Evaluation providers | `10–16-*-provider.yaml` | Inspect AI, Garak, RULER, RAGAS, GuideLLM (community ConfigMaps) |
| Evaluation collections | `20–21-collections-*.yaml` | 7 custom collections with research-calibrated thresholds |
| Individual eval configs | `evals/` | Per-benchmark YAML configs for all supported providers |
| Day 2 — continuous eval | `21-continuous-eval-cronjob.yaml` | Nightly K8s CronJob with threshold gate |
| Day 2 — drift monitoring | `22-drift-monitor.sh` | Baseline record + behavioral drift comparison |
| Day 2 — Pushgateway | `24-pushgateway.yaml` | Per-benchmark metrics for Prometheus alerting |
| Alerting | `23-alerting.yaml`, `23-alerting-cluster.yaml` | PrometheusRules for user-workload and cluster Prometheus |
| Perses dashboard | `25-perses-*.yaml`, `26-*` | COO install + dashboard for OpenShift Console |
| Documentation | `docs/` | Step-by-step guides, monitoring setup, workshop |

---

## Architecture

```
redhat-ods-applications
  MLflow Server ←── SA token auth (TLS: svc:8443)

project1
  EvalHub Server  ── route: evalhub-project1.apps.<cluster>
  ├── Providers: lm-evaluation-harness, lighteval, ibm-clear (operator)
  │             inspect, garak, guidellm, ruler, ragas (ConfigMap)
  ├── Collections: combined-safety-alignment, nightly-safety-check, ...
  ├── Qwen3-8B-FP8 ── InferenceService, 1× L4 GPU
  ├── Prometheus Pushgateway ── per-benchmark scores → alerting + dashboard
  └── Evaluation Jobs (K8s Jobs, one pod per benchmark)

openshift-operators
  Perses ── Observe > Dashboards (Perses) in OCP Console
  Cluster Observability Operator

openshift-monitoring / openshift-user-workload-monitoring
  PrometheusRules ── EvalHubBenchmarkThresholdBreach, EvalHubServerDown, ...
```

---

## Quick start

```bash
# 1. Cluster-admin setup (DSC patches + provider/collection registration)
#    See docs/setup.md — Step 1

# 2. Deploy EvalHub + MLflow
./deploy.sh

# 3. Configure the CLI and run your first eval
uv sync
uv run evalhub config set base_url "https://$(oc get route evalhub -n project1 -o jsonpath='{.spec.host}')"
uv run evalhub config set token "$(oc create token evalhub-user-sa -n project1 --duration=8h)"
uv run evalhub config set tenant "project1"
uv run evalhub health
uv run evalhub eval run --config evals/arc-easy.yaml --wait
```

---

## Documentation

| Guide | What it covers |
|---|---|
| [`docs/setup.md`](docs/setup.md) | Full step-by-step setup: prerequisites, DSC patches, providers, MLflow, EvalHub, model serving, running evals and collections, known issues, troubleshooting, cleanup |
| [`docs/workshop-day2-continuous-eval.md`](docs/workshop-day2-continuous-eval.md) | Day 2 operations workshop: continuous evaluation via CronJob, drift detection with thresholds — 60-min demo + hands-on guide |
| [`docs/monitoring-setup.md`](docs/monitoring-setup.md) | Prometheus alerting setup (user-workload + cluster rules), Pushgateway, Perses dashboard installation via COO |

---

## Provider status

| Provider | Benchmarks | Status |
|---|---|---|
| `lm_evaluation_harness` | 188 | ✅ |
| `lighteval` | 28 | ✅ generative; logprob pending PR #115 |
| `ibm-clear` | 1 | ✅ |
| `inspect` | 20 | ⚠️ Petri ✅; inspect-evals blocked by G9 (Responses API) |
| `garak` | 9 | ✅ |
| `guidellm` | 7 | ✅ |
| `ruler` | 13 | ❌ image not yet published |
| `ragas` | 2 | ❌ InstructorLLM API mismatch |

See [`docs/setup.md`](docs/setup.md#known-issues) for details on each issue.

---

## License

Apache 2.0
