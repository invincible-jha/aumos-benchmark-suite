"""Unit tests for the AumOS Benchmark Suite service layer."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from aumos_common.errors import ConflictError, NotFoundError

from aumos_benchmark_suite.core.models import BenchmarkRun, MetricResult, RegressionCheck
from aumos_benchmark_suite.core.services import (
    BenchmarkRunnerService,
    CompetitorBaselineService,
    MetricService,
    RegressionService,
)


class TestBenchmarkRunnerService:
    """Tests for BenchmarkRunnerService."""

    def _make_service(
        self,
        run_repo: AsyncMock,
        metric_repo: AsyncMock,
        runner_adapter: AsyncMock,
        event_publisher: AsyncMock,
    ) -> BenchmarkRunnerService:
        return BenchmarkRunnerService(
            run_repo=run_repo,
            metric_repo=metric_repo,
            runner_adapter=runner_adapter,
            event_publisher=event_publisher,
        )

    @pytest.mark.asyncio
    async def test_submit_run_validates_config(
        self,
        mock_run_repo: AsyncMock,
        mock_metric_repo: AsyncMock,
        mock_runner_adapter: AsyncMock,
        mock_event_publisher: AsyncMock,
        tenant_id: uuid.UUID,
    ) -> None:
        """submit_run raises ConflictError when configuration is invalid."""
        mock_runner_adapter.validate_config = AsyncMock(
            return_value=(False, ["Missing required key: metrics"])
        )

        service = self._make_service(
            mock_run_repo, mock_metric_repo, mock_runner_adapter, mock_event_publisher
        )

        with pytest.raises(ConflictError) as exc_info:
            await service.submit_run(
                tenant_id=tenant_id,
                name="Bad run",
                config_name="bad_config",
                dataset_name="adult_income",
                aumos_version="1.0.0",
                run_config={},
            )

        assert "Invalid benchmark configuration" in str(exc_info.value)
        mock_run_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_submit_run_completes_successfully(
        self,
        mock_run_repo: AsyncMock,
        mock_metric_repo: AsyncMock,
        mock_runner_adapter: AsyncMock,
        mock_event_publisher: AsyncMock,
        sample_run: BenchmarkRun,
        tenant_id: uuid.UUID,
    ) -> None:
        """submit_run creates run, executes, and persists metrics on success."""
        mock_run_repo.create = AsyncMock(return_value=sample_run)
        mock_run_repo.update_status = AsyncMock(return_value=sample_run)
        mock_metric_repo.create_bulk = AsyncMock(return_value=[])

        service = self._make_service(
            mock_run_repo, mock_metric_repo, mock_runner_adapter, mock_event_publisher
        )

        result = await service.submit_run(
            tenant_id=tenant_id,
            name="Test run",
            config_name="tabular_full_suite",
            dataset_name="adult_income",
            aumos_version="1.0.0",
            run_config={"metrics": {"fidelity": ["tv_complement"]}},
        )

        assert result.status == "completed"
        mock_run_repo.create.assert_called_once()
        mock_metric_repo.create_bulk.assert_called_once()
        mock_event_publisher.publish.assert_called()

    @pytest.mark.asyncio
    async def test_get_run_raises_not_found(
        self,
        mock_run_repo: AsyncMock,
        mock_metric_repo: AsyncMock,
        mock_runner_adapter: AsyncMock,
        mock_event_publisher: AsyncMock,
        tenant_id: uuid.UUID,
    ) -> None:
        """get_run raises NotFoundError when run does not exist."""
        mock_run_repo.get_by_id = AsyncMock(return_value=None)

        service = self._make_service(
            mock_run_repo, mock_metric_repo, mock_runner_adapter, mock_event_publisher
        )

        with pytest.raises(NotFoundError):
            await service.get_run(uuid.uuid4(), tenant_id)

    @pytest.mark.asyncio
    async def test_submit_run_marks_failed_on_runner_error(
        self,
        mock_run_repo: AsyncMock,
        mock_metric_repo: AsyncMock,
        mock_runner_adapter: AsyncMock,
        mock_event_publisher: AsyncMock,
        sample_run: BenchmarkRun,
        tenant_id: uuid.UUID,
    ) -> None:
        """submit_run marks run as failed when runner raises an exception."""
        failed_run = BenchmarkRun()
        failed_run.id = sample_run.id
        failed_run.status = "failed"
        failed_run.error_message = "Connection refused"
        failed_run.tenant_id = tenant_id
        failed_run.name = "Test"
        failed_run.config_name = "cfg"
        failed_run.dataset_name = "ds"
        failed_run.aumos_version = "1.0.0"
        failed_run.run_config = {}
        failed_run.tags = []
        failed_run.created_at = datetime.now(tz=timezone.utc)
        failed_run.updated_at = datetime.now(tz=timezone.utc)

        mock_run_repo.create = AsyncMock(return_value=sample_run)
        mock_run_repo.update_status = AsyncMock(
            side_effect=[sample_run, failed_run]
        )
        mock_runner_adapter.execute_run = AsyncMock(
            side_effect=RuntimeError("Connection refused")
        )

        service = self._make_service(
            mock_run_repo, mock_metric_repo, mock_runner_adapter, mock_event_publisher
        )

        result = await service.submit_run(
            tenant_id=tenant_id,
            name="Test run",
            config_name="tabular_full_suite",
            dataset_name="adult_income",
            aumos_version="1.0.0",
            run_config={"metrics": {"fidelity": ["tv_complement"]}},
        )

        assert result.status == "failed"
        mock_metric_repo.create_bulk.assert_not_called()


class TestRegressionService:
    """Tests for RegressionService."""

    def _make_service(
        self,
        run_repo: AsyncMock,
        metric_repo: AsyncMock,
        regression_repo: AsyncMock,
        event_publisher: AsyncMock,
    ) -> RegressionService:
        return RegressionService(
            run_repo=run_repo,
            metric_repo=metric_repo,
            regression_repo=regression_repo,
            event_publisher=event_publisher,
            fidelity_threshold=0.05,
            privacy_threshold=0.03,
            speed_threshold_percent=20.0,
        )

    @pytest.mark.asyncio
    async def test_check_regression_skipped_when_no_baseline(
        self,
        mock_run_repo: AsyncMock,
        mock_metric_repo: AsyncMock,
        mock_regression_repo: AsyncMock,
        mock_event_publisher: AsyncMock,
        sample_run: BenchmarkRun,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> None:
        """check_regression returns skipped when no prior run exists."""
        mock_run_repo.get_by_id = AsyncMock(return_value=sample_run)
        mock_run_repo.get_latest_completed = AsyncMock(return_value=None)

        skipped_check = RegressionCheck()
        skipped_check.id = uuid.uuid4()
        skipped_check.run_id = run_id
        skipped_check.tenant_id = tenant_id
        skipped_check.status = "skipped"
        skipped_check.regressed_metrics = []
        skipped_check.details = {}
        skipped_check.checked_at = datetime.now(tz=timezone.utc)
        skipped_check.baseline_run_id = None
        skipped_check.ci_build_id = None
        skipped_check.ci_commit_sha = None
        skipped_check.created_at = datetime.now(tz=timezone.utc)

        mock_regression_repo.create = AsyncMock(return_value=skipped_check)

        service = self._make_service(
            mock_run_repo, mock_metric_repo, mock_regression_repo, mock_event_publisher
        )

        result = await service.check_regression(tenant_id, run_id)

        assert result.status == "skipped"
        mock_metric_repo.list_by_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_regression_fails_when_run_not_completed(
        self,
        mock_run_repo: AsyncMock,
        mock_metric_repo: AsyncMock,
        mock_regression_repo: AsyncMock,
        mock_event_publisher: AsyncMock,
        sample_run: BenchmarkRun,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> None:
        """check_regression raises ConflictError when run is not completed."""
        running_run = BenchmarkRun()
        running_run.id = run_id
        running_run.status = "running"
        running_run.tenant_id = tenant_id

        mock_run_repo.get_by_id = AsyncMock(return_value=running_run)

        service = self._make_service(
            mock_run_repo, mock_metric_repo, mock_regression_repo, mock_event_publisher
        )

        with pytest.raises(ConflictError) as exc_info:
            await service.check_regression(tenant_id, run_id)

        assert "running" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_regression_passes_when_no_degradation(
        self,
        mock_run_repo: AsyncMock,
        mock_metric_repo: AsyncMock,
        mock_regression_repo: AsyncMock,
        mock_event_publisher: AsyncMock,
        sample_run: BenchmarkRun,
        sample_metric: MetricResult,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> None:
        """check_regression returns passed when metrics are within threshold."""
        # Baseline run with same or worse metrics
        baseline_run = BenchmarkRun()
        baseline_run.id = uuid.UUID("00000000-0000-0000-0000-000000000099")
        baseline_run.config_name = "tabular_full_suite"
        baseline_run.status = "completed"

        # Baseline metric (slightly worse than current)
        baseline_metric = MetricResult()
        baseline_metric.metric_name = "tv_complement"
        baseline_metric.metric_value = 0.83  # current is 0.85 — improved
        baseline_metric.metric_category = "fidelity"
        baseline_metric.higher_is_better = True

        mock_run_repo.get_by_id = AsyncMock(return_value=sample_run)
        mock_run_repo.get_latest_completed = AsyncMock(return_value=baseline_run)
        mock_metric_repo.list_by_run = AsyncMock(
            side_effect=[[sample_metric], [baseline_metric]]
        )

        passed_check = RegressionCheck()
        passed_check.id = uuid.uuid4()
        passed_check.run_id = run_id
        passed_check.tenant_id = tenant_id
        passed_check.status = "passed"
        passed_check.regressed_metrics = []
        passed_check.details = {}
        passed_check.checked_at = datetime.now(tz=timezone.utc)
        passed_check.baseline_run_id = baseline_run.id
        passed_check.ci_build_id = None
        passed_check.ci_commit_sha = None
        passed_check.created_at = datetime.now(tz=timezone.utc)

        mock_regression_repo.create = AsyncMock(return_value=passed_check)

        service = self._make_service(
            mock_run_repo, mock_metric_repo, mock_regression_repo, mock_event_publisher
        )

        result = await service.check_regression(tenant_id, run_id)

        assert result.status == "passed"
        assert result.regressed_metrics == []


class TestCompetitorBaselineService:
    """Tests for CompetitorBaselineService."""

    def _make_service(
        self,
        baseline_repo: AsyncMock,
        event_publisher: AsyncMock,
    ) -> CompetitorBaselineService:
        return CompetitorBaselineService(
            baseline_repo=baseline_repo,
            event_publisher=event_publisher,
        )

    @pytest.mark.asyncio
    async def test_upsert_baseline_rejects_invalid_competitor(
        self,
        mock_baseline_repo: AsyncMock,
        mock_event_publisher: AsyncMock,
        tenant_id: uuid.UUID,
    ) -> None:
        """upsert_baseline raises ConflictError for unknown competitor names."""
        service = self._make_service(mock_baseline_repo, mock_event_publisher)

        with pytest.raises(ConflictError) as exc_info:
            await service.upsert_baseline(
                tenant_id=tenant_id,
                competitor_name="unknown_vendor",
                metric_category="fidelity",
                metric_name="ks_statistic",
                metric_value=0.75,
                dataset_name="adult_income",
                measured_at=datetime.now(tz=timezone.utc),
            )

        assert "unknown_vendor" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_upsert_baseline_rejects_invalid_category(
        self,
        mock_baseline_repo: AsyncMock,
        mock_event_publisher: AsyncMock,
        tenant_id: uuid.UUID,
    ) -> None:
        """upsert_baseline raises ConflictError for unknown metric categories."""
        service = self._make_service(mock_baseline_repo, mock_event_publisher)

        with pytest.raises(ConflictError) as exc_info:
            await service.upsert_baseline(
                tenant_id=tenant_id,
                competitor_name="gretel",
                metric_category="invalid_category",
                metric_name="ks_statistic",
                metric_value=0.75,
                dataset_name="adult_income",
                measured_at=datetime.now(tz=timezone.utc),
            )

        assert "invalid_category" in str(exc_info.value)


class TestMetricService:
    """Tests for MetricService."""

    def _make_service(
        self,
        metric_repo: AsyncMock,
        run_repo: AsyncMock,
    ) -> MetricService:
        return MetricService(
            metric_repo=metric_repo,
            run_repo=run_repo,
        )

    @pytest.mark.asyncio
    async def test_get_metrics_raises_not_found_for_missing_run(
        self,
        mock_metric_repo: AsyncMock,
        mock_run_repo: AsyncMock,
        tenant_id: uuid.UUID,
    ) -> None:
        """get_metrics_for_run raises NotFoundError when run does not exist."""
        mock_run_repo.get_by_id = AsyncMock(return_value=None)

        service = self._make_service(mock_metric_repo, mock_run_repo)

        with pytest.raises(NotFoundError):
            await service.get_metrics_for_run(uuid.uuid4(), tenant_id)

    @pytest.mark.asyncio
    async def test_get_available_metrics_returns_all_categories(
        self,
        mock_metric_repo: AsyncMock,
        mock_run_repo: AsyncMock,
    ) -> None:
        """get_available_metrics returns metrics for all three categories."""
        service = self._make_service(mock_metric_repo, mock_run_repo)

        metrics = await service.get_available_metrics()

        assert "fidelity" in metrics
        assert "privacy" in metrics
        assert "speed" in metrics
        assert len(metrics["fidelity"]) > 0
        assert len(metrics["privacy"]) > 0
        assert len(metrics["speed"]) > 0
