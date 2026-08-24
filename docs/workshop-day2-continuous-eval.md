# Workshop: Continuous AI Evaluation & Drift Monitoring with EvalHub on RHOAI

**Audience**: Platform engineers and MLOps practitioners running AI workloads in production  
**Duration**: ~60 min (30 min presenter demo + 30 min hands-on)  
**Cluster**: RHOAI 3.5 EA2+ with EvalHub, Qwen3-8B-FP8, MLflow  
**Platform**: RHOAI (evals, guardrails, and red-teaming are RHOAI-only — not RHAII)

---

## Framing (5 min)

The question organizations ask after deploying a model is not *"did it pass the benchmark?"* — it is *"is it still performing the way we expect, on today's traffic, in today's context?"*

Most teams handle this with manual spot-checks or silence until something breaks in production. EvalHub on RHOAI lets you replace that with a continuous, automated signal — the same way you have Prometheus for latency, you have EvalHub for model behavior.

**Two scenarios today:**

1. **Continuous evaluation via pipeline** — a nightly Kubernetes CronJob triggers an EvalHub collection run across safety and alignment dimensions. Every morning your team has fresh results in MLflow. No human intervention required.

2. **Drift monitoring** — a comparison script runs after a model update and compares scores against a recorded baseline. If any dimension degrades beyond your delta threshold, the script exits non-zero and blocks promotion.

**What makes EvalHub different from running lm-eval yourself**: EvalHub orchestrates LMEval, Garak, Inspect AI (Petri), and custom evaluators through the same API. You pick a collection, point it at a model endpoint, and it dispatches to the right adapter. You write the "what good looks like" definition once (in the collection YAML). EvalHub enforces it on every run.

---

## Architecture (2 min)

```
                         RHOAI Cluster
 ┌────────────────────────────────────────────────────────────────┐
 │                                                                │
 │  K8s CronJob (nightly)                                         │
 │  └─▶ evalhub collections run nightly-safety-check             │
 │         └─▶ EvalHub Server (API control plane)                 │
 │                └─▶ Collections + pass_criteria thresholds      │
 │                      ├─▶ Inspect adapter (Petri behavioral)   │
 │                      └─▶ Garak adapter (DAN red-team)         │
 │                              └─▶ MLflow (results + history)   │
 │                                                                │
 │  Drift Monitor (./22-drift-monitor.sh)                         │
 │  └─▶ evalhub collections run combined-safety-alignment        │
 │       └─▶ Compare scores vs. baseline MLflow run ID           │
 │            └─▶ exit 0 (no drift) | exit 1 (drift detected)    │
 └────────────────────────────────────────────────────────────────┘
```

**Note**: This Kubernetes CronJob pattern is the recommended approach for scheduling continuous evaluations on RHOAI 3.5. Scheduling capabilities may evolve in future RHOAI releases.

---

## Prerequisites

Complete the main EvalHub setup first (Steps 1–7 in the root README):

```bash
# Verify EvalHub is running
uv run evalhub health

# Verify the Qwen3 judge model is serving
oc get inferenceservice qwen3-8b-fp8 -n project1
# Expected: READY=True

# Verify MLflow is accessible
oc get route mlflow -n redhat-ods-applications
```

Register the Day 2 collections (cluster-admin):

```bash
oc apply -f 21-collections-day2.yaml
```

Add `nightly-safety-check` to the EvalHub CR's `spec.collections` list and re-apply:

```yaml
# In 04-evalhub-cr.yaml, add to spec.collections:
spec:
  collections:
    - nightly-safety-check   # ← add this
    - combined-safety-alignment
    # ... existing entries ...
```

```bash
oc apply -f 04-evalhub-cr.yaml

# Verify the collection is registered
uv run evalhub collections list | grep nightly-safety-check
```

---

## Part 1 — Continuous Evaluation via CronJob (20 min)

### Step 1: Examine the nightly collection

Open `collections/nightly-safety-check.yaml` or run:

```bash
uv run evalhub collections get nightly-safety-check
```

Point out:
- **4 Petri behavioral audits** — the highest-risk dimensions only (alignment-faking and oversight-subversion at weight 2.0; jailbreak and harmful-cooperation at weight 1.5)
- **Garak DAN quick probe** — fast jailbreak canary
- **Each benchmark has `pass_criteria.threshold`** — this is the SLA for model behavior
- **Runtime: ~12–15 min** — fast enough to run nightly without blocking

**Talking point**: *"You're not writing eval logic — you're selecting what to measure. The collection encodes the domain expertise: which dimensions matter, at what weights, with what thresholds. EvalHub enforces it every run."*

### Step 2: Trigger a manual run to demonstrate

```bash
uv run evalhub collections run nightly-safety-check \
  --model-url http://qwen3-8b-fp8-predictor.project1.svc.cluster.local:8080/v1 \
  --model-name qwen3-8b-fp8 \
  --wait
```

Watch the eval pods spin up:

```bash
# In a second terminal
oc get pods -n project1 -w | grep -v "evalhub\|qwen3"
```

**While it runs**, show the collection YAML — walk through a `pass_criteria` block and explain it's a deployment gate, not a metric.

### Step 3: Examine results in MLflow

After the run completes:

```bash
uv run evalhub eval status
# Note the job ID of the completed run

uv run evalhub eval results <job-id>
```

Then open the MLflow UI:

```bash
echo "MLflow: https://$(oc get route mlflow -n redhat-ods-applications -o jsonpath='{.spec.host}')/mlflow"
```

Navigate to experiment `evalhub-continuous-safety`. Show the run, its metrics, and the timestamp.

**Talking point**: *"These are the numbers your team wakes up to. Safety score, alignment score, DAN resistance — every night. Reproducible, timestamped, versioned."*

### Step 4: Set up the CronJob

Create the runner Secret (once per cluster):

```bash
oc create secret generic evalhub-runner-config -n project1 \
  --from-literal=evalhub_url="https://$(oc get route evalhub -n project1 -o jsonpath='{.spec.host}')" \
  --from-literal=model_url="http://qwen3-8b-fp8-predictor.project1.svc.cluster.local:8080/v1" \
  --from-literal=model_name="qwen3-8b-fp8"
```

Deploy the CronJob:

```bash
oc apply -f 21-continuous-eval-cronjob.yaml

oc get cronjob nightly-safety-eval -n project1
# Expected: SCHEDULE=0 2 * * *  SUSPEND=False  ACTIVE=0
```

Trigger a manual run to show it working:

```bash
oc create job \
  --from=cronjob/nightly-safety-eval \
  nightly-safety-eval-manual \
  -n project1

# Watch the job
oc logs -n project1 -l job-name=nightly-safety-eval-manual -f
```

**Talking point**: *"From this point on, every night at 02:00 UTC, your model gets evaluated across the safety dimensions that matter most. If any benchmark fails its threshold, the job exits non-zero — OpenShift marks it failed, it appears in your alerting. No human intervention required unless something breaks."*

---

## Part 2 — Drift Monitoring (20 min)

### Concept (2 min)

Drift detection in EvalHub is **behavioral**, not statistical. You are not measuring feature distribution shift — you are measuring whether the model's evaluated behavior on a curated benchmark has degraded compared to a baseline you recorded.

This is the right frame for GenAI: you care whether the model still answers correctly and safely, not whether your input token distribution has shifted. Statistical drift tools (PSI, KL divergence) tell you your inputs changed. EvalHub tells you whether the model's *outputs* are still within the safety envelope you defined.

### Step 1: Record a baseline

After a known-good deployment (or at the start of the demo using the run from Part 1):

```bash
./22-drift-monitor.sh --record-baseline
```

This runs `combined-safety-alignment` (the full weekly suite), waits for completion, then stores the MLflow job ID in a ConfigMap:

```bash
oc get configmap evalhub-drift-baseline -n project1 -o yaml
# Shows: collection, job_id, recorded_at, model_name
```

**Talking point**: *"This is your behavioral SLA checkpoint. Every future drift check compares against this run. You define what 'good' looks like once — when the model is in a known-good state."*

### Step 2: Simulate a model update (drift scenario)

For the demo, trigger the drift check against a deliberately degraded model variant — or simply run it against the same model to show a clean pass first:

```bash
# Clean pass — same model, no drift
./22-drift-monitor.sh
```

Expected output:

```
============================================================
Benchmark                                Baseline  Current    Delta   Status
------------------------------------------------------------
inspect/petri-alignment-faking              1.200    1.300   +0.100       OK
inspect/petri-oversight-subversion          1.100    1.200   +0.100       OK
inspect/petri-jailbreak                     2.100    2.000   -0.100       OK
inspect/petri-harmful-cooperation           1.800    1.900   +0.100       OK
quick                                       0.800    0.750   -0.050       OK
...
============================================================
No drift detected (all deltas within delta=0.05)
```

### Step 3: Trigger a drift detection

Switch the model URL to a smaller or unguarded variant, or manually pass a run ID from a degraded model. The script compares scores:

```bash
./22-drift-monitor.sh --baseline-run-id <baseline-job-id>
```

Expected output when drift detected:

```
============================================================
Benchmark                                Baseline  Current    Delta   Status
------------------------------------------------------------
inspect/petri-alignment-faking              1.200    4.500   +3.300    DRIFT
inspect/petri-oversight-subversion          1.100    1.200   +0.100       OK
...
============================================================
DRIFT DETECTED — one or more scores degraded beyond delta=0.05
Baseline job: <baseline-id>
Current job:  <current-id>
Action: investigate before promoting model to production.
```

Exit code is 1 — if this runs in CI/CD, the pipeline fails and the model update is blocked.

**Talking point**: *"The pipeline failed. In a CI/CD context, this is your gate: a model update that degrades alignment-faking by more than 5 points does not get promoted to production without a human decision. The evaluation is the signal; the exit code is the enforcer."*

### Step 4: Investigate via MLflow

```bash
echo "MLflow: https://$(oc get route mlflow -n redhat-ods-applications -o jsonpath='{.spec.host}')/mlflow"
```

Open MLflow → experiment `evalhub-continuous-safety`. Both runs are visible. The metric table shows each score over time — you can see exactly which dimension drifted and by how much.

**Talking point**: *"This is what your team uses to triage. The safety score held. The alignment-faking score drifted. You now know where to investigate — not just that something broke, but exactly what dimension and by how much."*

---

## Part 3 — What's Next (5 min)

Everything demonstrated today runs on RHOAI 3.5. The CronJob pattern for continuous evaluation and the drift monitor script are production-ready today.

| Capability | RHOAI 3.5 (now) |
|---|---|
| Continuous eval scheduling | Kubernetes CronJob calling `evalhub collections run` |
| Threshold enforcement | `pass_criteria.threshold` per benchmark in collection YAML — client-side check |
| Drift baseline comparison | `22-drift-monitor.sh` — run manually or in CI/CD |
| Scheduling granularity | Any cron expression via standard K8s CronJob |

**Talking point**: *"Everything you saw today works now on RHOAI 3.5. This is a production pattern, not a demo workaround. EvalHub continues to evolve and scheduling capabilities may improve in future releases."*

---

## Key Messages to Land

1. **Not just lm-eval** — EvalHub orchestrates Petri behavioral audits, Garak red-teaming, LMEval capability benchmarks, and custom evaluators through the same collection API. The audience should leave knowing the platform is not a single-tool wrapper.

2. **Evaluation as SLA** — `pass_criteria.threshold` turns benchmark scores into deployment gates. Same mental model as latency SLOs applied to model behavior.

3. **Works today on RHOAI 3.5** — continuous eval via CronJob and drift detection via script are production patterns, not workarounds. Scheduling capabilities may improve in future releases.

4. **Reproducible by default** — every EvalHub run captures the environment: hardware, software versions, model endpoint, collection version. Any historical run can be reproduced exactly.

---

## Demo Pre-flight Checklist

```bash
# 1. EvalHub is running
uv run evalhub health

# 2. Qwen3 judge model is ready
oc get inferenceservice qwen3-8b-fp8 -n project1
# READY=True

# 3. MLflow is accessible
oc get route mlflow -n redhat-ods-applications

# 4. Day 2 collections are registered
uv run evalhub collections list | grep nightly-safety-check

# 5. evalhub-runner-config Secret exists
oc get secret evalhub-runner-config -n project1

# 6. CronJob is deployed
oc get cronjob nightly-safety-eval -n project1

# 7. A baseline run exists (or record one now)
oc get configmap evalhub-drift-baseline -n project1 2>/dev/null \
  || echo "No baseline yet — run: ./22-drift-monitor.sh --record-baseline"
```

---

## Cleanup (after demo)

```bash
# Remove CronJob and Secret
oc delete cronjob nightly-safety-eval -n project1
oc delete secret evalhub-runner-config -n project1
oc delete configmap evalhub-drift-baseline -n project1 --ignore-not-found

# Remove Day 2 collections (cluster-admin)
oc delete -f 21-collections-day2.yaml --ignore-not-found
```
