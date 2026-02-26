# Contributing to aumos-benchmark-suite

## Development Setup

```bash
git clone <repo>
cd aumos-benchmark-suite
pip install -e ".[dev]"
```

## Running Tests

```bash
make test          # full test suite with coverage
make test-quick    # fast run without coverage
```

## Code Quality

```bash
make lint          # ruff check + format check
make format        # auto-fix lint and formatting
make typecheck     # mypy strict
```

## Adding a New Benchmark Config

1. Create a YAML file in `benchmarks/configs/` following the schema in `benchmarks/README.md`
2. Register any new metric names in `MetricService.get_available_metrics()`
3. Add corresponding fixture data to `benchmarks/datasets/README.md`

## Adding a Competitor Baseline

Use the PUT /api/v1/benchmarks/baselines endpoint or import via the admin CLI.

## Pull Requests

- Branch from `main`: `feature/`, `fix/`, `docs/`
- All commits must follow Conventional Commits (`feat:`, `fix:`, `refactor:`, etc.)
- Tests required for new services and API endpoints
- Mypy strict must pass before merge
