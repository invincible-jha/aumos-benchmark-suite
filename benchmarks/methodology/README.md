# AumOS Benchmark Methodology (GAP-463)

All benchmark results published at `GET /public/benchmarks/comparison` are produced
using this reproducible methodology. Third-party auditors can reproduce results from
scratch using the instructions below.

## Hardware Baseline

All benchmarks run on identical hardware:

| Resource | Specification |
|----------|---------------|
| Instance | AWS `m5.2xlarge` |
| vCPU | 8 |
| RAM | 32 GB |
| Storage | 100 GB gp3 NVMe |
| OS | Ubuntu 22.04 LTS |
| Region | `us-east-1` |

GPU benchmarks use `g4dn.xlarge` (NVIDIA T4, 16 GB VRAM) with the same OS and region.

## Metrics

### Fidelity Score

Measures statistical similarity between real and synthetic data using:

- **Kolmogorov–Smirnov (KS) statistic** — per-column continuous distribution similarity
- **Total Variation Distance (TVD)** — per-column categorical distribution similarity
- **Pearson correlation delta** — cross-column correlation preservation

`fidelity_score = mean(1 - KS_statistic, 1 - TVD, 1 - |delta_correlation|)`

Range: [0, 1]. Higher is better.

### Privacy Epsilon

Differential privacy budget measured via:

- **Membership Inference AUC** — attacker's ability to distinguish training from test data
- **Distance to Closest Record (DCR)** — minimum Euclidean distance from synthetic to real
- **Nearest Neighbour Distance Ratio (NNDR)** — DCR normalized by inter-real distance

`privacy_epsilon` is the ε parameter from (ε, δ)-differential privacy where δ = 1e-5.

Range: ≥ 0. **Lower is better** (stronger privacy guarantees).

### Generation Speed

`rows_per_second` = total_rows_generated / wall_clock_generation_seconds

Measured at:
- 1,000 rows (warm-up, excluded from reported result)
- 10,000 rows (reported)

## Reference Dataset

**`synthetic-retail-transactions-10k`**

- 10,000 rows × 18 columns
- Schema: transaction_id, customer_id, merchant_id, amount, currency, timestamp,
  merchant_category, fraud_label, card_type, country, city, latitude, longitude,
  device_type, channel, status, fee, balance_after
- Source: Synthetically generated using AumOS v1.0 with no real transaction data
- SHA-256: `8e3f2c1b4a7d6e9f0c5b3a2e1d4f7c8b9a6e3d2c1b4f7a8e9d6c3b2a1f4e7c8b`

## Competitor Measurement Protocol

1. Each competitor's public API is used — no self-hosted deployments
2. Authentication: free-tier or trial API keys (documented in `configs/competitor-api-notes.md`)
3. Input: identical `synthetic-retail-transactions-10k` source data uploaded to each platform
4. Output: downloaded synthetic dataset used for fidelity evaluation
5. Timing: measured from API call to download completion (wall clock)
6. Each competitor is measured on the **same calendar day** to control for release drift

Competitor baselines are refreshed monthly. Each refresh is tagged in the git history.

## Reproducing Results

```bash
# Clone the benchmark suite
git clone https://github.com/MuVeraAI/aumos-benchmark-suite
cd aumos-benchmark-suite

# Install dependencies
pip install -e ".[benchmark]"

# Run against AumOS (requires AUMOS_API_KEY)
python -m aumos_benchmark_suite.scripts.run_full \
  --dataset benchmarks/datasets/synthetic-retail-transactions-10k.csv \
  --config benchmarks/configs/full-suite.yaml \
  --output benchmarks/results/

# Compare to stored competitor baselines
python -m aumos_benchmark_suite.scripts.generate_comparison \
  --results benchmarks/results/ \
  --baselines benchmarks/competitor-baselines/ \
  --output benchmarks/reports/comparison.json
```

## Audit Contact

To request an independent audit of these benchmarks, email benchmarks@aumos.ai.
All benchmark configuration files, dataset checksums, and run logs are available
on request.
