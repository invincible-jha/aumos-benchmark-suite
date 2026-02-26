"""Shared pytest fixtures for the AumOS Benchmark Suite test suite."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from aumos_benchmark_suite.core.models import (
    BenchmarkRun,
    CompetitorBaseline,
    MetricResult,
    RegressionCheck,
)


@pytest.fixture
def tenant_id() -> uuid.UUID:
    """Return a fixed tenant UUID for tests."""
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def run_id() -> uuid.UUID:
    """Return a fixed run UUID for tests."""
    return uuid.UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def sample_run(tenant_id: uuid.UUID, run_id: uuid.UUID) -> BenchmarkRun:
    """Return a sample BenchmarkRun in completed status."""
    run = BenchmarkRun()
    run.id = run_id
    run.tenant_id = tenant_id
    run.name = "Test benchmark run"
    run.config_name = "tabular_full_suite"
    run.dataset_name = "adult_income"
    run.aumos_version = "1.0.0"
    run.status = "completed"
    run.run_config = {"metrics": {"fidelity": ["ks_statistic"], "privacy": ["dcr_score"]}}
    run.tags = ["test"]
    run.triggered_by = "api"
    run.started_at = datetime.now(tz=timezone.utc)
    run.completed_at = datetime.now(tz=timezone.utc)
    run.duration_seconds = 42.0
    run.created_at = datetime.now(tz=timezone.utc)
    run.updated_at = datetime.now(tz=timezone.utc)
    return run


@pytest.fixture
def sample_metric(tenant_id: uuid.UUID, run_id: uuid.UUID) -> MetricResult:
    """Return a sample MetricResult for fidelity."""
    metric = MetricResult()
    metric.id = uuid.uuid4()
    metric.tenant_id = tenant_id
    metric.run_id = run_id
    metric.metric_category = "fidelity"
    metric.metric_name = "tv_complement"
    metric.metric_value = 0.85
    metric.metric_unit = "score_0_1"
    metric.higher_is_better = True
    metric.baseline_competitor = None
    metric.baseline_value = None
    metric.delta_from_baseline = None
    metric.additional_data = {}
    metric.created_at = datetime.now(tz=timezone.utc)
    return metric


@pytest.fixture
def mock_event_publisher() -> AsyncMock:
    """Return a mock Kafka event publisher."""
    publisher = AsyncMock()
    publisher.publish = AsyncMock()
    return publisher


@pytest.fixture
def mock_run_repo() -> AsyncMock:
    """Return a mock BenchmarkRunRepository."""
    return AsyncMock()


@pytest.fixture
def mock_metric_repo() -> AsyncMock:
    """Return a mock MetricResultRepository."""
    return AsyncMock()


@pytest.fixture
def mock_baseline_repo() -> AsyncMock:
    """Return a mock CompetitorBaselineRepository."""
    return AsyncMock()


@pytest.fixture
def mock_regression_repo() -> AsyncMock:
    """Return a mock RegressionCheckRepository."""
    return AsyncMock()


@pytest.fixture
def mock_runner_adapter() -> AsyncMock:
    """Return a mock RunnerEngineAdapter."""
    adapter = AsyncMock()
    adapter.validate_config = AsyncMock(return_value=(True, []))
    adapter.execute_run = AsyncMock(
        return_value={
            "fidelity": [
                {
                    "metric_name": "tv_complement",
                    "value": 0.85,
                    "unit": "score_0_1",
                    "higher_is_better": True,
                    "additional_data": {},
                }
            ],
            "privacy": [
                {
                    "metric_name": "dcr_score",
                    "value": 0.92,
                    "unit": "score_0_1",
                    "higher_is_better": True,
                    "additional_data": {},
                }
            ],
            "speed": [
                {
                    "metric_name": "rows_per_second",
                    "value": 5000.0,
                    "unit": "rows/sec",
                    "higher_is_better": True,
                    "additional_data": {},
                }
            ],
        }
    )
    return adapter
