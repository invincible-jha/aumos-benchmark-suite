"""GPU benchmark adapter for the AumOS Benchmark Suite.

Profiles GPU utilization, inference/training throughput per GPU type,
memory usage, multi-GPU scaling efficiency, and cost-performance ratios.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from aumos_common.observability import get_logger

logger = get_logger(__name__)

# Known GPU types with hourly cost in USD (cloud on-demand pricing)
_GPU_COST_PER_HOUR_USD: dict[str, float] = {
    "a100-80gb": 3.20,
    "a100-40gb": 2.10,
    "v100-32gb": 2.48,
    "v100-16gb": 1.24,
    "t4": 0.53,
    "l4": 0.70,
    "h100-80gb": 9.80,
}

# Default utilization threshold below which GPU is considered underutilized
_UNDERUTILIZATION_THRESHOLD_PCT: float = 40.0


@dataclass
class GPUMeasurement:
    """A single GPU utilization and throughput measurement.

    Attributes:
        gpu_type: GPU model identifier (e.g., "a100-80gb").
        utilization_pct: Mean GPU compute utilization percentage.
        memory_used_gb: Peak GPU memory used in gigabytes.
        memory_total_gb: Total GPU memory capacity.
        throughput_rows_per_second: Inference throughput (rows/sec).
        duration_seconds: Measurement duration.
        gpu_count: Number of GPUs used in this measurement.
        training_samples_per_second: Training throughput (optional).
        additional_data: Supplementary measurement metadata.
    """

    gpu_type: str
    utilization_pct: float
    memory_used_gb: float
    memory_total_gb: float
    throughput_rows_per_second: float
    duration_seconds: float
    gpu_count: int = 1
    training_samples_per_second: float = 0.0
    additional_data: dict[str, Any] = field(default_factory=dict)


class GPUBenchmark:
    """Profiles GPU efficiency for AumOS synthetic data generation workloads.

    Measures utilization, inference throughput, memory efficiency, and
    multi-GPU scaling. Computes cost-performance ratios to help customers
    select the optimal GPU instance type for their workload.
    """

    def __init__(
        self,
        tabular_engine_url: str,
        http_timeout: float = 120.0,
        underutilization_threshold_pct: float = _UNDERUTILIZATION_THRESHOLD_PCT,
    ) -> None:
        """Initialise with tabular engine URL and thresholds.

        Args:
            tabular_engine_url: Base URL for aumos-tabular-engine service.
            http_timeout: HTTP call timeout in seconds.
            underutilization_threshold_pct: GPU util % below which it is underutilized.
        """
        self._tabular_url = tabular_engine_url
        self._http_timeout = http_timeout
        self._underutilization_threshold = underutilization_threshold_pct

    async def profile_gpu_utilization(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        gpu_type: str,
        row_count: int = 10000,
        sample_interval_seconds: float = 1.0,
    ) -> GPUMeasurement:
        """Profile GPU utilization while generating synthetic data.

        Triggers a generation job and concurrently polls GPU metrics
        to compute utilization statistics over the workload duration.

        Args:
            run_id: BenchmarkRun UUID for logging.
            dataset_name: Dataset name for generation.
            gpu_type: GPU model identifier for cost lookup.
            row_count: Number of synthetic rows to generate.
            sample_interval_seconds: GPU metrics polling interval.

        Returns:
            GPUMeasurement with utilization and throughput statistics.
        """
        logger.info(
            "Profiling GPU utilization",
            run_id=str(run_id),
            dataset=dataset_name,
            gpu_type=gpu_type,
            row_count=row_count,
        )

        utilization_samples: list[float] = []
        memory_samples: list[float] = []
        stop_event = asyncio.Event()

        async def poll_gpu_metrics() -> None:
            while not stop_event.is_set():
                metrics = await self._fetch_gpu_metrics(run_id, gpu_type)
                utilization_samples.append(metrics.get("utilization_pct", 0.0))
                memory_samples.append(metrics.get("memory_used_gb", 0.0))
                await asyncio.sleep(sample_interval_seconds)

        start = time.monotonic()
        poller_task = asyncio.create_task(poll_gpu_metrics())

        try:
            async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                await client.post(
                    f"{self._tabular_url}/api/v1/generate/probe",
                    json={
                        "dataset_name": dataset_name,
                        "rows": row_count,
                        "run_id": str(run_id),
                        "use_gpu": True,
                        "gpu_type": gpu_type,
                    },
                )
        except Exception as exc:
            logger.warning(
                "Tabular engine unavailable during GPU profile",
                run_id=str(run_id),
                error=str(exc),
            )
        finally:
            stop_event.set()
            await poller_task

        duration = max(time.monotonic() - start, 0.001)
        mean_utilization = sum(utilization_samples) / max(len(utilization_samples), 1)
        peak_memory = max(memory_samples) if memory_samples else 0.0

        gpu_memory_gb = self._get_gpu_memory_gb(gpu_type)
        throughput = row_count / duration

        return GPUMeasurement(
            gpu_type=gpu_type,
            utilization_pct=round(mean_utilization, 2),
            memory_used_gb=round(peak_memory, 2),
            memory_total_gb=gpu_memory_gb,
            throughput_rows_per_second=round(throughput, 2),
            duration_seconds=round(duration, 3),
            additional_data={
                "run_id": str(run_id),
                "dataset_name": dataset_name,
                "row_count": row_count,
                "utilization_sample_count": len(utilization_samples),
            },
        )

    async def benchmark_inference_throughput(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        gpu_types: list[str],
        row_count: int = 10000,
    ) -> dict[str, Any]:
        """Benchmark inference throughput across multiple GPU types.

        Args:
            run_id: BenchmarkRun UUID.
            dataset_name: Dataset name.
            gpu_types: List of GPU model identifiers to benchmark.
            row_count: Rows to generate per GPU type.

        Returns:
            Dict with per-GPU throughput and ranking.
        """
        logger.info(
            "Benchmarking inference throughput across GPU types",
            run_id=str(run_id),
            gpu_types=gpu_types,
            row_count=row_count,
        )

        measurements: dict[str, GPUMeasurement] = {}

        for gpu_type in gpu_types:
            measurement = await self.profile_gpu_utilization(
                run_id=run_id,
                dataset_name=dataset_name,
                gpu_type=gpu_type,
                row_count=row_count,
            )
            measurements[gpu_type] = measurement

        ranked = sorted(
            measurements.keys(),
            key=lambda g: measurements[g].throughput_rows_per_second,
            reverse=True,
        )

        return {
            "run_id": str(run_id),
            "dataset_name": dataset_name,
            "row_count": row_count,
            "gpu_results": {
                gpu: self._measurement_to_dict(m)
                for gpu, m in measurements.items()
            },
            "ranking": ranked,
            "best_gpu": ranked[0] if ranked else None,
            "best_throughput_rps": measurements[ranked[0]].throughput_rows_per_second if ranked else 0.0,
        }

    async def profile_memory_usage(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        gpu_type: str,
        row_counts: list[int],
    ) -> dict[str, Any]:
        """Profile GPU memory usage at increasing dataset sizes.

        Args:
            run_id: BenchmarkRun UUID.
            dataset_name: Dataset name.
            gpu_type: GPU model identifier.
            row_counts: List of row counts to test.

        Returns:
            Memory profile with per-size measurements and extrapolated capacity.
        """
        gpu_memory_gb = self._get_gpu_memory_gb(gpu_type)
        profile_points: list[dict[str, Any]] = []

        for row_count in sorted(row_counts):
            measurement = await self.profile_gpu_utilization(
                run_id=run_id,
                dataset_name=dataset_name,
                gpu_type=gpu_type,
                row_count=row_count,
            )

            memory_utilization_pct = (
                measurement.memory_used_gb / max(gpu_memory_gb, 0.001)
            ) * 100.0

            profile_points.append({
                "row_count": row_count,
                "memory_used_gb": measurement.memory_used_gb,
                "memory_utilization_pct": round(memory_utilization_pct, 2),
                "will_oom": memory_utilization_pct > 95.0,
            })

        # Extrapolate max rows before OOM
        max_safe_rows = self._extrapolate_max_rows(profile_points, gpu_memory_gb)

        return {
            "run_id": str(run_id),
            "dataset_name": dataset_name,
            "gpu_type": gpu_type,
            "gpu_memory_total_gb": gpu_memory_gb,
            "memory_profile": profile_points,
            "estimated_max_safe_rows": max_safe_rows,
        }

    async def test_multi_gpu_scaling(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        gpu_type: str,
        gpu_counts: list[int],
        row_count_per_gpu: int = 10000,
    ) -> dict[str, Any]:
        """Test throughput scaling efficiency with multiple GPUs.

        Args:
            run_id: BenchmarkRun UUID.
            dataset_name: Dataset name.
            gpu_type: GPU model identifier.
            gpu_counts: List of GPU counts to test (e.g., [1, 2, 4, 8]).
            row_count_per_gpu: Rows per GPU for each test.

        Returns:
            Multi-GPU scaling curve with efficiency per GPU count.
        """
        logger.info(
            "Testing multi-GPU scaling",
            run_id=str(run_id),
            gpu_type=gpu_type,
            gpu_counts=gpu_counts,
        )

        scaling_curve: list[dict[str, Any]] = []
        single_gpu_throughput: float | None = None

        for gpu_count in sorted(gpu_counts):
            total_rows = row_count_per_gpu * gpu_count

            # Simulate multi-GPU by running parallel measurements
            tasks = [
                self.profile_gpu_utilization(
                    run_id=run_id,
                    dataset_name=dataset_name,
                    gpu_type=gpu_type,
                    row_count=row_count_per_gpu,
                )
                for _ in range(gpu_count)
            ]
            gpu_results = await asyncio.gather(*tasks, return_exceptions=True)
            valid_results = [r for r in gpu_results if isinstance(r, GPUMeasurement)]

            combined_throughput = sum(r.throughput_rows_per_second for r in valid_results)

            if gpu_count == 1 or single_gpu_throughput is None:
                single_gpu_throughput = combined_throughput

            ideal_throughput = (single_gpu_throughput or 1.0) * gpu_count
            scaling_efficiency = combined_throughput / max(ideal_throughput, 0.001)

            scaling_curve.append({
                "gpu_count": gpu_count,
                "gpu_type": gpu_type,
                "combined_throughput_rps": round(combined_throughput, 2),
                "ideal_throughput_rps": round(ideal_throughput, 2),
                "scaling_efficiency": round(scaling_efficiency, 4),
                "total_rows": total_rows,
            })

        return {
            "run_id": str(run_id),
            "dataset_name": dataset_name,
            "gpu_type": gpu_type,
            "row_count_per_gpu": row_count_per_gpu,
            "scaling_curve": scaling_curve,
            "single_gpu_throughput_rps": round(single_gpu_throughput or 0.0, 2),
            "is_linearly_scalable": all(
                p["scaling_efficiency"] >= 0.75 for p in scaling_curve
            ),
        }

    def compute_cost_performance_ratio(
        self,
        measurement: GPUMeasurement,
    ) -> dict[str, Any]:
        """Compute the cost-performance ratio for a GPU measurement.

        A lower cost-performance ratio is better (more throughput per dollar).

        Args:
            measurement: GPUMeasurement from profile_gpu_utilization.

        Returns:
            Dict with hourly cost, rows_per_dollar, and cost_per_million_rows.
        """
        hourly_cost = _GPU_COST_PER_HOUR_USD.get(measurement.gpu_type, 3.0)
        cost_per_second = hourly_cost / 3600.0
        throughput = max(measurement.throughput_rows_per_second, 0.001)

        rows_per_dollar = throughput / cost_per_second
        cost_per_million_rows = (cost_per_second / throughput) * 1_000_000

        return {
            "gpu_type": measurement.gpu_type,
            "hourly_cost_usd": hourly_cost,
            "throughput_rps": measurement.throughput_rows_per_second,
            "rows_per_dollar": round(rows_per_dollar, 0),
            "cost_per_million_rows_usd": round(cost_per_million_rows, 4),
            "utilization_pct": measurement.utilization_pct,
            "is_underutilized": measurement.utilization_pct < self._underutilization_threshold,
        }

    def generate_gpu_report(
        self,
        run_id: uuid.UUID,
        measurements: list[GPUMeasurement],
        multi_gpu_results: dict[str, Any] | None = None,
        cost_ratios: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Generate a structured GPU benchmark report.

        Args:
            run_id: BenchmarkRun UUID.
            measurements: List of GPUMeasurement instances.
            multi_gpu_results: Optional multi-GPU scaling dict.
            cost_ratios: Optional list of cost-performance dicts.

        Returns:
            Structured GPU benchmark report.
        """
        best_gpu = max(
            measurements,
            key=lambda m: m.throughput_rows_per_second,
            default=None,
        )
        underutilized_gpus = [
            m.gpu_type
            for m in measurements
            if m.utilization_pct < self._underutilization_threshold
        ]

        report: dict[str, Any] = {
            "run_id": str(run_id),
            "report_type": "gpu_benchmark",
            "gpu_count": len(measurements),
            "best_gpu": best_gpu.gpu_type if best_gpu else None,
            "best_throughput_rps": best_gpu.throughput_rows_per_second if best_gpu else 0.0,
            "underutilized_gpus": underutilized_gpus,
            "underutilization_threshold_pct": self._underutilization_threshold,
            "measurements": [self._measurement_to_dict(m) for m in measurements],
        }

        if multi_gpu_results:
            report["multi_gpu_scaling"] = multi_gpu_results
        if cost_ratios:
            best_value_gpu = min(
                cost_ratios,
                key=lambda r: r.get("cost_per_million_rows_usd", float("inf")),
                default=None,
            )
            report["cost_performance"] = cost_ratios
            report["best_value_gpu"] = best_value_gpu.get("gpu_type") if best_value_gpu else None

        logger.info(
            "GPU benchmark report generated",
            run_id=str(run_id),
            gpu_count=len(measurements),
            best_gpu=report["best_gpu"],
        )

        return report

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _fetch_gpu_metrics(
        self,
        run_id: uuid.UUID,
        gpu_type: str,
    ) -> dict[str, float]:
        """Fetch current GPU utilization from the tabular engine metrics endpoint.

        Args:
            run_id: BenchmarkRun UUID.
            gpu_type: GPU model identifier.

        Returns:
            Dict with utilization_pct and memory_used_gb, or zeros on error.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{self._tabular_url}/api/v1/metrics/gpu",
                    params={"gpu_type": gpu_type, "run_id": str(run_id)},
                )
                if response.status_code == 200:
                    return response.json()
        except Exception:
            pass

        return {"utilization_pct": 0.0, "memory_used_gb": 0.0}

    def _get_gpu_memory_gb(self, gpu_type: str) -> float:
        """Look up total memory capacity for a GPU type.

        Args:
            gpu_type: GPU model identifier (e.g., "a100-80gb").

        Returns:
            Total memory in gigabytes.
        """
        memory_map: dict[str, float] = {
            "a100-80gb": 80.0,
            "a100-40gb": 40.0,
            "v100-32gb": 32.0,
            "v100-16gb": 16.0,
            "t4": 16.0,
            "l4": 24.0,
            "h100-80gb": 80.0,
        }
        return memory_map.get(gpu_type, 16.0)

    def _extrapolate_max_rows(
        self,
        profile_points: list[dict[str, Any]],
        gpu_memory_gb: float,
        oom_threshold_pct: float = 90.0,
    ) -> int | None:
        """Extrapolate the maximum row count before GPU OOM at 90% memory.

        Args:
            profile_points: Memory profile measurement points.
            gpu_memory_gb: Total GPU memory in GB.
            oom_threshold_pct: Memory utilization percentage treated as unsafe.

        Returns:
            Estimated maximum safe row count, or None if insufficient data.
        """
        if len(profile_points) < 2:
            return None

        # Fit a simple linear model: memory_gb = slope * row_count + intercept
        row_counts = [p["row_count"] for p in profile_points]
        memory_values = [p["memory_used_gb"] for p in profile_points]

        n = len(row_counts)
        mean_rows = sum(row_counts) / n
        mean_memory = sum(memory_values) / n

        slope_numerator = sum(
            (row_counts[i] - mean_rows) * (memory_values[i] - mean_memory)
            for i in range(n)
        )
        slope_denominator = sum((row_counts[i] - mean_rows) ** 2 for i in range(n))

        if abs(slope_denominator) < 1e-10:
            return None

        slope = slope_numerator / slope_denominator
        intercept = mean_memory - slope * mean_rows

        # Solve for row_count when memory_gb = gpu_memory_gb * (oom_threshold_pct / 100)
        target_memory = gpu_memory_gb * (oom_threshold_pct / 100.0)
        if abs(slope) < 1e-10:
            return None

        max_rows = int((target_memory - intercept) / slope)
        return max(max_rows, 0)

    def _measurement_to_dict(self, measurement: GPUMeasurement) -> dict[str, Any]:
        """Convert a GPUMeasurement dataclass to a plain dict.

        Args:
            measurement: GPUMeasurement instance.

        Returns:
            Plain dict representation.
        """
        return {
            "gpu_type": measurement.gpu_type,
            "utilization_pct": measurement.utilization_pct,
            "memory_used_gb": measurement.memory_used_gb,
            "memory_total_gb": measurement.memory_total_gb,
            "memory_utilization_pct": round(
                measurement.memory_used_gb / max(measurement.memory_total_gb, 0.001) * 100, 2
            ),
            "throughput_rows_per_second": measurement.throughput_rows_per_second,
            "duration_seconds": measurement.duration_seconds,
            "gpu_count": measurement.gpu_count,
            "training_samples_per_second": measurement.training_samples_per_second,
        }
