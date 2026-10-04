# Garak red-team report workflow

This workflow turns Garak's native JSONL output into a self-contained HTML report. It can render one scan or compare a baseline scan with a scan of the same target behind a guardrail. The report generator is separate from the EvalHub service and has no third-party Python dependencies.

## Files

| File | Purpose |
|---|---|
| `scripts/garak_report_generator.py` | Render and compare Garak report JSONL files from a local shell or pipeline. |
| `notebooks/garak_red_team_report.ipynb` | Interactive analyst workflow over downloaded artifacts. |
| `examples/garak-report-metadata.example.json` | Starter metadata for architecture, scan context, known issues, and reviewed recommendations. |

The generator needs Garak `.report.jsonl` files. `.hitlog.jsonl` files are optional and are included in the report only when both a hitlog is passed and `--include-examples` is set.

## Run the EvalHub scans

Use the same Garak benchmark and parameters for both configurations. The existing [`evals/garak-owasp.yaml`](../evals/garak-owasp.yaml) is a single-benchmark starting point; create copies named `evals/garak-owasp-baseline.yaml` and `evals/garak-owasp-guardrailed.yaml`, changing only the model endpoint/configuration between them. The quick config is a single-probe smoke test and is not representative for a full report. This demo does not deploy a separate guardrail endpoint, so paired reporting requires a defended endpoint reachable by the Garak provider.

```bash
uv run evalhub eval run --config evals/garak-owasp-baseline.yaml --wait
uv run evalhub eval run --config evals/garak-owasp-guardrailed.yaml --wait
```

Keep the returned job IDs with the report metadata. Report generation itself is offline after the Garak files are downloaded.

## Persist and retrieve the native Garak files

The Garak adapter writes `scan.report.jsonl`, `scan.hitlog.jsonl`, and `scan.report.html` in the evaluation job. When MLflow tracking succeeds, it logs those files to the MLflow run. This demo documents an RHOAI 3.5 workspace-context issue with the server-mediated MLflow path; confirm the artifacts exist before using that path.

For an OCI handoff, add an `exports` block to each individual eval config and configure a registry repository and an OpenShift Secret of type `kubernetes.io/dockerconfigjson` in the evaluation namespace:

```yaml
exports:
  oci:
    coordinates:
      oci_host: quay.io
      oci_repository: your-org/evalhub-garak-scans
    k8s:
      connection: evalhub-oci-push-credentials
```

The adapter assigns a deterministic job-specific tag when `oci_tag` is omitted and returns the digest-pinned reference in the full job result. Retrieve that job resource through the EvalHub API and read `results.benchmarks[].artifacts.oci_reference`. The `evalhub eval results --format json` command prints benchmark metrics; it does not download the raw scan artifacts. For example, use the EvalHub URL, token, and tenant configured for the CLI:

```bash
mkdir -p runs
curl -fsS \
  -H "Authorization: Bearer ${EVALHUB_TOKEN}" \
  -H "X-Tenant: ${EVALHUB_TENANT}" \
  "${EVALHUB_URL}/api/v1/evaluations/jobs/${JOB_ID}" \
  > runs/job.json
jq -r '.results.benchmarks[]?.artifacts.oci_reference // empty' runs/job.json
```

If the reference is empty, confirm the job completed and that `exports.oci` was included in its submitted configuration.

After authenticating `oras` to the configured registry, pull each job artifact into a separate directory. For example:

```bash
mkdir -p runs/Baseline runs/Guardrailed
(cd runs/Baseline && oras pull "$BASELINE_OCI_REF")
(cd runs/Guardrailed && oras pull "$GUARDRAILED_OCI_REF")
```

The extracted files should include `scan.report.jsonl`; the hit log is `scan.hitlog.jsonl` when Garak produced one. For KFP-backed scans, the adapter may instead report S3 artifact locations; retrieve those objects from the configured bucket before rendering.

## Render from a pipeline

From the repository root, run:

```bash
mkdir -p reports
python3 scripts/garak_report_generator.py \
  --scan Baseline=runs/Baseline/scan.report.jsonl \
  --scan Guardrailed=runs/Guardrailed/scan.report.jsonl \
  --metadata examples/garak-report-metadata.example.json \
  --output reports/garak-red-team-report.html
```

To include prompt/output examples, first confirm the hit-log file exists, then add `--hitlog Guardrailed=runs/Guardrailed/scan.hitlog.jsonl --include-examples`. Keep examples out by default.

The generator reports scan-wide rates as weighted detector-evaluation ASR and separately compares the intersection of matching probe/detector pairs. Review the scan coverage and human-authored metadata before treating the HTML as evidence. Detector hits require human triage; they are not automatically confirmed vulnerabilities.

The script writes the HTML file locally. A pipeline that needs durable run-linked output should upload it as an MLflow artifact after that path is verified on the target release, or publish it as a separate OCI artifact with both source job IDs in its annotations. Keep the generated report access-controlled when it contains sensitive findings.

## Use the notebook

Install Jupyter in the local demo environment, then start it from `evalhub-demo/` or `evalhub-demo/notebooks/`. Download the scan files into `runs/Baseline/` and `runs/Guardrailed/`; edit the notebook's metadata and input labels; and run the cells. The notebook does not call EvalHub or download artifacts itself.

## Current scope

The generator accepts one Garak report JSONL file per scan label. A single broad benchmark such as `owasp_llm_top10` fits that interface. Combining separate files from a multi-benchmark collection requires an assembly step or an extension to accept multiple files per scan. The report is HTML; this workflow does not produce PDF or JSON reports.
