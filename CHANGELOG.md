# Changelog — aumos-benchmark-suite

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.1.0] — 2026-02-26

### Added
- Initial implementation of the AumOS Benchmark Suite
- `BenchmarkRunnerService` — orchestrates end-to-end benchmark run lifecycle
- `MetricService` — queries and summarizes fidelity, privacy, and speed metrics
- `CompetitorBaselineService` — manages Gretel, MOSTLY AI, Tonic baseline data
- `RegressionService` — CI regression detection with configurable thresholds
- `ReportGeneratorService` — structured reports with Pareto curve data and competitor comparisons
- REST API: POST /run, GET /runs, GET /runs/{id}, GET /metrics, GET /baselines, PUT /baselines, POST /regression/check, POST /reports/generate
- `RunnerEngineAdapter` — coordinates tabular-engine, privacy-engine, fidelity-validator calls
- DB models: `bnk_benchmark_runs`, `bnk_metric_results`, `bnk_competitor_baselines`, `bnk_regression_checks`
- Benchmark configuration YAML support
- Dataset placeholder structure at `benchmarks/datasets/`
- Apache 2.0 license
