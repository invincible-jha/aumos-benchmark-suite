"""Fidelity benchmark adapter for the AumOS Benchmark Suite.

Benchmarks synthetic data quality using SDMetrics-compatible scoring,
cross-generator comparison, quality vs speed tradeoff analysis, and
domain-specific quality benchmarks.
"""

import uuid
from typing import Any

import httpx

from aumos_common.observability import get_logger

logger = get_logger(__name__)

# Minimum acceptable fidelity score for production use
_MIN_ACCEPTABLE_SCORE: float = 0.70

# Supported synthetic data generators for comparison
_KNOWN_GENERATORS: frozenset[str] = frozenset({
    "ctgan", "tvae", "copulagan", "gaussiancopula", "fast_ml"
})

# Domain-specific quality thresholds
_DOMAIN_THRESHOLDS: dict[str, float] = {
    "healthcare": 0.85,
    "financial": 0.80,
    "retail": 0.75,
    "general": 0.70,
}


class FidelityBenchmark:
    """Benchmarks synthesis quality using statistical fidelity metrics.

    Supports single-generator benchmarking, cross-generator comparison,
    quality vs speed tradeoff analysis, and domain-specific threshold validation.
    Integrates with aumos-fidelity-validator for metric computation.
    """

    def __init__(
        self,
        fidelity_validator_url: str,
        http_timeout: float = 60.0,
        min_acceptable_score: float = _MIN_ACCEPTABLE_SCORE,
    ) -> None:
        """Initialise with fidelity validator service URL.

        Args:
            fidelity_validator_url: Base URL for aumos-fidelity-validator service.
            http_timeout: HTTP call timeout in seconds.
            min_acceptable_score: Minimum fidelity score to pass the benchmark.
        """
        self._fidelity_url = fidelity_validator_url
        self._http_timeout = http_timeout
        self._min_acceptable_score = min_acceptable_score

    async def run_quality_benchmark(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        generator_name: str,
        dataset_rows: int = 1000,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a full quality benchmark for a single generator on a dataset.

        Calls aumos-fidelity-validator to compute SDMetrics-compatible scores
        including KS statistic, TV complement, correlation similarity, and
        boundary coverage.

        Args:
            run_id: BenchmarkRun UUID for correlation logging.
            dataset_name: Reference dataset name to benchmark against.
            generator_name: Name of the synthesis model under test.
            dataset_rows: Number of synthetic rows to evaluate.
            metadata: Optional schema or column-level metadata hints.

        Returns:
            Dict with overall_score, per-metric scores, and pass/fail status.
        """
        logger.info(
            "Running fidelity quality benchmark",
            run_id=str(run_id),
            dataset=dataset_name,
            generator=generator_name,
            rows=dataset_rows,
        )

        try:
            async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                response = await client.post(
                    f"{self._fidelity_url}/api/v1/validate/benchmark",
                    json={
                        "dataset_name": dataset_name,
                        "generator_name": generator_name,
                        "run_id": str(run_id),
                        "row_count": dataset_rows,
                        "metadata": metadata or {},
                    },
                )
                response.raise_for_status()
                data = response.json()

                metrics = {
                    m["name"]: m["value"]
                    for m in data.get("metrics", [])
                }
                overall_score = data.get("overall_score", self._compute_fallback_score(metrics))

                return self._build_quality_result(
                    run_id=run_id,
                    dataset_name=dataset_name,
                    generator_name=generator_name,
                    overall_score=overall_score,
                    metrics=metrics,
                    raw_response=data,
                )

        except httpx.HTTPError as exc:
            logger.warning(
                "Fidelity validator unavailable — using stub scores",
                run_id=str(run_id),
                error=str(exc),
            )
            return self._stub_quality_result(run_id, dataset_name, generator_name)

    async def compare_generators(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        generators: list[str],
        dataset_rows: int = 1000,
    ) -> dict[str, Any]:
        """Compare fidelity scores across multiple synthesis generators.

        Args:
            run_id: BenchmarkRun UUID.
            dataset_name: Reference dataset name.
            generators: List of generator names to compare.
            dataset_rows: Synthetic row count per generator.

        Returns:
            Dict with per-generator scores, ranking, and best_generator.
        """
        logger.info(
            "Comparing fidelity across generators",
            run_id=str(run_id),
            dataset=dataset_name,
            generators=generators,
        )

        generator_results: dict[str, dict[str, Any]] = {}
        for generator_name in generators:
            result = await self.run_quality_benchmark(
                run_id=run_id,
                dataset_name=dataset_name,
                generator_name=generator_name,
                dataset_rows=dataset_rows,
            )
            generator_results[generator_name] = result

        ranking = sorted(
            generator_results.keys(),
            key=lambda g: generator_results[g].get("overall_score", 0.0),
            reverse=True,
        )

        best_generator = ranking[0] if ranking else None

        return {
            "run_id": str(run_id),
            "dataset_name": dataset_name,
            "generators": generator_results,
            "ranking": ranking,
            "best_generator": best_generator,
            "best_score": generator_results[best_generator].get("overall_score") if best_generator else None,
        }

    async def analyze_quality_vs_speed_tradeoff(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        generator_measurements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Analyze the quality-speed tradeoff across generator measurements.

        Each entry in generator_measurements must contain generator_name,
        overall_score, and duration_seconds.

        Args:
            run_id: BenchmarkRun UUID.
            dataset_name: Dataset name for labelling.
            generator_measurements: List of dicts with generator stats.

        Returns:
            Pareto curve data identifying quality-speed optimal generators.
        """
        if not generator_measurements:
            return {"run_id": str(run_id), "pareto_frontier": []}

        # Build (quality, speed) pairs — higher quality and higher speed is better
        points: list[dict[str, Any]] = []
        for measurement in generator_measurements:
            quality = measurement.get("overall_score", 0.0)
            duration = measurement.get("duration_seconds", 1.0)
            rows = measurement.get("rows_produced", 1)
            speed = rows / max(duration, 0.001)

            points.append({
                "generator_name": measurement.get("generator_name"),
                "overall_score": quality,
                "rows_per_second": round(speed, 2),
                "duration_seconds": duration,
                "is_pareto_optimal": False,
            })

        # Identify Pareto frontier (non-dominated points)
        for i, point_i in enumerate(points):
            dominated = False
            for j, point_j in enumerate(points):
                if i == j:
                    continue
                if (
                    point_j["overall_score"] >= point_i["overall_score"]
                    and point_j["rows_per_second"] >= point_i["rows_per_second"]
                    and (
                        point_j["overall_score"] > point_i["overall_score"]
                        or point_j["rows_per_second"] > point_i["rows_per_second"]
                    )
                ):
                    dominated = True
                    break
            if not dominated:
                point_i["is_pareto_optimal"] = True

        pareto_frontier = [p for p in points if p["is_pareto_optimal"]]

        return {
            "run_id": str(run_id),
            "dataset_name": dataset_name,
            "all_generators": points,
            "pareto_frontier": pareto_frontier,
            "pareto_count": len(pareto_frontier),
        }

    async def validate_domain_quality(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        generator_name: str,
        domain: str,
        overall_score: float,
    ) -> dict[str, Any]:
        """Validate that a benchmark meets domain-specific quality thresholds.

        Args:
            run_id: BenchmarkRun UUID.
            dataset_name: Dataset name.
            generator_name: Generator under test.
            domain: Domain name (healthcare | financial | retail | general).
            overall_score: Fidelity score from run_quality_benchmark.

        Returns:
            Validation result with threshold, passed flag, and gap to threshold.
        """
        threshold = _DOMAIN_THRESHOLDS.get(domain, _DOMAIN_THRESHOLDS["general"])
        passed = overall_score >= threshold
        gap = threshold - overall_score

        return {
            "run_id": str(run_id),
            "dataset_name": dataset_name,
            "generator_name": generator_name,
            "domain": domain,
            "overall_score": overall_score,
            "threshold": threshold,
            "passed": passed,
            "gap_to_threshold": round(gap, 4),
        }

    async def test_quality_consistency(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        generator_name: str,
        repetitions: int = 5,
        dataset_rows: int = 1000,
    ) -> dict[str, Any]:
        """Test reproducibility by running the same benchmark multiple times.

        Args:
            run_id: BenchmarkRun UUID.
            dataset_name: Dataset name.
            generator_name: Generator under test.
            repetitions: Number of times to repeat the benchmark.
            dataset_rows: Rows to generate per repetition.

        Returns:
            Consistency report with scores, mean, stddev, and coefficient of variation.
        """
        scores: list[float] = []
        for rep_index in range(repetitions):
            logger.info(
                "Quality consistency repetition",
                run_id=str(run_id),
                rep=rep_index + 1,
                total=repetitions,
            )
            result = await self.run_quality_benchmark(
                run_id=run_id,
                dataset_name=dataset_name,
                generator_name=generator_name,
                dataset_rows=dataset_rows,
            )
            scores.append(result.get("overall_score", 0.0))

        import statistics as stats_module

        mean_score = stats_module.mean(scores) if scores else 0.0
        stddev = stats_module.stdev(scores) if len(scores) > 1 else 0.0
        cv = (stddev / max(mean_score, 0.0001)) * 100.0

        return {
            "run_id": str(run_id),
            "dataset_name": dataset_name,
            "generator_name": generator_name,
            "repetitions": repetitions,
            "scores": scores,
            "mean_score": round(mean_score, 4),
            "stddev": round(stddev, 4),
            "coefficient_of_variation_pct": round(cv, 2),
            "is_consistent": cv < 5.0,  # CV < 5% considered consistent
        }

    def generate_fidelity_report(
        self,
        run_id: uuid.UUID,
        benchmark_results: list[dict[str, Any]],
        comparison: dict[str, Any] | None = None,
        consistency: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate a structured fidelity benchmark report.

        Args:
            run_id: BenchmarkRun UUID.
            benchmark_results: List of run_quality_benchmark outputs.
            comparison: Optional generator comparison dict.
            consistency: Optional quality consistency dict.

        Returns:
            Structured fidelity report.
        """
        passing_count = sum(
            1 for r in benchmark_results
            if r.get("overall_score", 0.0) >= self._min_acceptable_score
        )

        report: dict[str, Any] = {
            "run_id": str(run_id),
            "report_type": "fidelity",
            "benchmark_count": len(benchmark_results),
            "passing_count": passing_count,
            "failure_count": len(benchmark_results) - passing_count,
            "min_acceptable_score": self._min_acceptable_score,
            "benchmarks": benchmark_results,
        }

        if comparison:
            report["generator_comparison"] = comparison
        if consistency:
            report["quality_consistency"] = consistency

        logger.info(
            "Fidelity benchmark report generated",
            run_id=str(run_id),
            passing=passing_count,
            total=len(benchmark_results),
        )

        return report

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_quality_result(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        generator_name: str,
        overall_score: float,
        metrics: dict[str, float],
        raw_response: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a normalised quality result dict from validator response data.

        Args:
            run_id: BenchmarkRun UUID.
            dataset_name: Dataset name.
            generator_name: Generator name.
            overall_score: Aggregated overall quality score.
            metrics: Dict of metric_name -> value.
            raw_response: Full validator response for additional_data.

        Returns:
            Normalised quality result dict.
        """
        return {
            "run_id": str(run_id),
            "dataset_name": dataset_name,
            "generator_name": generator_name,
            "overall_score": round(overall_score, 4),
            "passed": overall_score >= self._min_acceptable_score,
            "min_acceptable_score": self._min_acceptable_score,
            "metrics": {k: round(v, 4) for k, v in metrics.items()},
            "additional_data": raw_response,
        }

    def _stub_quality_result(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        generator_name: str,
    ) -> dict[str, Any]:
        """Return zero-valued stub quality result when fidelity validator is unavailable.

        Args:
            run_id: BenchmarkRun UUID.
            dataset_name: Dataset name.
            generator_name: Generator name.

        Returns:
            Stub quality result with stub=True marker.
        """
        return {
            "run_id": str(run_id),
            "dataset_name": dataset_name,
            "generator_name": generator_name,
            "overall_score": 0.0,
            "passed": False,
            "min_acceptable_score": self._min_acceptable_score,
            "metrics": {
                "ks_statistic": 0.0,
                "tv_complement": 0.0,
                "correlation_similarity": 0.0,
                "boundary_coverage": 0.0,
            },
            "additional_data": {"stub": True},
        }

    def _compute_fallback_score(self, metrics: dict[str, float]) -> float:
        """Compute an overall score as the mean of available metric values.

        Args:
            metrics: Dict of metric_name -> value.

        Returns:
            Mean score, or 0.0 if no metrics available.
        """
        if not metrics:
            return 0.0
        return sum(metrics.values()) / len(metrics)
