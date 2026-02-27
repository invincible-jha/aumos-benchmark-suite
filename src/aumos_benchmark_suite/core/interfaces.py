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


@runtime_checkable
class ILatencyBenchmark(Protocol):
    """Interface for the latency benchmark adapter."""

    async def measure_endpoint(
        self,
        run_id: uuid.UUID,
        endpoint_url: str,
        method: str,
        payload: dict[str, Any] | None,
        headers: dict[str, str] | None,
        sample_count: int,
    ) -> dict[str, Any]:
        """Measure latency distribution for a single endpoint.

        Args:
            run_id: BenchmarkRun UUID for correlation logging.
            endpoint_url: Full URL of the endpoint to probe.
            method: HTTP method.
            payload: Optional JSON request body.
            headers: Optional extra request headers.
            sample_count: Number of measurement samples to collect.

        Returns:
            Dict with latency percentiles, mean, stddev, min, max.
        """
        ...

    def generate_latency_report(
        self,
        run_id: uuid.UUID,
        measurements: list[dict[str, Any]],
        comparison: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Generate a structured latency report from collected measurements.

        Args:
            run_id: BenchmarkRun UUID.
            measurements: List of endpoint measurement dicts.
            comparison: Optional baseline comparison dict.

        Returns:
            Structured latency report.
        """
        ...


@runtime_checkable
class IThroughputBenchmark(Protocol):
    """Interface for the throughput benchmark adapter."""

    async def measure_max_rps(
        self,
        run_id: uuid.UUID,
        endpoint_url: str,
        method: str,
        payload: dict[str, Any] | None,
        headers: dict[str, str] | None,
        max_concurrency: int,
    ) -> dict[str, Any]:
        """Find the maximum achievable requests per second for an endpoint.

        Args:
            run_id: BenchmarkRun UUID.
            endpoint_url: Full URL.
            method: HTTP method.
            payload: Optional JSON body.
            headers: Optional extra headers.
            max_concurrency: Maximum concurrent connections.

        Returns:
            Dict with max_rps, peak_concurrency, saturation_point.
        """
        ...

    def generate_throughput_report(
        self,
        run_id: uuid.UUID,
        measurements: list[dict[str, Any]],
        version_comparisons: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Generate a structured throughput report.

        Args:
            run_id: BenchmarkRun UUID.
            measurements: List of endpoint max RPS measurement dicts.
            version_comparisons: Optional list of version comparison dicts.

        Returns:
            Structured throughput report.
        """
        ...


@runtime_checkable
class ICostBenchmark(Protocol):
    """Interface for the cost benchmark adapter."""

    async def measure_inference_cost(
        self,
        run_id: uuid.UUID,
        operation_name: str,
        duration_seconds: float,
        rows_produced: int,
        peak_memory_mb: float,
        uses_gpu: bool,
        output_size_bytes: int,
    ) -> Any:
        """Compute cost for a single inference operation.

        Args:
            run_id: BenchmarkRun UUID.
            operation_name: Descriptive name.
            duration_seconds: Measured wall-clock duration.
            rows_produced: Number of rows produced.
            peak_memory_mb: Peak memory utilization.
            uses_gpu: Whether GPU was used.
            output_size_bytes: Size of output data in bytes.

        Returns:
            OperationCostMeasurement with all cost components.
        """
        ...

    def generate_cost_report(
        self,
        run_id: uuid.UUID,
        measurements: list[Any],
        provider_comparison: dict[str, Any] | None,
        optimizations: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Generate a structured cost benchmark report.

        Args:
            run_id: BenchmarkRun UUID.
            measurements: List of cost measurement instances.
            provider_comparison: Optional cross-provider comparison dict.
            optimizations: Optional list of optimization recommendations.

        Returns:
            Structured cost report.
        """
        ...


@runtime_checkable
class IFidelityBenchmark(Protocol):
    """Interface for the fidelity benchmark adapter."""

    async def run_quality_benchmark(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        generator_name: str,
        dataset_rows: int,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Run a full quality benchmark for a single generator.

        Args:
            run_id: BenchmarkRun UUID.
            dataset_name: Reference dataset name.
            generator_name: Synthesis model under test.
            dataset_rows: Number of synthetic rows to evaluate.
            metadata: Optional metadata hints.

        Returns:
            Dict with overall_score, per-metric scores, and pass/fail status.
        """
        ...

    def generate_fidelity_report(
        self,
        run_id: uuid.UUID,
        benchmark_results: list[dict[str, Any]],
        comparison: dict[str, Any] | None,
        consistency: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Generate a structured fidelity benchmark report.

        Args:
            run_id: BenchmarkRun UUID.
            benchmark_results: List of quality benchmark outputs.
            comparison: Optional generator comparison dict.
            consistency: Optional quality consistency dict.

        Returns:
            Structured fidelity report.
        """
        ...


@runtime_checkable
class IPrivacyBenchmark(Protocol):
    """Interface for the privacy benchmark adapter."""

    async def measure_reid_risk(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        generator_name: str,
        dataset_rows: int,
    ) -> dict[str, Any]:
        """Measure re-identification risk for a synthetic dataset.

        Args:
            run_id: BenchmarkRun UUID.
            dataset_name: Reference dataset name.
            generator_name: Generator under test.
            dataset_rows: Row count to evaluate.

        Returns:
            Dict with per-metric risk scores and aggregate risk flag.
        """
        ...

    def generate_privacy_report(
        self,
        run_id: uuid.UUID,
        measurements: list[dict[str, Any]],
        guarantee_verification: dict[str, Any] | None,
        tradeoff_curve: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Generate a structured privacy benchmark report.

        Args:
            run_id: BenchmarkRun UUID.
            measurements: List of privacy measurement dicts.
            guarantee_verification: Optional guarantee verification dict.
            tradeoff_curve: Optional privacy-utility tradeoff curve.

        Returns:
            Structured privacy report.
        """
        ...


@runtime_checkable
class IScalabilityBenchmark(Protocol):
    """Interface for the scalability benchmark adapter."""

    async def run_linear_scalability_test(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        base_row_count: int,
        scale_multipliers: list[int] | None,
    ) -> dict[str, Any]:
        """Test linear scalability by generating data at increasing volumes.

        Args:
            run_id: BenchmarkRun UUID.
            dataset_name: Dataset name.
            base_row_count: Number of rows at the 1x baseline.
            scale_multipliers: List of scale multipliers to test.

        Returns:
            Dict with scale_curve, scaling_ceiling, is_linear_scalable.
        """
        ...

    def generate_scalability_report(
        self,
        run_id: uuid.UUID,
        linear_test: dict[str, Any],
        horizontal_test: dict[str, Any] | None,
        isolation_test: dict[str, Any] | None,
        bottlenecks: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Generate a structured scalability benchmark report.

        Args:
            run_id: BenchmarkRun UUID.
            linear_test: Output from run_linear_scalability_test.
            horizontal_test: Optional horizontal scaling results.
            isolation_test: Optional tenant isolation results.
            bottlenecks: Optional list from identify_bottlenecks.

        Returns:
            Structured scalability report.
        """
        ...


@runtime_checkable
class IComparisonReporter(Protocol):
    """Interface for the benchmark comparison reporter adapter."""

    def compare_versions(
        self,
        run_id: uuid.UUID,
        current_run: dict[str, Any],
        baseline_run: dict[str, Any],
        metric_categories: list[str] | None,
    ) -> dict[str, Any]:
        """Generate a cross-version comparison between two benchmark runs.

        Args:
            run_id: Current BenchmarkRun UUID.
            current_run: Full report dict from the current run.
            baseline_run: Full report dict from the baseline run.
            metric_categories: Optional subset of categories to compare.

        Returns:
            Version comparison dict with per-metric deltas and regression flags.
        """
        ...

    def export_report(
        self,
        report: dict[str, Any],
        output_format: str,
    ) -> str:
        """Export a report to the specified format string.

        Args:
            report: Report dict to serialize.
            output_format: Output format (json | html | markdown).

        Returns:
            String representation in the requested format.
        """
        ...


@runtime_checkable
class IGPUBenchmark(Protocol):
    """Interface for the GPU benchmark adapter."""

    async def profile_gpu_utilization(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        gpu_type: str,
        row_count: int,
        sample_interval_seconds: float,
    ) -> Any:
        """Profile GPU utilization during synthetic data generation.

        Args:
            run_id: BenchmarkRun UUID.
            dataset_name: Dataset name.
            gpu_type: GPU model identifier.
            row_count: Rows to generate.
            sample_interval_seconds: Metrics polling interval.

        Returns:
            GPUMeasurement with utilization and throughput statistics.
        """
        ...

    def generate_gpu_report(
        self,
        run_id: uuid.UUID,
        measurements: list[Any],
        multi_gpu_results: dict[str, Any] | None,
        cost_ratios: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Generate a structured GPU benchmark report.

        Args:
            run_id: BenchmarkRun UUID.
            measurements: List of GPUMeasurement instances.
            multi_gpu_results: Optional multi-GPU scaling dict.
            cost_ratios: Optional cost-performance ratio list.

        Returns:
            Structured GPU benchmark report.
        """
        ...
