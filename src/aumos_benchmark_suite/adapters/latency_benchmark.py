"""Latency benchmark adapter for the AumOS Benchmark Suite.

Measures per-endpoint API latency with statistical percentile analysis,
warm-up exclusion, concurrent load profiling, regression detection,
and structured report generation.
"""

import asyncio
import statistics
import time
import uuid
from typing import Any

import httpx

from aumos_common.observability import get_logger

logger = get_logger(__name__)

# Number of warm-up requests to discard before recording measurements
_WARMUP_REQUESTS: int = 3

# Percentiles to compute from the latency distribution
_PERCENTILES: dict[str, float] = {"p50": 0.50, "p75": 0.75, "p95": 0.95, "p99": 0.99}

# Default regression threshold in milliseconds
_DEFAULT_REGRESSION_THRESHOLD_MS: float = 50.0


class LatencyBenchmark:
    """Measures per-endpoint API latency with warm-up exclusion and percentile analysis.

    Executes a configurable number of sequential or concurrent HTTP requests
    against a target endpoint, discards warm-up samples, then computes P50,
    P75, P95, and P99 latency percentiles. Supports concurrent load testing,
    baseline comparison, and structured report generation.
    """

    def __init__(
        self,
        http_timeout: float = 30.0,
        warmup_requests: int = _WARMUP_REQUESTS,
        regression_threshold_ms: float = _DEFAULT_REGRESSION_THRESHOLD_MS,
    ) -> None:
        """Initialise with measurement parameters.

        Args:
            http_timeout: Per-request HTTP timeout in seconds.
            warmup_requests: Number of initial requests to discard.
            regression_threshold_ms: Absolute P95 delta that constitutes regression.
        """
        self._http_timeout = http_timeout
        self._warmup_requests = warmup_requests
        self._regression_threshold_ms = regression_threshold_ms

    async def measure_endpoint(
        self,
        run_id: uuid.UUID,
        endpoint_url: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        sample_count: int = 50,
    ) -> dict[str, Any]:
        """Measure latency distribution for a single endpoint.

        Sends warm-up requests (discarded) followed by measurement requests.
        Returns raw latency samples and computed percentiles.

        Args:
            run_id: BenchmarkRun UUID for correlation logging.
            endpoint_url: Full URL of the endpoint to probe.
            method: HTTP method (GET, POST, etc.).
            payload: Optional JSON request body.
            headers: Optional extra request headers.
            sample_count: Number of measurement samples to collect.

        Returns:
            Dict containing latency_samples_ms, percentiles, mean_ms,
            stddev_ms, min_ms, max_ms, endpoint_url, sample_count.
        """
        logger.info(
            "Starting latency measurement",
            run_id=str(run_id),
            endpoint=endpoint_url,
            sample_count=sample_count,
            warmup=self._warmup_requests,
        )

        total_requests = self._warmup_requests + sample_count
        latencies: list[float] = []

        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            for index in range(total_requests):
                sample_start = time.monotonic()
                try:
                    response = await client.request(
                        method,
                        endpoint_url,
                        json=payload,
                        headers=headers or {},
                    )
                    elapsed_ms = (time.monotonic() - sample_start) * 1000.0
                    response.raise_for_status()
                except httpx.TimeoutException:
                    elapsed_ms = self._http_timeout * 1000.0
                    logger.warning(
                        "Request timed out",
                        run_id=str(run_id),
                        index=index,
                    )
                except httpx.HTTPError as exc:
                    elapsed_ms = (time.monotonic() - sample_start) * 1000.0
                    logger.warning(
                        "HTTP error during latency measurement",
                        run_id=str(run_id),
                        error=str(exc),
                    )

                # Skip warm-up samples
                if index >= self._warmup_requests:
                    latencies.append(elapsed_ms)

        return self._compute_distribution(
            latencies=latencies,
            endpoint_url=endpoint_url,
            sample_count=sample_count,
        )

    async def measure_concurrent_latency(
        self,
        run_id: uuid.UUID,
        endpoint_url: str,
        concurrency_levels: list[int],
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        requests_per_level: int = 30,
    ) -> dict[str, Any]:
        """Measure latency under increasing concurrency to identify saturation.

        Args:
            run_id: BenchmarkRun UUID.
            endpoint_url: Full URL of the endpoint.
            concurrency_levels: List of concurrency values to test (e.g., [1, 5, 10, 25]).
            method: HTTP method.
            payload: Optional JSON body.
            headers: Optional extra headers.
            requests_per_level: Requests to fire at each concurrency level.

        Returns:
            Dict mapping concurrency level to its latency distribution.
        """
        logger.info(
            "Starting concurrent latency measurement",
            run_id=str(run_id),
            endpoint=endpoint_url,
            concurrency_levels=concurrency_levels,
        )

        curve: dict[str, Any] = {}

        for concurrency in concurrency_levels:
            level_latencies: list[float] = []

            async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                for batch_start in range(0, requests_per_level, concurrency):
                    batch_size = min(concurrency, requests_per_level - batch_start)
                    tasks = [
                        self._timed_request(client, method, endpoint_url, payload, headers)
                        for _ in range(batch_size)
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for result in results:
                        if isinstance(result, float):
                            level_latencies.append(result)
                        elif isinstance(result, Exception):
                            logger.warning(
                                "Concurrent request failed",
                                concurrency=concurrency,
                                error=str(result),
                            )

            curve[str(concurrency)] = self._compute_distribution(
                latencies=level_latencies,
                endpoint_url=endpoint_url,
                sample_count=len(level_latencies),
            )

        return {
            "endpoint_url": endpoint_url,
            "concurrency_curve": curve,
            "saturation_point": self._detect_saturation(curve),
        }

    async def compare_to_baseline(
        self,
        current_distribution: dict[str, Any],
        baseline_distribution: dict[str, Any],
    ) -> dict[str, Any]:
        """Compare current latency measurements to a historical baseline.

        Args:
            current_distribution: Output from measure_endpoint for current run.
            baseline_distribution: Output from measure_endpoint for baseline run.

        Returns:
            Comparison dict with deltas, regression flags, and summary.
        """
        comparison: dict[str, Any] = {
            "endpoint_url": current_distribution.get("endpoint_url"),
            "current": current_distribution.get("percentiles", {}),
            "baseline": baseline_distribution.get("percentiles", {}),
            "deltas": {},
            "regressions": [],
            "overall_status": "passed",
        }

        for percentile, current_value in current_distribution.get("percentiles", {}).items():
            baseline_value = baseline_distribution.get("percentiles", {}).get(percentile)
            if baseline_value is not None:
                delta_ms = current_value - baseline_value
                regressed = delta_ms > self._regression_threshold_ms
                comparison["deltas"][percentile] = {
                    "delta_ms": round(delta_ms, 2),
                    "percent_change": round((delta_ms / max(baseline_value, 0.001)) * 100, 2),
                    "regressed": regressed,
                }
                if regressed and percentile in ("p95", "p99"):
                    comparison["regressions"].append(percentile)

        if comparison["regressions"]:
            comparison["overall_status"] = "failed"

        return comparison

    def generate_latency_report(
        self,
        run_id: uuid.UUID,
        measurements: list[dict[str, Any]],
        comparison: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate a structured latency report from collected measurements.

        Args:
            run_id: BenchmarkRun UUID.
            measurements: List of per-endpoint measurement dicts.
            comparison: Optional baseline comparison dict.

        Returns:
            Structured latency report ready for persistence or export.
        """
        endpoints_summary: list[dict[str, Any]] = []
        worst_p95: float = 0.0

        for measurement in measurements:
            p95 = measurement.get("percentiles", {}).get("p95", 0.0)
            endpoints_summary.append({
                "endpoint_url": measurement.get("endpoint_url"),
                "p50_ms": measurement.get("percentiles", {}).get("p50"),
                "p95_ms": p95,
                "p99_ms": measurement.get("percentiles", {}).get("p99"),
                "mean_ms": measurement.get("mean_ms"),
                "sample_count": measurement.get("sample_count"),
            })
            worst_p95 = max(worst_p95, p95)

        report = {
            "run_id": str(run_id),
            "report_type": "latency",
            "endpoint_count": len(measurements),
            "endpoints": endpoints_summary,
            "worst_p95_ms": round(worst_p95, 2),
            "regression_threshold_ms": self._regression_threshold_ms,
        }

        if comparison is not None:
            report["comparison"] = comparison

        logger.info(
            "Latency report generated",
            run_id=str(run_id),
            endpoint_count=len(measurements),
            worst_p95_ms=worst_p95,
        )

        return report

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _timed_request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        headers: dict[str, str] | None,
    ) -> float:
        """Execute a single timed request and return elapsed milliseconds.

        Args:
            client: Shared httpx client.
            method: HTTP method.
            url: Target URL.
            payload: Optional JSON body.
            headers: Optional extra headers.

        Returns:
            Elapsed time in milliseconds.
        """
        start = time.monotonic()
        try:
            await client.request(method, url, json=payload, headers=headers or {})
        except Exception:
            pass
        return (time.monotonic() - start) * 1000.0

    def _compute_distribution(
        self,
        latencies: list[float],
        endpoint_url: str,
        sample_count: int,
    ) -> dict[str, Any]:
        """Compute descriptive statistics from a list of latency samples.

        Args:
            latencies: Raw latency measurements in milliseconds.
            endpoint_url: Endpoint URL for labelling.
            sample_count: Expected sample count.

        Returns:
            Distribution dict with percentiles, mean, stddev, min, max.
        """
        if not latencies:
            return {
                "endpoint_url": endpoint_url,
                "sample_count": 0,
                "latency_samples_ms": [],
                "percentiles": {k: 0.0 for k in _PERCENTILES},
                "mean_ms": 0.0,
                "stddev_ms": 0.0,
                "min_ms": 0.0,
                "max_ms": 0.0,
            }

        sorted_latencies = sorted(latencies)
        percentiles = {
            label: round(sorted_latencies[
                min(int(quantile * len(sorted_latencies)), len(sorted_latencies) - 1)
            ], 2)
            for label, quantile in _PERCENTILES.items()
        }

        return {
            "endpoint_url": endpoint_url,
            "sample_count": sample_count,
            "latency_samples_ms": [round(v, 2) for v in latencies],
            "percentiles": percentiles,
            "mean_ms": round(statistics.mean(latencies), 2),
            "stddev_ms": round(statistics.stdev(latencies) if len(latencies) > 1 else 0.0, 2),
            "min_ms": round(min(latencies), 2),
            "max_ms": round(max(latencies), 2),
        }

    def _detect_saturation(self, curve: dict[str, Any]) -> int | None:
        """Identify the concurrency level where latency begins degrading non-linearly.

        Args:
            curve: Dict mapping concurrency level (str) to distribution.

        Returns:
            Concurrency level at saturation, or None if not detected.
        """
        sorted_levels = sorted(curve.keys(), key=int)
        if len(sorted_levels) < 2:
            return None

        previous_p95: float | None = None
        for level_str in sorted_levels:
            current_p95 = curve[level_str].get("percentiles", {}).get("p95", 0.0)
            if previous_p95 is not None:
                increase = current_p95 - previous_p95
                if increase > self._regression_threshold_ms * 2:
                    return int(level_str)
            previous_p95 = current_p95

        return None
