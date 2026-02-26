"""Abstract interfaces (Protocol classes) for the AumOS Benchmark Suite service.

All adapters implement these protocols so services depend only on abstractions,
enabling straightforward testing via mock implementations.
"""

import uuid
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from aumos_benchmark_suite.core.models import (
    BenchmarkRun,
    CompetitorBaseline,
    MetricResult,
    RegressionCheck,
)


@runtime_checkable
class IBenchmarkRunRepository(Protocol):
    """Persistence interface for BenchmarkRun entities."""

    async def create(
        self,
        tenant_id: uuid.UUID,
        name: str,
        config_name: str,
        dataset_name: str,
        aumos_version: str,
        run_config: dict[str, Any],
        description: str | None,
        tags: list[str],
        triggered_by: str | None,
    ) -> BenchmarkRun:
        """Create and persist a new BenchmarkRun in pending status.

        Args:
            tenant_id: Owning tenant UUID.
            name: Human-readable run name.
            config_name: Benchmark configuration YAML name.
            dataset_name: Name of the dataset to benchmark on.
            aumos_version: AumOS version string under test.
            run_config: Full configuration snapshot.
            description: Optional human-readable description.
            tags: List of tag strings for filtering (e.g., ['ci', 'nightly']).
            triggered_by: What initiated this run (api | ci | schedule | manual).

        Returns:
            Newly created BenchmarkRun with status=pending.
        """
        ...

    async def get_by_id(
        self, run_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> BenchmarkRun | None:
        """Retrieve a run by UUID within a tenant.

        Args:
            run_id: BenchmarkRun UUID.
            tenant_id: Requesting tenant for RLS enforcement.

        Returns:
            BenchmarkRun or None if not found.
        """
        ...

    async def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        page: int,
        page_size: int,
        status: str | None,
        config_name: str | None,
    ) -> tuple[list[BenchmarkRun], int]:
        """List runs for a tenant with pagination and optional filters.

        Args:
            tenant_id: Requesting tenant.
            page: 1-based page number.
            page_size: Results per page.
            status: Optional status filter.
            config_name: Optional config name filter.

        Returns:
            Tuple of (runs, total_count).
        """
        ...

    async def update_status(
        self,
        run_id: uuid.UUID,
        status: str,
        started_at: datetime | None,
        completed_at: datetime | None,
        duration_seconds: float | None,
        error_message: str | None,
    ) -> BenchmarkRun:
        """Update run status and lifecycle timestamps.

        Args:
            run_id: BenchmarkRun UUID.
            status: New status value.
            started_at: Execution start timestamp for running state.
            completed_at: Completion timestamp for terminal states.
            duration_seconds: Total duration for completed runs.
            error_message: Error detail when status=failed.

        Returns:
            Updated BenchmarkRun.
        """
        ...

    async def get_latest_completed(
        self,
        tenant_id: uuid.UUID,
        config_name: str,
    ) -> BenchmarkRun | None:
        """Retrieve the most recently completed run for a given config.

        Used as baseline for regression checks.

        Args:
            tenant_id: Requesting tenant.
            config_name: Benchmark configuration name to filter by.

        Returns:
            Most recent completed BenchmarkRun or None if no history.
        """
        ...


@runtime_checkable
class IMetricResultRepository(Protocol):
    """Persistence interface for MetricResult entities."""

    async def create_bulk(
        self,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        metrics: list[dict[str, Any]],
    ) -> list[MetricResult]:
        """Bulk-create metric results for a benchmark run.

        Args:
            tenant_id: Owning tenant UUID.
            run_id: Parent BenchmarkRun UUID.
            metrics: List of metric dicts with all required MetricResult fields.

        Returns:
            List of created MetricResult instances.
        """
        ...

    async def list_by_run(
        self,
        run_id: uuid.UUID,
        category: str | None,
    ) -> list[MetricResult]:
        """Retrieve all metric results for a run, optionally filtered by category.

        Args:
            run_id: Parent BenchmarkRun UUID.
            category: Optional category filter (fidelity | privacy | speed).

        Returns:
            List of MetricResult instances.
        """
        ...

    async def get_summary(
        self,
        run_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Aggregate metric summary statistics for a run.

        Args:
            run_id: BenchmarkRun UUID.
            tenant_id: Requesting tenant.

        Returns:
            Dict with per-category averages and key metric values.
        """
        ...


@runtime_checkable
class ICompetitorBaselineRepository(Protocol):
    """Persistence interface for CompetitorBaseline entities."""

    async def create(
        self,
        tenant_id: uuid.UUID,
        competitor_name: str,
        metric_category: str,
        metric_name: str,
        metric_value: float,
        dataset_name: str,
        measured_at: datetime,
        higher_is_better: bool,
        metric_unit: str | None,
        source_url: str | None,
        notes: str | None,
    ) -> CompetitorBaseline:
        """Create a competitor baseline record.

        Args:
            tenant_id: Owning tenant UUID.
            competitor_name: Competitor identifier (gretel | mostly_ai | tonic).
            metric_category: fidelity | privacy | speed.
            metric_name: Specific metric name.
            metric_value: Baseline numeric value.
            dataset_name: Dataset on which baseline was measured.
            measured_at: Timestamp of measurement.
            higher_is_better: True if higher is better for this metric.
            metric_unit: Unit of measurement.
            source_url: Optional source publication URL.
            notes: Optional additional context.

        Returns:
            Newly created CompetitorBaseline.
        """
        ...

    async def get_by_competitor_and_metric(
        self,
        competitor_name: str,
        metric_name: str,
        dataset_name: str,
        tenant_id: uuid.UUID,
    ) -> CompetitorBaseline | None:
        """Retrieve a baseline by the unique competitor+metric+dataset combination.

        Args:
            competitor_name: Competitor identifier.
            metric_name: Specific metric name.
            dataset_name: Dataset name.
            tenant_id: Requesting tenant.

        Returns:
            CompetitorBaseline or None if not found.
        """
        ...

    async def list_by_competitor(
        self,
        competitor_name: str,
        tenant_id: uuid.UUID,
        active_only: bool,
    ) -> list[CompetitorBaseline]:
        """List all baselines for a specific competitor.

        Args:
            competitor_name: Competitor identifier.
            tenant_id: Requesting tenant.
            active_only: If True, exclude soft-deleted baselines.

        Returns:
            List of CompetitorBaseline instances.
        """
        ...

    async def list_all(
        self,
        tenant_id: uuid.UUID,
        active_only: bool,
    ) -> list[CompetitorBaseline]:
        """List all competitor baselines.

        Args:
            tenant_id: Requesting tenant.
            active_only: If True, exclude soft-deleted baselines.

        Returns:
            List of all active CompetitorBaseline instances.
        """
        ...

    async def upsert(
        self,
        tenant_id: uuid.UUID,
        competitor_name: str,
        metric_category: str,
        metric_name: str,
        metric_value: float,
        dataset_name: str,
        measured_at: datetime,
        higher_is_better: bool,
        metric_unit: str | None,
        source_url: str | None,
        notes: str | None,
    ) -> CompetitorBaseline:
        """Create or update a competitor baseline (upsert by unique key).

        Args:
            tenant_id: Owning tenant UUID.
            competitor_name: Competitor identifier.
            metric_category: fidelity | privacy | speed.
            metric_name: Specific metric name.
            metric_value: New baseline value.
            dataset_name: Dataset name.
            measured_at: Measurement timestamp.
            higher_is_better: Metric direction.
            metric_unit: Unit of measurement.
            source_url: Optional source URL.
            notes: Optional additional context.

        Returns:
            Created or updated CompetitorBaseline.
        """
        ...


@runtime_checkable
class IRegressionCheckRepository(Protocol):
    """Persistence interface for RegressionCheck entities."""

    async def create(
        self,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        baseline_run_id: uuid.UUID | None,
        status: str,
        regressed_metrics: list[str],
        details: dict[str, Any],
        checked_at: datetime,
        ci_build_id: str | None,
        ci_commit_sha: str | None,
    ) -> RegressionCheck:
        """Create a regression check record.

        Args:
            tenant_id: Owning tenant UUID.
            run_id: BenchmarkRun UUID being checked.
            baseline_run_id: Previous run used as baseline (None if first run).
            status: passed | failed | skipped.
            regressed_metrics: List of metric names that regressed.
            details: Full per-metric analysis dict.
            checked_at: Timestamp of the check.
            ci_build_id: Optional CI build identifier.
            ci_commit_sha: Optional git commit SHA.

        Returns:
            Newly created RegressionCheck.
        """
        ...

    async def get_by_run(
        self, run_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> RegressionCheck | None:
        """Retrieve the regression check for a specific run.

        Args:
            run_id: BenchmarkRun UUID.
            tenant_id: Requesting tenant.

        Returns:
            RegressionCheck or None if no check performed.
        """
        ...

    async def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        page: int,
        page_size: int,
        status: str | None,
    ) -> tuple[list[RegressionCheck], int]:
        """List regression checks for a tenant with pagination.

        Args:
            tenant_id: Requesting tenant.
            page: 1-based page number.
            page_size: Results per page.
            status: Optional status filter (passed | failed | skipped).

        Returns:
            Tuple of (checks, total_count).
        """
        ...


@runtime_checkable
class IBenchmarkRunnerAdapter(Protocol):
    """Interface for the benchmark runner execution engine adapter."""

    async def execute_run(
        self,
        run_id: uuid.UUID,
        config: dict[str, Any],
        dataset_name: str,
    ) -> dict[str, Any]:
        """Execute a benchmark run and return raw metric measurements.

        Args:
            run_id: BenchmarkRun UUID for correlation and logging.
            config: Full benchmark configuration specifying what to measure.
            dataset_name: Dataset to use for benchmark execution.

        Returns:
            Dict of raw metric measurements grouped by category:
            {
                "fidelity": [{metric_name, value, unit, higher_is_better, additional_data}],
                "privacy": [...],
                "speed": [...],
            }
        """
        ...

    async def validate_config(
        self, config: dict[str, Any]
    ) -> tuple[bool, list[str]]:
        """Validate a benchmark configuration before execution.

        Args:
            config: Benchmark configuration dict to validate.

        Returns:
            Tuple of (is_valid, list_of_validation_errors).
        """
        ...
