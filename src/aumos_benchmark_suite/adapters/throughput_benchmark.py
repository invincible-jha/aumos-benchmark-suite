"""Throughput benchmark adapter for the AumOS Benchmark Suite.

Measures maximum requests per second per endpoint, plots throughput vs
concurrency curves, detects saturation, and identifies resource utilization
at peak throughput.
"""

import asyncio
import time
import uuid
from typing import Any

import httpx

from aumos_common.observability import get_logger

logger = get_logger(__name__)

# How long to sustain load at each concurrency level before measuring
_RAMP_DURATION_SECONDS: float = 5.0

# Minimum sample window for throughput measurement
_MEASUREMENT_WINDOW_SECONDS: float = 10.0


class ThroughputBenchmark:
    """Measures request throughput and saturation characteristics for endpoints.

    Drives increasing concurrency levels against a target endpoint, measuring
    achieved requests-per-second at each level. Plots the throughput vs
    concurrency curve to identify the saturation point where adding more
    concurrency no longer improves throughput.
    """

    def __init__(
        self,
        http_timeout: float = 30.0,
        measurement_window_seconds: float = _MEASUREMENT_WINDOW_SECONDS,
        ramp_duration_seconds: float = _RAMP_DURATION_SECONDS,
    ) -> None:
        """Initialise with measurement parameters.

        Args:
            http_timeout: Per-request HTTP timeout in seconds.
            measurement_window_seconds: Duration of the measurement window per level.
            ramp_duration_seconds: Warm-up ramp period before measurement starts.
        """
        self._http_timeout = http_timeout
        self._measurement_window = measurement_window_seconds
        self._ramp_duration = ramp_duration_seconds

    async def measure_max_rps(
        self,
        run_id: uuid.UUID,
        endpoint_url: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        max_concurrency: int = 50,
    ) -> dict[str, Any]:
        """Find the maximum achievable requests per second for an endpoint.

        Ramps concurrency from 1 to max_concurrency, recording throughput
        at each level. Returns the peak RPS and the concurrency at which it
        was achieved.

        Args:
            run_id: BenchmarkRun UUID for correlation logging.
            endpoint_url: Full URL of the endpoint to probe.
            method: HTTP method.
            payload: Optional JSON body.
            headers: Optional extra request headers.
            max_concurrency: Maximum concurrent connections to use.

        Returns:
            Dict with max_rps, peak_concurrency, saturation_point, throughput_curve.
        """
        logger.info(
            "Starting max RPS measurement",
            run_id=str(run_id),
            endpoint=endpoint_url,
            max_concurrency=max_concurrency,
        )

        concurrency_steps = self._build_concurrency_steps(max_concurrency)
        throughput_curve: list[dict[str, Any]] = []
        peak_rps: float = 0.0
        peak_concurrency: int = 1
        saturation_concurrency: int | None = None

        for concurrency in concurrency_steps:
            rps, success_count, error_count = await self._measure_rps_at_concurrency(
                endpoint_url=endpoint_url,
                method=method,
                payload=payload,
                headers=headers,
                concurrency=concurrency,
            )

            throughput_curve.append({
                "concurrency": concurrency,
                "rps": round(rps, 2),
                "success_count": success_count,
                "error_count": error_count,
                "error_rate": round(
                    error_count / max(success_count + error_count, 1), 4
                ),
            })

            logger.info(
                "Throughput measurement point",
                concurrency=concurrency,
                rps=rps,
                error_count=error_count,
            )

            if rps > peak_rps:
                peak_rps = rps
                peak_concurrency = concurrency
            elif saturation_concurrency is None and rps < peak_rps * 0.95:
                # RPS dropped more than 5% from peak — saturation detected
                saturation_concurrency = concurrency

        return {
            "run_id": str(run_id),
            "endpoint_url": endpoint_url,
            "max_rps": round(peak_rps, 2),
            "peak_concurrency": peak_concurrency,
            "saturation_point": saturation_concurrency,
            "throughput_curve": throughput_curve,
            "measurement_window_seconds": self._measurement_window,
        }

    async def measure_throughput_curve(
        self,
        run_id: uuid.UUID,
        endpoint_url: str,
        concurrency_levels: list[int],
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Plot throughput vs concurrency for a specified set of concurrency levels.

        Args:
            run_id: BenchmarkRun UUID.
            endpoint_url: Full URL.
            concurrency_levels: Explicit concurrency levels to measure.
            method: HTTP method.
            payload: Optional JSON body.
            headers: Optional extra headers.

        Returns:
            Dict with throughput_curve list and saturation analysis.
        """
        logger.info(
            "Measuring throughput curve",
            run_id=str(run_id),
            endpoint=endpoint_url,
            levels=concurrency_levels,
        )

        curve_points: list[dict[str, Any]] = []
        peak_rps: float = 0.0
        saturation_point: int | None = None

        for concurrency in concurrency_levels:
            rps, success_count, error_count = await self._measure_rps_at_concurrency(
                endpoint_url=endpoint_url,
                method=method,
                payload=payload,
                headers=headers,
                concurrency=concurrency,
            )

            curve_points.append({
                "concurrency": concurrency,
                "rps": round(rps, 2),
                "success_count": success_count,
                "error_count": error_count,
            })

            if rps > peak_rps:
                peak_rps = rps
            elif saturation_point is None and rps < peak_rps * 0.95:
                saturation_point = concurrency

        return {
            "run_id": str(run_id),
            "endpoint_url": endpoint_url,
            "throughput_curve": curve_points,
            "peak_rps": round(peak_rps, 2),
            "saturation_point": saturation_point,
        }

    async def compare_versions(
        self,
        run_id: uuid.UUID,
        current_rps: float,
        baseline_rps: float,
        endpoint_url: str,
    ) -> dict[str, Any]:
        """Compare throughput between current and baseline versions.

        Args:
            run_id: BenchmarkRun UUID.
            current_rps: Current version max RPS.
            baseline_rps: Baseline version max RPS.
            endpoint_url: Endpoint under comparison.

        Returns:
            Comparison dict with delta, percent_change, and regression flag.
        """
        delta = current_rps - baseline_rps
        percent_change = (delta / max(baseline_rps, 0.001)) * 100.0
        regressed = percent_change < -10.0  # >10% throughput drop is a regression

        return {
            "run_id": str(run_id),
            "endpoint_url": endpoint_url,
            "current_rps": round(current_rps, 2),
            "baseline_rps": round(baseline_rps, 2),
            "delta_rps": round(delta, 2),
            "percent_change": round(percent_change, 2),
            "regressed": regressed,
            "regression_threshold_percent": -10.0,
        }

    def generate_throughput_report(
        self,
        run_id: uuid.UUID,
        measurements: list[dict[str, Any]],
        version_comparisons: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Generate a structured throughput report from collected measurements.

        Args:
            run_id: BenchmarkRun UUID.
            measurements: List of per-endpoint max RPS measurement dicts.
            version_comparisons: Optional list of version comparison dicts.

        Returns:
            Structured throughput report.
        """
        total_peak_rps = sum(m.get("max_rps", 0.0) for m in measurements)
        saturation_points = [
            m["saturation_point"]
            for m in measurements
            if m.get("saturation_point") is not None
        ]

        report: dict[str, Any] = {
            "run_id": str(run_id),
            "report_type": "throughput",
            "endpoint_count": len(measurements),
            "total_peak_rps": round(total_peak_rps, 2),
            "min_saturation_point": min(saturation_points) if saturation_points else None,
            "endpoints": [
                {
                    "endpoint_url": m.get("endpoint_url"),
                    "max_rps": m.get("max_rps"),
                    "peak_concurrency": m.get("peak_concurrency"),
                    "saturation_point": m.get("saturation_point"),
                }
                for m in measurements
            ],
        }

        if version_comparisons:
            report["version_comparisons"] = version_comparisons
            regressions = [c for c in version_comparisons if c.get("regressed")]
            report["regression_count"] = len(regressions)

        logger.info(
            "Throughput report generated",
            run_id=str(run_id),
            total_peak_rps=total_peak_rps,
        )

        return report

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _measure_rps_at_concurrency(
        self,
        endpoint_url: str,
        method: str,
        payload: dict[str, Any] | None,
        headers: dict[str, str] | None,
        concurrency: int,
    ) -> tuple[float, int, int]:
        """Drive constant concurrency for the measurement window and count completions.

        Args:
            endpoint_url: Target URL.
            method: HTTP method.
            payload: Optional JSON body.
            headers: Optional extra headers.
            concurrency: Number of concurrent in-flight requests.

        Returns:
            Tuple of (requests_per_second, success_count, error_count).
        """
        success_count: int = 0
        error_count: int = 0
        deadline = time.monotonic() + self._ramp_duration + self._measurement_window
        measurement_start = time.monotonic() + self._ramp_duration

        semaphore = asyncio.Semaphore(concurrency)
        lock = asyncio.Lock()
        results: list[bool] = []

        async def worker() -> None:
            async with semaphore:
                async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                    while time.monotonic() < deadline:
                        try:
                            await client.request(
                                method,
                                endpoint_url,
                                json=payload,
                                headers=headers or {},
                            )
                            if time.monotonic() >= measurement_start:
                                async with lock:
                                    results.append(True)
                        except Exception:
                            if time.monotonic() >= measurement_start:
                                async with lock:
                                    results.append(False)

        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        await asyncio.gather(*workers, return_exceptions=True)

        success_count = sum(1 for r in results if r)
        error_count = sum(1 for r in results if not r)
        actual_window = max(self._measurement_window, 0.001)
        rps = (success_count + error_count) / actual_window

        return rps, success_count, error_count

    def _build_concurrency_steps(self, max_concurrency: int) -> list[int]:
        """Build a list of concurrency levels to test up to max_concurrency.

        Args:
            max_concurrency: Upper bound for concurrency.

        Returns:
            Sorted list of concurrency levels (1, 2, 5, 10, 25, 50, ...).
        """
        steps = [1, 2, 5, 10, 25, 50, 100, 200]
        return [s for s in steps if s <= max_concurrency] + (
            [max_concurrency] if max_concurrency not in steps else []
        )
