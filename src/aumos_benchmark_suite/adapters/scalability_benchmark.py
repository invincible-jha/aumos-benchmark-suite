"""Scalability benchmark adapter for the AumOS Benchmark Suite.

Tests linear scalability from 1x through 100x data volumes, horizontal
scaling efficiency, tenant isolation under load, and bottleneck identification.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from aumos_common.observability import get_logger

logger = get_logger(__name__)

# Default scale multipliers (1x, 10x, 100x data)
_DEFAULT_SCALE_MULTIPLIERS: list[int] = [1, 2, 5, 10, 25, 50, 100]

# Ideal linear scaling factor: 1.0 = perfectly linear
_LINEAR_EFFICIENCY_FLOOR: float = 0.70


@dataclass
class ScalePoint:
    """A single measurement point on the scalability curve.

    Attributes:
        scale_multiplier: Relative data size multiplier (1 = baseline).
        row_count: Actual number of rows processed at this scale.
        duration_seconds: Wall-clock time for the operation.
        throughput_rows_per_second: Derived throughput metric.
        scaling_efficiency: Ratio of actual vs ideal linear throughput.
        memory_mb: Peak memory utilization in megabytes.
        error_count: Number of errors encountered during the run.
        additional_data: Supplementary measurement metadata.
    """

    scale_multiplier: int
    row_count: int
    duration_seconds: float
    throughput_rows_per_second: float
    scaling_efficiency: float
    memory_mb: float = 0.0
    error_count: int = 0
    additional_data: dict[str, Any] = field(default_factory=dict)


class ScalabilityBenchmark:
    """Tests platform scalability from small to large data volumes.

    Drives the system at increasing data scales and measures whether throughput
    scales linearly. Detects the point at which scaling efficiency drops below
    acceptable thresholds — the scalability ceiling.
    """

    def __init__(
        self,
        tabular_engine_url: str,
        http_timeout: float = 300.0,
        linear_efficiency_floor: float = _LINEAR_EFFICIENCY_FLOOR,
    ) -> None:
        """Initialise with upstream service URL and efficiency threshold.

        Args:
            tabular_engine_url: Base URL for aumos-tabular-engine service.
            http_timeout: HTTP call timeout in seconds (long for large data).
            linear_efficiency_floor: Minimum acceptable scaling efficiency (0–1).
        """
        self._tabular_url = tabular_engine_url
        self._http_timeout = http_timeout
        self._efficiency_floor = linear_efficiency_floor

    async def run_linear_scalability_test(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        base_row_count: int = 1000,
        scale_multipliers: list[int] | None = None,
    ) -> dict[str, Any]:
        """Test linear scalability by generating data at increasing volumes.

        Generates synthetic data at each scale multiplier level and computes
        scaling efficiency relative to the baseline (1x) throughput.

        Args:
            run_id: BenchmarkRun UUID for logging.
            dataset_name: Dataset name to use for generation.
            base_row_count: Number of rows at the 1x baseline.
            scale_multipliers: List of scale multipliers to test.

        Returns:
            Dict with scale_curve list, scaling_ceiling, is_linear_scalable.
        """
        multipliers = scale_multipliers or _DEFAULT_SCALE_MULTIPLIERS
        logger.info(
            "Running linear scalability test",
            run_id=str(run_id),
            dataset=dataset_name,
            base_rows=base_row_count,
            scale_points=len(multipliers),
        )

        scale_points: list[ScalePoint] = []
        baseline_rps: float | None = None

        for multiplier in sorted(multipliers):
            row_count = base_row_count * multiplier
            scale_point = await self._measure_at_scale(
                run_id=run_id,
                dataset_name=dataset_name,
                row_count=row_count,
                scale_multiplier=multiplier,
            )

            if multiplier == 1 or baseline_rps is None:
                baseline_rps = scale_point.throughput_rows_per_second

            # Scaling efficiency: actual_rps / (baseline_rps * multiplier)
            ideal_rps = baseline_rps * multiplier if baseline_rps else 1.0
            scale_point.scaling_efficiency = round(
                scale_point.throughput_rows_per_second / max(ideal_rps, 0.001), 4
            )

            scale_points.append(scale_point)
            logger.info(
                "Scale point measured",
                run_id=str(run_id),
                multiplier=multiplier,
                rows=row_count,
                throughput=scale_point.throughput_rows_per_second,
                efficiency=scale_point.scaling_efficiency,
            )

        scaling_ceiling = self._detect_ceiling(scale_points)
        is_linear = all(
            sp.scaling_efficiency >= self._efficiency_floor for sp in scale_points
        )

        return {
            "run_id": str(run_id),
            "dataset_name": dataset_name,
            "base_row_count": base_row_count,
            "scale_curve": [self._scale_point_to_dict(sp) for sp in scale_points],
            "baseline_rps": round(baseline_rps or 0.0, 2),
            "scaling_ceiling_multiplier": scaling_ceiling,
            "is_linear_scalable": is_linear,
            "linear_efficiency_floor": self._efficiency_floor,
        }

    async def test_horizontal_scaling_efficiency(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        instance_counts: list[int],
        row_count: int = 10000,
    ) -> dict[str, Any]:
        """Measure throughput improvements as horizontal instances are added.

        Args:
            run_id: BenchmarkRun UUID.
            dataset_name: Dataset name.
            instance_counts: List of instance counts to test (e.g., [1, 2, 4, 8]).
            row_count: Total rows to distribute across instances.

        Returns:
            Horizontal scaling curve with efficiency per instance count.
        """
        logger.info(
            "Testing horizontal scaling efficiency",
            run_id=str(run_id),
            dataset=dataset_name,
            instance_counts=instance_counts,
        )

        curve_points: list[dict[str, Any]] = []
        single_instance_rps: float | None = None

        for instance_count in sorted(instance_counts):
            rows_per_instance = max(row_count // instance_count, 1)

            tasks = [
                self._measure_at_scale(
                    run_id=run_id,
                    dataset_name=dataset_name,
                    row_count=rows_per_instance,
                    scale_multiplier=1,
                )
                for _ in range(instance_count)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            valid_results = [r for r in results if isinstance(r, ScalePoint)]
            if not valid_results:
                continue

            combined_rps = sum(r.throughput_rows_per_second for r in valid_results)
            if instance_count == 1 or single_instance_rps is None:
                single_instance_rps = combined_rps

            efficiency = combined_rps / max(
                (single_instance_rps or 1.0) * instance_count, 0.001
            )

            curve_points.append({
                "instance_count": instance_count,
                "combined_rps": round(combined_rps, 2),
                "efficiency": round(efficiency, 4),
                "ideal_rps": round((single_instance_rps or 0.0) * instance_count, 2),
                "error_count": sum(1 for r in results if isinstance(r, Exception)),
            })

        return {
            "run_id": str(run_id),
            "dataset_name": dataset_name,
            "row_count": row_count,
            "horizontal_scaling_curve": curve_points,
            "single_instance_rps": round(single_instance_rps or 0.0, 2),
            "is_horizontally_scalable": all(
                p["efficiency"] >= self._efficiency_floor for p in curve_points
            ),
        }

    async def test_tenant_isolation_under_load(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        tenant_count: int = 10,
        rows_per_tenant: int = 1000,
    ) -> dict[str, Any]:
        """Verify that concurrent tenant loads do not degrade per-tenant performance.

        Simulates multiple tenants generating data simultaneously and measures
        whether any tenant's throughput is significantly impacted by others.

        Args:
            run_id: BenchmarkRun UUID.
            dataset_name: Dataset name.
            tenant_count: Number of concurrent simulated tenants.
            rows_per_tenant: Rows each tenant generates.

        Returns:
            Isolation test results with per-tenant throughput and fairness score.
        """
        logger.info(
            "Testing tenant isolation under load",
            run_id=str(run_id),
            tenant_count=tenant_count,
        )

        tasks = [
            self._measure_at_scale(
                run_id=run_id,
                dataset_name=dataset_name,
                row_count=rows_per_tenant,
                scale_multiplier=1,
                tenant_label=f"tenant_{i}",
            )
            for i in range(tenant_count)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        tenant_throughputs: list[float] = [
            r.throughput_rows_per_second
            for r in results
            if isinstance(r, ScalePoint)
        ]

        if not tenant_throughputs:
            return {
                "run_id": str(run_id),
                "tenant_count": tenant_count,
                "error": "All tenant measurements failed",
            }

        import statistics as stats_module

        mean_throughput = stats_module.mean(tenant_throughputs)
        stddev_throughput = stats_module.stdev(tenant_throughputs) if len(tenant_throughputs) > 1 else 0.0
        cv = (stddev_throughput / max(mean_throughput, 0.001)) * 100.0
        min_throughput = min(tenant_throughputs)
        fairness_score = min_throughput / max(mean_throughput, 0.001)

        return {
            "run_id": str(run_id),
            "tenant_count": tenant_count,
            "rows_per_tenant": rows_per_tenant,
            "tenant_throughputs_rps": [round(t, 2) for t in tenant_throughputs],
            "mean_throughput_rps": round(mean_throughput, 2),
            "stddev_throughput_rps": round(stddev_throughput, 2),
            "coefficient_of_variation_pct": round(cv, 2),
            "fairness_score": round(fairness_score, 4),
            "isolation_passed": fairness_score >= 0.80,  # Bottom tenant >= 80% of mean
        }

    def identify_bottlenecks(
        self,
        scale_curve: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Identify scale points where efficiency drops sharply.

        Args:
            scale_curve: List of scale point dicts (from run_linear_scalability_test).

        Returns:
            List of bottleneck dicts with scale_multiplier, efficiency_drop, and cause hint.
        """
        bottlenecks: list[dict[str, Any]] = []
        previous_efficiency: float | None = None

        for point in scale_curve:
            current_efficiency = point.get("scaling_efficiency", 1.0)
            if previous_efficiency is not None:
                drop = previous_efficiency - current_efficiency
                if drop > 0.15:  # More than 15% efficiency drop between levels
                    bottlenecks.append({
                        "scale_multiplier": point.get("scale_multiplier"),
                        "efficiency": current_efficiency,
                        "efficiency_drop": round(drop, 4),
                        "cause_hint": self._classify_bottleneck(point),
                    })
            previous_efficiency = current_efficiency

        return bottlenecks

    def generate_scalability_report(
        self,
        run_id: uuid.UUID,
        linear_test: dict[str, Any],
        horizontal_test: dict[str, Any] | None = None,
        isolation_test: dict[str, Any] | None = None,
        bottlenecks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Generate a structured scalability benchmark report.

        Args:
            run_id: BenchmarkRun UUID.
            linear_test: Output from run_linear_scalability_test.
            horizontal_test: Optional output from test_horizontal_scaling_efficiency.
            isolation_test: Optional output from test_tenant_isolation_under_load.
            bottlenecks: Optional list from identify_bottlenecks.

        Returns:
            Structured scalability report.
        """
        report: dict[str, Any] = {
            "run_id": str(run_id),
            "report_type": "scalability",
            "linear_scalability": linear_test,
            "is_linear_scalable": linear_test.get("is_linear_scalable", False),
            "scaling_ceiling_multiplier": linear_test.get("scaling_ceiling_multiplier"),
        }

        if horizontal_test:
            report["horizontal_scaling"] = horizontal_test
        if isolation_test:
            report["tenant_isolation"] = isolation_test
        if bottlenecks:
            report["bottlenecks"] = bottlenecks
            report["bottleneck_count"] = len(bottlenecks)

        logger.info(
            "Scalability report generated",
            run_id=str(run_id),
            is_linear=linear_test.get("is_linear_scalable"),
            ceiling=linear_test.get("scaling_ceiling_multiplier"),
        )

        return report

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _measure_at_scale(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        row_count: int,
        scale_multiplier: int,
        tenant_label: str | None = None,
    ) -> ScalePoint:
        """Invoke the tabular engine and measure generation throughput.

        Args:
            run_id: BenchmarkRun UUID.
            dataset_name: Dataset name.
            row_count: Number of rows to generate.
            scale_multiplier: Current scale factor.
            tenant_label: Optional tenant label for isolation tests.

        Returns:
            ScalePoint with throughput and efficiency measurements.
        """
        start = time.monotonic()
        error_count = 0

        try:
            async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                payload: dict[str, Any] = {
                    "dataset_name": dataset_name,
                    "rows": row_count,
                    "run_id": str(run_id),
                }
                if tenant_label:
                    payload["tenant_label"] = tenant_label

                await client.post(
                    f"{self._tabular_url}/api/v1/generate/probe",
                    json=payload,
                )
        except Exception as exc:
            logger.warning(
                "Tabular engine call failed at scale measurement",
                run_id=str(run_id),
                row_count=row_count,
                error=str(exc),
            )
            error_count = 1

        duration = max(time.monotonic() - start, 0.001)
        throughput = row_count / duration

        return ScalePoint(
            scale_multiplier=scale_multiplier,
            row_count=row_count,
            duration_seconds=round(duration, 3),
            throughput_rows_per_second=round(throughput, 2),
            scaling_efficiency=1.0,  # Set by caller after baseline is known
            error_count=error_count,
            additional_data={"run_id": str(run_id), "tenant_label": tenant_label},
        )

    def _detect_ceiling(self, scale_points: list[ScalePoint]) -> int | None:
        """Detect the first scale multiplier where efficiency falls below the floor.

        Args:
            scale_points: Ordered list of ScalePoint measurements.

        Returns:
            Scale multiplier at the ceiling, or None if never reached.
        """
        for scale_point in scale_points:
            if scale_point.scaling_efficiency < self._efficiency_floor:
                return scale_point.scale_multiplier
        return None

    def _classify_bottleneck(self, point: dict[str, Any]) -> str:
        """Provide a hint about the likely bottleneck type at a scale point.

        Args:
            point: Scale point dict with memory_mb and efficiency fields.

        Returns:
            String hint describing the likely bottleneck category.
        """
        memory_mb = point.get("memory_mb", 0.0)
        if memory_mb > 8192:
            return "memory_pressure"
        if point.get("error_count", 0) > 0:
            return "service_errors"
        if point.get("scaling_efficiency", 1.0) < 0.5:
            return "compute_saturation"
        return "network_or_io_contention"

    def _scale_point_to_dict(self, scale_point: ScalePoint) -> dict[str, Any]:
        """Convert a ScalePoint dataclass to a plain dict.

        Args:
            scale_point: ScalePoint instance to convert.

        Returns:
            Plain dict representation.
        """
        return {
            "scale_multiplier": scale_point.scale_multiplier,
            "row_count": scale_point.row_count,
            "duration_seconds": scale_point.duration_seconds,
            "throughput_rows_per_second": scale_point.throughput_rows_per_second,
            "scaling_efficiency": scale_point.scaling_efficiency,
            "memory_mb": scale_point.memory_mb,
            "error_count": scale_point.error_count,
        }
