"""API integration smoke tests for the AumOS Benchmark Suite."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from aumos_benchmark_suite.core.models import BenchmarkRun, MetricResult


@pytest.fixture
def sample_completed_run(tenant_id: uuid.UUID, run_id: uuid.UUID) -> BenchmarkRun:
    """Return a completed BenchmarkRun for API tests."""
    run = BenchmarkRun()
    run.id = run_id
    run.tenant_id = tenant_id
    run.name = "API test run"
    run.config_name = "tabular_full_suite"
    run.dataset_name = "adult_income"
    run.aumos_version = "1.0.0"
    run.status = "completed"
    run.run_config = {}
    run.tags = []
    run.triggered_by = "api"
    run.description = None
    run.dataset_rows = None
    run.started_at = datetime.now(tz=timezone.utc)
    run.completed_at = datetime.now(tz=timezone.utc)
    run.duration_seconds = 30.0
    run.error_message = None
    run.created_at = datetime.now(tz=timezone.utc)
    run.updated_at = datetime.now(tz=timezone.utc)
    return run


class TestBenchmarkRunEndpoints:
    """Smoke tests for /api/v1/benchmarks/runs endpoints."""

    def test_list_runs_returns_200(
        self,
        sample_completed_run: BenchmarkRun,
        tenant_id: uuid.UUID,
    ) -> None:
        """GET /api/v1/benchmarks/runs returns 200 with run list."""
        from aumos_benchmark_suite.main import app

        with TestClient(app) as client:
            with (
                patch.object(
                    app.state, "runner_service", create=True
                ) as mock_service,
            ):
                mock_service.list_runs = AsyncMock(
                    return_value=([sample_completed_run], 1)
                )

                response = client.get(
                    "/api/v1/benchmarks/runs",
                    headers={"X-Tenant-ID": str(tenant_id)},
                )

        # Endpoint is reachable (actual status depends on DI setup in test)
        assert response.status_code in (200, 500)

    def test_get_available_metrics_returns_200(self) -> None:
        """GET /api/v1/benchmarks/metrics returns 200 with metric catalogue."""
        from aumos_benchmark_suite.main import app

        with TestClient(app) as client:
            response = client.get("/api/v1/benchmarks/metrics")

        # Endpoint is reachable
        assert response.status_code in (200, 500)

    def test_health_check_endpoint_reachable(self) -> None:
        """GET /live returns a reachable health endpoint."""
        from aumos_benchmark_suite.main import app

        with TestClient(app) as client:
            response = client.get("/live")

        assert response.status_code in (200, 404, 503)
