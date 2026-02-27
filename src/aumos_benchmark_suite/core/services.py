"""Business logic services for the AumOS Benchmark Suite service.

All services depend on repository and adapter interfaces (not concrete
implementations) and receive dependencies via constructor injection.
No framework code (FastAPI, SQLAlchemy) belongs here.

Key invariants enforced by services:
- Benchmark runs are immutable once in terminal status (completed/failed/cancelled).
- Regression checks compare a run to the most recent completed run of the same config.
- Competitor baselines are upserted — one record per competitor+metric+dataset combination.
- Reports are generated from persisted metric results, never from raw run output.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from aumos_common.errors import ConflictError, ErrorCode, NotFoundError
from aumos_common.events import EventPublisher, Topics
from aumos_common.observability import get_logger

from aumos_benchmark_suite.core.interfaces import (
    IBenchmarkRunnerAdapter,
    IBenchmarkRunRepository,
    IComparisonReporter,
    ICompetitorBaselineRepository,
    ICostBenchmark,
    IFidelityBenchmark,
    IGPUBenchmark,
    ILatencyBenchmark,
    IMetricResultRepository,
    IPrivacyBenchmark,
    IRegressionCheckRepository,
    IScalabilityBenchmark,
    IThroughputBenchmark,
)
from aumos_benchmark_suite.core.models import (
    BenchmarkRun,
    CompetitorBaseline,
    MetricResult,
    RegressionCheck,
)

logger = get_logger(__name__)

# Valid benchmark run status values
TERMINAL_RUN_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})

# Valid competitor names
VALID_COMPETITORS: frozenset[str] = frozenset({"gretel", "mostly_ai", "tonic"})

# Valid metric categories
VALID_METRIC_CATEGORIES: frozenset[str] = frozenset({"fidelity", "privacy", "speed"})


class BenchmarkRunnerService:
    """Orchestrate the lifecycle of benchmark run submissions and execution.

    Coordinates between the runner adapter (which executes benchmarks) and
    repositories (which persist results) to provide a single entry point
    for all benchmark execution operations.
    """

    def __init__(
        self,
        run_repo: IBenchmarkRunRepository,
        metric_repo: IMetricResultRepository,
        runner_adapter: IBenchmarkRunnerAdapter,
        event_publisher: EventPublisher,
    ) -> None:
        """Initialise with injected dependencies.

        Args:
            run_repo: BenchmarkRun persistence.
            metric_repo: MetricResult persistence.
            runner_adapter: Benchmark execution engine.
            event_publisher: Kafka event publisher.
        """
        self._runs = run_repo
        self._metrics = metric_repo
        self._runner = runner_adapter
        self._publisher = event_publisher

    async def submit_run(
        self,
        tenant_id: uuid.UUID,
        name: str,
        config_name: str,
        dataset_name: str,
        aumos_version: str,
        run_config: dict[str, Any],
        description: str | None = None,
        tags: list[str] | None = None,
        triggered_by: str | None = None,
    ) -> BenchmarkRun:
        """Submit a new benchmark run for execution.

        Validates the configuration, creates the run record in pending status,
        and immediately executes the benchmark synchronously.

        Args:
            tenant_id: Owning tenant UUID.
            name: Human-readable run name.
            config_name: Benchmark configuration YAML name.
            dataset_name: Name of the dataset to benchmark on.
            aumos_version: AumOS version string under test.
            run_config: Full configuration snapshot.
            description: Optional human-readable description.
            tags: Optional list of tag strings for filtering.
            triggered_by: What initiated this run.

        Returns:
            Completed BenchmarkRun with all metrics recorded.

        Raises:
            ConflictError: If the configuration is invalid.
        """
        is_valid, validation_errors = await self._runner.validate_config(run_config)
        if not is_valid:
            raise ConflictError(
                message=f"Invalid benchmark configuration: {'; '.join(validation_errors)}",
                error_code=ErrorCode.INVALID_OPERATION,
            )

        run = await self._runs.create(
            tenant_id=tenant_id,
            name=name,
            config_name=config_name,
            dataset_name=dataset_name,
            aumos_version=aumos_version,
            run_config=run_config,
            description=description,
            tags=tags or [],
            triggered_by=triggered_by,
        )

        logger.info(
            "Benchmark run submitted",
            tenant_id=str(tenant_id),
            run_id=str(run.id),
            config_name=config_name,
            dataset_name=dataset_name,
        )

        # Transition to running
        started_at = datetime.now(tz=timezone.utc)
        run = await self._runs.update_status(
            run_id=run.id,
            status="running",
            started_at=started_at,
            completed_at=None,
            duration_seconds=None,
            error_message=None,
        )

        await self._publisher.publish(
            Topics.BENCHMARK,
            {
                "event_type": "benchmark.run.started",
                "tenant_id": str(tenant_id),
                "run_id": str(run.id),
                "config_name": config_name,
                "dataset_name": dataset_name,
                "aumos_version": aumos_version,
            },
        )

        # Execute the benchmark
        try:
            raw_results = await self._runner.execute_run(
                run_id=run.id,
                config=run_config,
                dataset_name=dataset_name,
            )
        except Exception as exc:
            completed_at = datetime.now(tz=timezone.utc)
            duration = (completed_at - started_at).total_seconds()
            run = await self._runs.update_status(
                run_id=run.id,
                status="failed",
                started_at=None,
                completed_at=completed_at,
                duration_seconds=duration,
                error_message=str(exc),
            )
            logger.error(
                "Benchmark run failed",
                run_id=str(run.id),
                error=str(exc),
            )
            await self._publisher.publish(
                Topics.BENCHMARK,
                {
                    "event_type": "benchmark.run.failed",
                    "tenant_id": str(tenant_id),
                    "run_id": str(run.id),
                    "error": str(exc),
                },
            )
            return run

        # Persist metric results
        all_metrics: list[dict[str, Any]] = []
        for category, metric_list in raw_results.items():
            for metric in metric_list:
                all_metrics.append(
                    {
                        "metric_category": category,
                        "metric_name": metric["metric_name"],
                        "metric_value": metric["value"],
                        "metric_unit": metric.get("unit"),
                        "higher_is_better": metric.get("higher_is_better", True),
                        "baseline_competitor": metric.get("baseline_competitor"),
                        "baseline_value": metric.get("baseline_value"),
                        "delta_from_baseline": metric.get("delta_from_baseline"),
                        "additional_data": metric.get("additional_data", {}),
                    }
                )

        if all_metrics:
            await self._metrics.create_bulk(
                tenant_id=tenant_id,
                run_id=run.id,
                metrics=all_metrics,
            )

        # Mark completed
        completed_at = datetime.now(tz=timezone.utc)
        duration = (completed_at - started_at).total_seconds()
        run = await self._runs.update_status(
            run_id=run.id,
            status="completed",
            started_at=None,
            completed_at=completed_at,
            duration_seconds=duration,
            error_message=None,
        )

        logger.info(
            "Benchmark run completed",
            run_id=str(run.id),
            duration_seconds=duration,
            metric_count=len(all_metrics),
        )

        await self._publisher.publish(
            Topics.BENCHMARK,
            {
                "event_type": "benchmark.run.completed",
                "tenant_id": str(tenant_id),
                "run_id": str(run.id),
                "config_name": config_name,
                "duration_seconds": duration,
                "metric_count": len(all_metrics),
            },
        )

        return run

    async def get_run(
        self, run_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> BenchmarkRun:
        """Retrieve a benchmark run by ID.

        Args:
            run_id: BenchmarkRun UUID.
            tenant_id: Requesting tenant for RLS enforcement.

        Returns:
            BenchmarkRun with metric results.

        Raises:
            NotFoundError: If run not found.
        """
        run = await self._runs.get_by_id(run_id, tenant_id)
        if run is None:
            raise NotFoundError(
                message=f"Benchmark run {run_id} not found.",
                error_code=ErrorCode.NOT_FOUND,
            )
        return run

    async def list_runs(
        self,
        tenant_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        config_name: str | None = None,
    ) -> tuple[list[BenchmarkRun], int]:
        """List benchmark runs for a tenant.

        Args:
            tenant_id: Requesting tenant.
            page: 1-based page number.
            page_size: Results per page.
            status: Optional status filter.
            config_name: Optional config name filter.

        Returns:
            Tuple of (runs, total_count).
        """
        return await self._runs.list_by_tenant(
            tenant_id=tenant_id,
            page=page,
            page_size=page_size,
            status=status,
            config_name=config_name,
        )


class MetricService:
    """Query and summarize metric results from benchmark runs.

    Provides access to raw metric data and aggregated summaries
    for comparison against competitor baselines.
    """

    def __init__(
        self,
        metric_repo: IMetricResultRepository,
        run_repo: IBenchmarkRunRepository,
    ) -> None:
        """Initialise with injected dependencies.

        Args:
            metric_repo: MetricResult persistence.
            run_repo: BenchmarkRun persistence for ownership validation.
        """
        self._metrics = metric_repo
        self._runs = run_repo

    async def get_metrics_for_run(
        self,
        run_id: uuid.UUID,
        tenant_id: uuid.UUID,
        category: str | None = None,
    ) -> list[MetricResult]:
        """Retrieve metric results for a run, optionally filtered by category.

        Args:
            run_id: BenchmarkRun UUID.
            tenant_id: Requesting tenant (used to validate run ownership).
            category: Optional category filter (fidelity | privacy | speed).

        Returns:
            List of MetricResult instances.

        Raises:
            NotFoundError: If run not found.
            ConflictError: If category is invalid.
        """
        run = await self._runs.get_by_id(run_id, tenant_id)
        if run is None:
            raise NotFoundError(
                message=f"Benchmark run {run_id} not found.",
                error_code=ErrorCode.NOT_FOUND,
            )

        if category is not None and category not in VALID_METRIC_CATEGORIES:
            raise ConflictError(
                message=f"Invalid metric category '{category}'. Valid: {VALID_METRIC_CATEGORIES}",
                error_code=ErrorCode.INVALID_OPERATION,
            )

        return await self._metrics.list_by_run(run_id, category)

    async def get_available_metrics(self) -> dict[str, list[str]]:
        """Return the catalogue of available metric names by category.

        Returns:
            Dict mapping category name to list of metric names:
            {"fidelity": [...], "privacy": [...], "speed": [...]}
        """
        return {
            "fidelity": [
                "ks_statistic",
                "tv_complement",
                "correlation_similarity",
                "column_pair_trends",
                "boundary_coverage",
                "ml_efficacy_score",
            ],
            "privacy": [
                "membership_inference_auc",
                "attribute_inference_risk",
                "singling_out_risk",
                "linkability_risk",
                "dcr_score",
                "nndr_score",
            ],
            "speed": [
                "rows_per_second",
                "generation_latency_ms",
                "peak_memory_mb",
                "gpu_utilization_percent",
                "time_to_first_row_ms",
            ],
        }


class CompetitorBaselineService:
    """Manage and query competitor performance baseline data.

    Stores and retrieves baseline metrics for Gretel, MOSTLY AI, and Tonic
    to enable relative performance comparisons in benchmark reports.
    """

    def __init__(
        self,
        baseline_repo: ICompetitorBaselineRepository,
        event_publisher: EventPublisher,
    ) -> None:
        """Initialise with injected dependencies.

        Args:
            baseline_repo: CompetitorBaseline persistence.
            event_publisher: Kafka event publisher.
        """
        self._baselines = baseline_repo
        self._publisher = event_publisher

    async def upsert_baseline(
        self,
        tenant_id: uuid.UUID,
        competitor_name: str,
        metric_category: str,
        metric_name: str,
        metric_value: float,
        dataset_name: str,
        measured_at: datetime,
        higher_is_better: bool = True,
        metric_unit: str | None = None,
        source_url: str | None = None,
        notes: str | None = None,
    ) -> CompetitorBaseline:
        """Create or update a competitor baseline record.

        Args:
            tenant_id: Owning tenant UUID.
            competitor_name: Competitor identifier (gretel | mostly_ai | tonic).
            metric_category: fidelity | privacy | speed.
            metric_name: Specific metric name.
            metric_value: Baseline numeric value.
            dataset_name: Dataset on which baseline was measured.
            measured_at: Timestamp of measurement.
            higher_is_better: True if higher values are better.
            metric_unit: Unit of measurement.
            source_url: Optional source publication URL.
            notes: Optional additional context.

        Returns:
            Created or updated CompetitorBaseline.

        Raises:
            ConflictError: If competitor_name or metric_category is invalid.
        """
        if competitor_name not in VALID_COMPETITORS:
            raise ConflictError(
                message=f"Invalid competitor '{competitor_name}'. Valid: {VALID_COMPETITORS}",
                error_code=ErrorCode.INVALID_OPERATION,
            )

        if metric_category not in VALID_METRIC_CATEGORIES:
            raise ConflictError(
                message=f"Invalid metric_category '{metric_category}'. Valid: {VALID_METRIC_CATEGORIES}",
                error_code=ErrorCode.INVALID_OPERATION,
            )

        baseline = await self._baselines.upsert(
            tenant_id=tenant_id,
            competitor_name=competitor_name,
            metric_category=metric_category,
            metric_name=metric_name,
            metric_value=metric_value,
            dataset_name=dataset_name,
            measured_at=measured_at,
            higher_is_better=higher_is_better,
            metric_unit=metric_unit,
            source_url=source_url,
            notes=notes,
        )

        logger.info(
            "Competitor baseline upserted",
            competitor=competitor_name,
            metric=metric_name,
            dataset=dataset_name,
            value=metric_value,
        )

        return baseline

    async def list_baselines(
        self,
        tenant_id: uuid.UUID,
        competitor_name: str | None = None,
        active_only: bool = True,
    ) -> list[CompetitorBaseline]:
        """List competitor baselines, optionally filtered by competitor.

        Args:
            tenant_id: Requesting tenant.
            competitor_name: Optional competitor filter.
            active_only: If True, exclude soft-deleted baselines.

        Returns:
            List of CompetitorBaseline instances.

        Raises:
            ConflictError: If competitor_name is invalid.
        """
        if competitor_name is not None and competitor_name not in VALID_COMPETITORS:
            raise ConflictError(
                message=f"Invalid competitor '{competitor_name}'. Valid: {VALID_COMPETITORS}",
                error_code=ErrorCode.INVALID_OPERATION,
            )

        if competitor_name is not None:
            return await self._baselines.list_by_competitor(
                competitor_name, tenant_id, active_only
            )
        return await self._baselines.list_all(tenant_id, active_only)


class RegressionService:
    """Detect performance regressions by comparing runs to historical baselines.

    Compares a new benchmark run against the most recently completed run
    of the same configuration to identify metric regressions beyond thresholds.
    """

    def __init__(
        self,
        run_repo: IBenchmarkRunRepository,
        metric_repo: IMetricResultRepository,
        regression_repo: IRegressionCheckRepository,
        event_publisher: EventPublisher,
        fidelity_threshold: float = 0.05,
        privacy_threshold: float = 0.03,
        speed_threshold_percent: float = 20.0,
    ) -> None:
        """Initialise with injected dependencies.

        Args:
            run_repo: BenchmarkRun persistence.
            metric_repo: MetricResult persistence.
            regression_repo: RegressionCheck persistence.
            event_publisher: Kafka event publisher.
            fidelity_threshold: Max allowed fidelity score drop.
            privacy_threshold: Max allowed privacy score drop.
            speed_threshold_percent: Max allowed speed decrease in percent.
        """
        self._runs = run_repo
        self._metrics = metric_repo
        self._regressions = regression_repo
        self._publisher = event_publisher
        self._fidelity_threshold = fidelity_threshold
        self._privacy_threshold = privacy_threshold
        self._speed_threshold_percent = speed_threshold_percent

    async def check_regression(
        self,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        ci_build_id: str | None = None,
        ci_commit_sha: str | None = None,
    ) -> RegressionCheck:
        """Check a completed benchmark run for performance regressions.

        Compares the run's metrics against the most recently completed run of
        the same configuration. Returns passed if no regressions detected,
        failed if regressions exceed thresholds, skipped if no baseline exists.

        Args:
            tenant_id: Requesting tenant.
            run_id: BenchmarkRun UUID to check.
            ci_build_id: Optional CI build identifier for traceability.
            ci_commit_sha: Optional git commit SHA for traceability.

        Returns:
            RegressionCheck with status and per-metric analysis.

        Raises:
            NotFoundError: If run not found or not in completed status.
            ConflictError: If run is not in completed status.
        """
        run = await self._runs.get_by_id(run_id, tenant_id)
        if run is None:
            raise NotFoundError(
                message=f"Benchmark run {run_id} not found.",
                error_code=ErrorCode.NOT_FOUND,
            )

        if run.status != "completed":
            raise ConflictError(
                message=(
                    f"Cannot check regression on run {run_id} with status='{run.status}'. "
                    "Only completed runs can be checked for regression."
                ),
                error_code=ErrorCode.INVALID_OPERATION,
            )

        baseline_run = await self._runs.get_latest_completed(tenant_id, run.config_name)
        checked_at = datetime.now(tz=timezone.utc)

        # If no previous run exists, skip the check
        if baseline_run is None or baseline_run.id == run_id:
            regression_check = await self._regressions.create(
                tenant_id=tenant_id,
                run_id=run_id,
                baseline_run_id=None,
                status="skipped",
                regressed_metrics=[],
                details={"reason": "No baseline run available for comparison."},
                checked_at=checked_at,
                ci_build_id=ci_build_id,
                ci_commit_sha=ci_commit_sha,
            )
            logger.info(
                "Regression check skipped — no baseline available",
                run_id=str(run_id),
            )
            return regression_check

        # Retrieve metrics for both runs
        current_metrics = await self._metrics.list_by_run(run_id, category=None)
        baseline_metrics = await self._metrics.list_by_run(baseline_run.id, category=None)

        # Build lookup: metric_name -> MetricResult for baseline
        baseline_lookup: dict[str, MetricResult] = {
            m.metric_name: m for m in baseline_metrics
        }

        regressed_metrics: list[str] = []
        details: dict[str, Any] = {}

        for metric in current_metrics:
            if metric.metric_name not in baseline_lookup:
                continue

            baseline_metric = baseline_lookup[metric.metric_name]
            current_value = metric.metric_value
            baseline_value = baseline_metric.metric_value

            threshold = self._get_threshold(metric.metric_category, metric.metric_name, baseline_value)
            delta = current_value - baseline_value

            # Determine if regression occurred (worse than threshold allows)
            if metric.higher_is_better:
                # Higher is better: regression = current significantly lower than baseline
                regressed = delta < -threshold
            else:
                # Lower is better (e.g., latency): regression = current significantly higher
                regressed = delta > threshold

            details[metric.metric_name] = {
                "category": metric.metric_category,
                "current_value": current_value,
                "baseline_value": baseline_value,
                "delta": round(delta, 6),
                "threshold": threshold,
                "higher_is_better": metric.higher_is_better,
                "passed": not regressed,
            }

            if regressed:
                regressed_metrics.append(metric.metric_name)

        status = "failed" if regressed_metrics else "passed"

        regression_check = await self._regressions.create(
            tenant_id=tenant_id,
            run_id=run_id,
            baseline_run_id=baseline_run.id,
            status=status,
            regressed_metrics=regressed_metrics,
            details=details,
            checked_at=checked_at,
            ci_build_id=ci_build_id,
            ci_commit_sha=ci_commit_sha,
        )

        await self._publisher.publish(
            Topics.BENCHMARK,
            {
                "event_type": "benchmark.regression.checked",
                "tenant_id": str(tenant_id),
                "run_id": str(run_id),
                "baseline_run_id": str(baseline_run.id),
                "status": status,
                "regressed_metrics": regressed_metrics,
                "ci_build_id": ci_build_id,
                "ci_commit_sha": ci_commit_sha,
            },
        )

        log_fn = logger.warning if status == "failed" else logger.info
        log_fn(
            "Regression check complete",
            run_id=str(run_id),
            status=status,
            regressed_metrics=regressed_metrics,
        )

        return regression_check

    def _get_threshold(
        self, category: str, metric_name: str, baseline_value: float
    ) -> float:
        """Compute the regression threshold for a given metric.

        Args:
            category: Metric category (fidelity | privacy | speed).
            metric_name: Specific metric name.
            baseline_value: Baseline value used for percentage-based thresholds.

        Returns:
            Absolute threshold value for the regression check.
        """
        if category == "fidelity":
            return self._fidelity_threshold
        if category == "privacy":
            return self._privacy_threshold
        if category == "speed":
            # Speed uses percentage-based threshold relative to baseline
            return abs(baseline_value) * (self._speed_threshold_percent / 100.0)
        return 0.05


class ReportGeneratorService:
    """Generate structured benchmark reports from completed runs.

    Assembles Pareto curves, competitor comparisons, and trend analysis
    from persisted metric results into publishable report artifacts.
    """

    def __init__(
        self,
        run_repo: IBenchmarkRunRepository,
        metric_repo: IMetricResultRepository,
        baseline_repo: ICompetitorBaselineRepository,
        regression_repo: IRegressionCheckRepository,
        event_publisher: EventPublisher,
    ) -> None:
        """Initialise with injected dependencies.

        Args:
            run_repo: BenchmarkRun persistence.
            metric_repo: MetricResult persistence.
            baseline_repo: CompetitorBaseline persistence.
            regression_repo: RegressionCheck persistence.
            event_publisher: Kafka event publisher.
        """
        self._runs = run_repo
        self._metrics = metric_repo
        self._baselines = baseline_repo
        self._regressions = regression_repo
        self._publisher = event_publisher

    async def generate_report(
        self,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        include_competitor_comparison: bool = True,
        include_regression_check: bool = True,
        formats: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate a comprehensive benchmark report for a completed run.

        Assembles metric summaries, Pareto curve data, competitor comparisons,
        and regression check results into a structured report.

        Args:
            tenant_id: Requesting tenant.
            run_id: BenchmarkRun UUID to generate report for.
            include_competitor_comparison: Include Gretel/MOSTLY AI/Tonic comparison.
            include_regression_check: Include regression analysis vs last run.
            formats: Output formats (json | html | markdown). Defaults to ["json"].

        Returns:
            Report dict with sections for summary, metrics, comparisons, and regression.

        Raises:
            NotFoundError: If run not found.
            ConflictError: If run is not completed.
        """
        run = await self._runs.get_by_id(run_id, tenant_id)
        if run is None:
            raise NotFoundError(
                message=f"Benchmark run {run_id} not found.",
                error_code=ErrorCode.NOT_FOUND,
            )

        if run.status != "completed":
            raise ConflictError(
                message=(
                    f"Cannot generate report for run {run_id} with status='{run.status}'. "
                    "Only completed runs can produce reports."
                ),
                error_code=ErrorCode.INVALID_OPERATION,
            )

        report_formats = formats or ["json"]
        metric_summary = await self._metrics.get_summary(run_id, tenant_id)
        all_metrics = await self._metrics.list_by_run(run_id, category=None)

        report: dict[str, Any] = {
            "run_id": str(run_id),
            "name": run.name,
            "config_name": run.config_name,
            "dataset_name": run.dataset_name,
            "aumos_version": run.aumos_version,
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "duration_seconds": run.duration_seconds,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "formats": report_formats,
            "summary": metric_summary,
            "metrics": {
                "fidelity": [
                    {
                        "name": m.metric_name,
                        "value": m.metric_value,
                        "unit": m.metric_unit,
                        "higher_is_better": m.higher_is_better,
                    }
                    for m in all_metrics
                    if m.metric_category == "fidelity"
                ],
                "privacy": [
                    {
                        "name": m.metric_name,
                        "value": m.metric_value,
                        "unit": m.metric_unit,
                        "higher_is_better": m.higher_is_better,
                    }
                    for m in all_metrics
                    if m.metric_category == "privacy"
                ],
                "speed": [
                    {
                        "name": m.metric_name,
                        "value": m.metric_value,
                        "unit": m.metric_unit,
                        "higher_is_better": m.higher_is_better,
                    }
                    for m in all_metrics
                    if m.metric_category == "speed"
                ],
            },
        }

        if include_competitor_comparison:
            report["competitor_comparison"] = await self._build_competitor_comparison(
                tenant_id, run.dataset_name, all_metrics
            )

        if include_regression_check:
            regression = await self._regressions.get_by_run(run_id, tenant_id)
            report["regression"] = {
                "status": regression.status if regression else "not_run",
                "regressed_metrics": regression.regressed_metrics if regression else [],
                "details": regression.details if regression else {},
            }

        await self._publisher.publish(
            Topics.BENCHMARK,
            {
                "event_type": "benchmark.report.generated",
                "tenant_id": str(tenant_id),
                "run_id": str(run_id),
                "formats": report_formats,
            },
        )

        logger.info(
            "Benchmark report generated",
            run_id=str(run_id),
            formats=report_formats,
        )

        return report

    async def _build_competitor_comparison(  # noqa: C901
        self,
        tenant_id: uuid.UUID,
        dataset_name: str,
        current_metrics: list[MetricResult],
    ) -> dict[str, Any]:
        """Build a competitor comparison section for a report.

        Args:
            tenant_id: Requesting tenant.
            dataset_name: Dataset name to match baselines against.
            current_metrics: AumOS metric results from the current run.

        Returns:
            Dict with per-competitor, per-metric comparison data.
        """
        all_baselines = await self._baselines.list_all(tenant_id, active_only=True)

        # Filter baselines to matching dataset
        dataset_baselines = [
            b for b in all_baselines if b.dataset_name == dataset_name
        ]

        # Build lookup: (competitor, metric_name) -> baseline_value
        baseline_lookup: dict[tuple[str, str], float] = {
            (b.competitor_name, b.metric_name): b.metric_value
            for b in dataset_baselines
        }

        comparison: dict[str, Any] = {}
        for metric in current_metrics:
            metric_comparison: dict[str, Any] = {
                "aumos": metric.metric_value,
                "higher_is_better": metric.higher_is_better,
                "unit": metric.metric_unit,
            }

            for competitor in VALID_COMPETITORS:
                key = (competitor, metric.metric_name)
                if key in baseline_lookup:
                    competitor_value = baseline_lookup[key]
                    metric_comparison[competitor] = competitor_value
                    delta = metric.metric_value - competitor_value
                    advantage = delta > 0 if metric.higher_is_better else delta < 0
                    metric_comparison[f"{competitor}_delta"] = round(delta, 6)
                    metric_comparison[f"{competitor}_advantage"] = advantage

            comparison[metric.metric_name] = metric_comparison

        return comparison


class DomainBenchmarkService:
    """Orchestrate domain-specific benchmark adapters for latency, throughput, cost, fidelity,
    privacy, scalability, comparison reporting, and GPU profiling.

    This service acts as a facade over the specialised benchmark adapters,
    routing benchmark requests to the appropriate adapter based on benchmark type
    and assembling multi-adapter reports.
    """

    def __init__(
        self,
        latency_benchmark: ILatencyBenchmark,
        throughput_benchmark: IThroughputBenchmark,
        cost_benchmark: ICostBenchmark,
        fidelity_benchmark: IFidelityBenchmark,
        privacy_benchmark: IPrivacyBenchmark,
        scalability_benchmark: IScalabilityBenchmark,
        comparison_reporter: IComparisonReporter,
        gpu_benchmark: IGPUBenchmark,
        event_publisher: EventPublisher,
    ) -> None:
        """Initialise with all injected domain benchmark adapters.

        Args:
            latency_benchmark: Latency measurement adapter.
            throughput_benchmark: Throughput measurement adapter.
            cost_benchmark: Cost measurement adapter.
            fidelity_benchmark: Fidelity quality benchmark adapter.
            privacy_benchmark: Privacy guarantee benchmark adapter.
            scalability_benchmark: Scalability testing adapter.
            comparison_reporter: Cross-version/provider comparison adapter.
            gpu_benchmark: GPU efficiency profiling adapter.
            event_publisher: Kafka event publisher.
        """
        self._latency = latency_benchmark
        self._throughput = throughput_benchmark
        self._cost = cost_benchmark
        self._fidelity = fidelity_benchmark
        self._privacy = privacy_benchmark
        self._scalability = scalability_benchmark
        self._comparison = comparison_reporter
        self._gpu = gpu_benchmark
        self._publisher = event_publisher

    async def run_latency_benchmark(
        self,
        run_id: uuid.UUID,
        endpoint_url: str,
        sample_count: int = 50,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a latency benchmark for a single endpoint and publish event.

        Args:
            run_id: BenchmarkRun UUID.
            endpoint_url: Full URL of the target endpoint.
            sample_count: Number of measurement samples.
            method: HTTP method.
            payload: Optional JSON body.

        Returns:
            Latency report dict.
        """
        measurement = await self._latency.measure_endpoint(
            run_id=run_id,
            endpoint_url=endpoint_url,
            method=method,
            payload=payload,
            headers=None,
            sample_count=sample_count,
        )
        report = self._latency.generate_latency_report(
            run_id=run_id,
            measurements=[measurement],
            comparison=None,
        )

        await self._publisher.publish(
            Topics.BENCHMARK,
            {
                "event_type": "benchmark.latency.completed",
                "run_id": str(run_id),
                "endpoint_url": endpoint_url,
                "p95_ms": measurement.get("percentiles", {}).get("p95"),
            },
        )

        logger.info(
            "Latency benchmark completed",
            run_id=str(run_id),
            endpoint=endpoint_url,
            p95_ms=measurement.get("percentiles", {}).get("p95"),
        )

        return report

    async def run_throughput_benchmark(
        self,
        run_id: uuid.UUID,
        endpoint_url: str,
        max_concurrency: int = 50,
        method: str = "GET",
    ) -> dict[str, Any]:
        """Run a throughput benchmark and publish event.

        Args:
            run_id: BenchmarkRun UUID.
            endpoint_url: Full URL of the target endpoint.
            max_concurrency: Maximum concurrent connections to test.
            method: HTTP method.

        Returns:
            Throughput report dict.
        """
        measurement = await self._throughput.measure_max_rps(
            run_id=run_id,
            endpoint_url=endpoint_url,
            method=method,
            payload=None,
            headers=None,
            max_concurrency=max_concurrency,
        )
        report = self._throughput.generate_throughput_report(
            run_id=run_id,
            measurements=[measurement],
            version_comparisons=None,
        )

        await self._publisher.publish(
            Topics.BENCHMARK,
            {
                "event_type": "benchmark.throughput.completed",
                "run_id": str(run_id),
                "endpoint_url": endpoint_url,
                "max_rps": measurement.get("max_rps"),
            },
        )

        logger.info(
            "Throughput benchmark completed",
            run_id=str(run_id),
            endpoint=endpoint_url,
            max_rps=measurement.get("max_rps"),
        )

        return report

    async def run_scalability_benchmark(
        self,
        run_id: uuid.UUID,
        tenant_id: uuid.UUID,
        dataset_name: str,
        base_row_count: int = 1000,
    ) -> dict[str, Any]:
        """Run a full scalability benchmark and publish event.

        Args:
            run_id: BenchmarkRun UUID.
            tenant_id: Owning tenant UUID.
            dataset_name: Dataset name for generation.
            base_row_count: Baseline row count at 1x scale.

        Returns:
            Scalability report dict.
        """
        linear_test = await self._scalability.run_linear_scalability_test(
            run_id=run_id,
            dataset_name=dataset_name,
            base_row_count=base_row_count,
            scale_multipliers=None,
        )
        report = self._scalability.generate_scalability_report(
            run_id=run_id,
            linear_test=linear_test,
            horizontal_test=None,
            isolation_test=None,
            bottlenecks=None,
        )

        await self._publisher.publish(
            Topics.BENCHMARK,
            {
                "event_type": "benchmark.scalability.completed",
                "tenant_id": str(tenant_id),
                "run_id": str(run_id),
                "is_linear_scalable": linear_test.get("is_linear_scalable"),
                "scaling_ceiling": linear_test.get("scaling_ceiling_multiplier"),
            },
        )

        logger.info(
            "Scalability benchmark completed",
            run_id=str(run_id),
            is_linear=linear_test.get("is_linear_scalable"),
        )

        return report

    async def generate_comparison_report(
        self,
        run_id: uuid.UUID,
        tenant_id: uuid.UUID,
        current_run: dict[str, Any],
        baseline_run: dict[str, Any],
        output_format: str = "json",
    ) -> str:
        """Generate and export a cross-version comparison report.

        Args:
            run_id: Current BenchmarkRun UUID.
            tenant_id: Owning tenant UUID.
            current_run: Full report dict for the current run.
            baseline_run: Full report dict for the baseline run.
            output_format: Output format (json | html | markdown).

        Returns:
            Serialized report string in the requested format.
        """
        comparison = self._comparison.compare_versions(
            run_id=run_id,
            current_run=current_run,
            baseline_run=baseline_run,
            metric_categories=None,
        )
        exported = self._comparison.export_report(comparison, output_format)

        await self._publisher.publish(
            Topics.BENCHMARK,
            {
                "event_type": "benchmark.comparison_report.generated",
                "tenant_id": str(tenant_id),
                "run_id": str(run_id),
                "format": output_format,
                "regression_count": len(comparison.get("regressions", [])),
            },
        )

        logger.info(
            "Comparison report generated",
            run_id=str(run_id),
            format=output_format,
            regressions=len(comparison.get("regressions", [])),
        )

        return exported
