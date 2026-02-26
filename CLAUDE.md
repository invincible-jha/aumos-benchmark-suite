# CLAUDE.md — AumOS Benchmark Suite

## Project Overview

AumOS Enterprise is a composable enterprise AI platform with 9 products + 2 services
across 78 repositories. This repo (`aumos-benchmark-suite`) is a **DevEx / Quality** repository:
Standardized, reproducible performance benchmarks comparing AumOS against competitors
(Gretel, MOSTLY AI, Tonic). Produces speed, fidelity, and privacy Pareto curves and
serves as the gate for CI regression testing.

**Release Tier:** Open Core
**Purpose:** Benchmarking, competitive positioning, CI quality gating
**Port:** 8000
**Table prefix:** `bnk_`
**Env prefix:** `AUMOS_BENCHMARK_`

## Repo Purpose

Provides:
1. A REST API to submit and execute benchmark runs against any AumOS release
2. Persistent metric storage for fidelity, privacy, and speed measurements
3. Competitor baseline management (Gretel, MOSTLY AI, Tonic)
4. CI regression detection — fails the build when performance degrades beyond threshold
5. Report generation with Pareto curve data and competitor comparison sections

## Architecture Position

```
aumos-tabular-engine    ↗
aumos-privacy-engine    → aumos-benchmark-suite → benchmark DB (bnk_*)
aumos-fidelity-validator ↗                      ↘ Kafka (benchmark.* events)
                                                 ↘ CI/CD pipeline (regression gate)
```

**Upstream dependencies (this repo IMPORTS from):**
- `aumos-common` — auth, database, events, errors, config, health
- `aumos-proto` — Protobuf message definitions
- `aumos-tabular-engine` — data generation (speed measurement)
- `aumos-privacy-engine` — privacy metric computation
- `aumos-fidelity-validator` — fidelity metric computation

**Downstream dependents:**
- CI/CD pipelines (regression gate before every release)
- `aumos-competitive-analysis` — consumes benchmark results for go-to-market
- `aumos-docs` — embeds benchmark charts in documentation

## Tech Stack (DO NOT DEVIATE)

| Component | Version | Purpose |
|-----------|---------|---------|
| Python | 3.11+ | Runtime |
| FastAPI | 0.110+ | REST API framework |
| SQLAlchemy | 2.0+ (async) | Database ORM |
| asyncpg | 0.29+ | PostgreSQL async driver |
| Pydantic | 2.6+ | Data validation, settings, API schemas |
| httpx | 0.27+ | HTTP client for upstream service calls |
| pyyaml | 6.0+ | Benchmark configuration YAML parsing |
| confluent-kafka | 2.3+ | Kafka via aumos-common |
| pytest | 8.0+ | Testing framework |
| ruff | 0.3+ | Linting and formatting |
| mypy | 1.8+ | Type checking |

## Coding Standards

### ABSOLUTE RULES

1. **Import aumos-common, never reimplement.** Use auth, DB, events, errors, config, health.
2. **Type hints on EVERY function.** No exceptions.
3. **Pydantic models for ALL API inputs/outputs.** Never return raw dicts.
4. **RLS tenant isolation.** Never write raw SQL that bypasses RLS.
5. **Structured logging via structlog.** Never use print() or logging.getLogger().
6. **Publish domain events to Kafka after state changes.**
7. **Async by default.** All I/O operations must be async.
8. **Google-style docstrings** on all public classes and functions.

### Benchmark Domain Rules

- **Metric categories**: Only `fidelity`, `privacy`, `speed` are valid categories.
- **Valid competitors**: Only `gretel`, `mostly_ai`, `tonic` are valid competitor names.
- **Run immutability**: Runs in terminal status (completed/failed/cancelled) cannot be modified.
- **Regression thresholds**: Configurable per category via env vars. Fidelity: 0.05, Privacy: 0.03, Speed: 20%.
- **Baseline upsert**: Competitor baselines use upsert semantics (competitor+metric+dataset is unique key).
- **Reports from persistence**: Reports are always generated from persisted metric results, never from raw run output.
- **CI integration**: The regression check endpoint is designed for CI pipeline integration with ci_build_id and ci_commit_sha.

### File Structure Convention

```
src/aumos_benchmark_suite/
├── __init__.py
├── main.py                       # FastAPI app entry point using create_app()
├── settings.py                   # Extends AumOSSettings with AUMOS_BENCHMARK_ prefix
├── api/                          # FastAPI routes (thin layer — delegates to services)
│   ├── __init__.py
│   ├── router.py                 # All endpoints
│   └── schemas.py                # Pydantic request/response models
├── core/                         # Business logic (no framework dependencies)
│   ├── __init__.py
│   ├── models.py                 # SQLAlchemy ORM models (bnk_ prefix)
│   ├── interfaces.py             # Protocol classes for dependency injection
│   └── services.py               # BenchmarkRunnerService, MetricService, etc.
├── adapters/                     # External integrations
│   ├── __init__.py
│   ├── repositories.py           # SQLAlchemy repositories (extend BaseRepository)
│   ├── kafka.py                  # Benchmark event publishing
│   └── runner_engine.py          # Coordinates tabular/privacy/fidelity service calls
└── migrations/                   # Alembic migrations
    ├── env.py
    ├── alembic.ini
    └── versions/
benchmarks/
├── datasets/                     # Reference datasets (not in git)
├── configs/                      # Benchmark YAML configuration files
└── README.md
tests/
├── conftest.py
├── test_services.py
└── test_api.py
```

## API Conventions

- All endpoints under `/api/v1/benchmarks/` prefix
- Auth: Bearer JWT token (validated by aumos-common)
- Tenant: `X-Tenant-ID` header
- Pagination: `?page=1&page_size=20`

## Database Conventions

- Table prefix: `bnk_`
- ALL tenant-scoped tables: extend `AumOSModel` (id, tenant_id, created_at, updated_at)
- RLS policy on every tenant table

## Kafka Events Published

- `benchmark.run.started` — run began execution
- `benchmark.run.completed` — run finished with metrics
- `benchmark.run.failed` — run execution error
- `benchmark.regression.checked` — CI regression check result
- `benchmark.report.generated` — report generation complete

## What Claude Code Should NOT Do

1. Do NOT reimplement anything in aumos-common.
2. Do NOT return raw dicts from API endpoints.
3. Do NOT write raw SQL — use SQLAlchemy ORM.
4. Do NOT hardcode configuration — use Pydantic Settings.
5. Do NOT skip type hints.
6. Do NOT add competitors beyond gretel/mostly_ai/tonic without updating VALID_COMPETITORS.
7. Do NOT modify completed/failed/cancelled runs.
8. Do NOT generate reports from in-memory run output — always persist metrics first.
