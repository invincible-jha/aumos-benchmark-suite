# Benchmark Datasets and Configurations

This directory contains benchmark datasets and configuration files used by the
AumOS Benchmark Suite to produce standardized, reproducible performance comparisons.

## Structure

```
benchmarks/
├── datasets/    — reference datasets for benchmark runs (not committed to git)
├── configs/     — benchmark configuration YAML files
└── README.md    — this file
```

## Datasets

Benchmark datasets are stored in `benchmarks/datasets/` and are NOT committed
to the repository. Download them from the AumOS internal data store or use
the dataset provisioning script.

Supported datasets:
- `adult_income` — UCI Adult Income dataset (48k rows, 14 columns, classification target)
- `credit_default` — Taiwan credit card default (30k rows, 23 columns)
- `mimic_patients` — MIMIC-III patient demographics subset (100k rows, anonymized)
- `telco_churn` — Telco customer churn (7k rows, 20 columns)

## Benchmark Configurations

Configuration files in `benchmarks/configs/` define what to measure in each run.

### Configuration Schema

```yaml
# benchmarks/configs/tabular_full_suite.yaml
name: tabular_full_suite
description: "Full fidelity + privacy + speed benchmark suite for tabular synthesis"

dataset:
  rows: 10000          # number of rows to generate
  train_ratio: 0.8     # fraction of real data used as training set

metrics:
  fidelity:
    - ks_statistic
    - tv_complement
    - correlation_similarity
    - ml_efficacy_score
  privacy:
    - membership_inference_auc
    - dcr_score
    - nndr_score
  speed:
    - rows_per_second
    - generation_latency_ms
    - peak_memory_mb

competitors:
  compare_against:
    - gretel
    - mostly_ai
    - tonic
```

## Running a Benchmark

```bash
# Via API
curl -X POST http://localhost:8000/api/v1/benchmarks/run \
  -H "Content-Type: application/json" \
  -d '{
    "name": "v1.2.3 full suite",
    "config_name": "tabular_full_suite",
    "dataset_name": "adult_income",
    "aumos_version": "1.2.3",
    "triggered_by": "manual"
  }'

# Check for CI regression
curl -X POST http://localhost:8000/api/v1/benchmarks/regression/check \
  -H "Content-Type: application/json" \
  -d '{"run_id": "<run-uuid>", "ci_build_id": "github-run-123"}'
```
