"""Cost benchmark adapter for the AumOS Benchmark Suite.

Measures per-operation compute, storage, and network costs, computes
cost-per-quality-unit metrics, and compares costs across providers.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from aumos_common.observability import get_logger

logger = get_logger(__name__)

# Default cost rates — overridden by configuration
_DEFAULT_COMPUTE_RATE_PER_HOUR_USD: float = 3.50  # GPU instance hourly rate
_DEFAULT_STORAGE_RATE_PER_GB_MONTH_USD: float = 0.023  # S3-equivalent rate
_DEFAULT_NETWORK_EGRESS_RATE_PER_GB_USD: float = 0.09  # Standard egress


@dataclass
class CostRates:
    """Configurable cost rates for cloud resource pricing.

    Attributes:
        compute_per_hour_usd: Compute instance cost per hour.
        storage_per_gb_month_usd: Storage cost per GB per month.
        network_egress_per_gb_usd: Network egress cost per GB.
        gpu_multiplier: Multiplier applied to compute cost for GPU instances.
    """

    compute_per_hour_usd: float = _DEFAULT_COMPUTE_RATE_PER_HOUR_USD
    storage_per_gb_month_usd: float = _DEFAULT_STORAGE_RATE_PER_GB_MONTH_USD
    network_egress_per_gb_usd: float = _DEFAULT_NETWORK_EGRESS_RATE_PER_GB_USD
    gpu_multiplier: float = 4.0


@dataclass
class OperationCostMeasurement:
    """Cost measurement for a single operation.

    Attributes:
        operation_name: Name of the measured operation.
        compute_cost_usd: Compute cost in USD.
        storage_cost_usd: Storage cost in USD.
        network_cost_usd: Network egress cost in USD.
        total_cost_usd: Sum of all cost components.
        duration_seconds: Operation wall-clock duration.
        rows_produced: Number of data rows produced (for data ops).
        quality_score: Optional quality score for cost-per-quality computation.
        additional_data: Supplementary measurement metadata.
    """

    operation_name: str
    compute_cost_usd: float
    storage_cost_usd: float
    network_cost_usd: float
    total_cost_usd: float
    duration_seconds: float
    rows_produced: int = 0
    quality_score: float | None = None
    additional_data: dict[str, Any] = field(default_factory=dict)


class CostBenchmark:
    """Measures per-operation costs for AumOS platform operations.

    Computes compute, storage, and network costs for each measured operation,
    derives cost-per-row and cost-per-quality-unit metrics, and supports
    cross-provider comparison and optimization identification.
    """

    def __init__(
        self,
        rates: CostRates | None = None,
    ) -> None:
        """Initialise with optional custom cost rates.

        Args:
            rates: CostRates instance. Defaults to standard cloud rates.
        """
        self._rates = rates or CostRates()

    async def measure_inference_cost(
        self,
        run_id: uuid.UUID,
        operation_name: str,
        duration_seconds: float,
        rows_produced: int,
        peak_memory_mb: float,
        uses_gpu: bool = False,
        output_size_bytes: int = 0,
    ) -> OperationCostMeasurement:
        """Compute cost for a single inference (data generation) operation.

        Args:
            run_id: BenchmarkRun UUID for logging.
            operation_name: Descriptive name for the operation.
            duration_seconds: Measured wall-clock duration.
            rows_produced: Number of synthetic rows produced.
            peak_memory_mb: Peak memory utilization during operation.
            uses_gpu: Whether GPU was used (applies gpu_multiplier).
            output_size_bytes: Size of output data produced in bytes.

        Returns:
            OperationCostMeasurement with all cost components populated.
        """
        hours = duration_seconds / 3600.0
        compute_base = self._rates.compute_per_hour_usd * hours
        compute_cost = compute_base * (self._rates.gpu_multiplier if uses_gpu else 1.0)

        # Storage cost: output stored for one month
        output_gb = output_size_bytes / (1024**3)
        storage_cost = output_gb * self._rates.storage_per_gb_month_usd

        # Network cost: output egressed once
        network_cost = output_gb * self._rates.network_egress_per_gb_usd

        total_cost = compute_cost + storage_cost + network_cost

        logger.info(
            "Inference cost measured",
            run_id=str(run_id),
            operation=operation_name,
            total_cost_usd=round(total_cost, 6),
            rows_produced=rows_produced,
        )

        return OperationCostMeasurement(
            operation_name=operation_name,
            compute_cost_usd=round(compute_cost, 6),
            storage_cost_usd=round(storage_cost, 6),
            network_cost_usd=round(network_cost, 6),
            total_cost_usd=round(total_cost, 6),
            duration_seconds=duration_seconds,
            rows_produced=rows_produced,
            additional_data={
                "run_id": str(run_id),
                "uses_gpu": uses_gpu,
                "peak_memory_mb": peak_memory_mb,
                "output_size_bytes": output_size_bytes,
                "hours": round(hours, 6),
            },
        )

    async def measure_storage_cost(
        self,
        run_id: uuid.UUID,
        dataset_name: str,
        size_bytes: int,
        retention_months: float = 1.0,
    ) -> dict[str, Any]:
        """Compute storage cost for a dataset over a retention period.

        Args:
            run_id: BenchmarkRun UUID.
            dataset_name: Name of the dataset.
            size_bytes: Dataset size in bytes.
            retention_months: How many months to retain.

        Returns:
            Dict with size_gb, monthly_cost_usd, total_cost_usd.
        """
        size_gb = size_bytes / (1024**3)
        monthly_cost = size_gb * self._rates.storage_per_gb_month_usd
        total_cost = monthly_cost * retention_months

        return {
            "run_id": str(run_id),
            "dataset_name": dataset_name,
            "size_gb": round(size_gb, 4),
            "monthly_cost_usd": round(monthly_cost, 6),
            "retention_months": retention_months,
            "total_cost_usd": round(total_cost, 6),
        }

    def compute_cost_per_quality_unit(
        self,
        measurement: OperationCostMeasurement,
        quality_score: float,
    ) -> dict[str, Any]:
        """Derive cost-per-quality-unit from a measurement and its quality score.

        A lower cost-per-quality-unit is better: it reflects how much USD
        is spent to achieve one unit of synthesis quality (score 0-1).

        Args:
            measurement: The cost measurement for the operation.
            quality_score: Fidelity score (0.0–1.0) achieved by the operation.

        Returns:
            Dict with cost_per_quality_unit_usd, quality_score, total_cost_usd.
        """
        safe_quality = max(quality_score, 0.0001)
        cost_per_quality_unit = measurement.total_cost_usd / safe_quality
        cost_per_row = measurement.total_cost_usd / max(measurement.rows_produced, 1)

        return {
            "operation_name": measurement.operation_name,
            "total_cost_usd": measurement.total_cost_usd,
            "quality_score": quality_score,
            "cost_per_quality_unit_usd": round(cost_per_quality_unit, 6),
            "cost_per_row_usd": round(cost_per_row, 8),
            "rows_produced": measurement.rows_produced,
        }

    async def compare_providers(
        self,
        run_id: uuid.UUID,
        provider_measurements: dict[str, OperationCostMeasurement],
    ) -> dict[str, Any]:
        """Compare costs across multiple providers for the same operation type.

        Args:
            run_id: BenchmarkRun UUID.
            provider_measurements: Dict mapping provider name to its cost measurement.

        Returns:
            Comparison dict with per-provider costs, cheapest provider, and savings.
        """
        if not provider_measurements:
            return {"run_id": str(run_id), "providers": {}, "cheapest_provider": None}

        provider_summary: dict[str, dict[str, Any]] = {}
        min_cost: float = float("inf")
        cheapest_provider: str = ""

        for provider_name, measurement in provider_measurements.items():
            provider_summary[provider_name] = {
                "total_cost_usd": measurement.total_cost_usd,
                "cost_per_row_usd": round(
                    measurement.total_cost_usd / max(measurement.rows_produced, 1), 8
                ),
                "compute_cost_usd": measurement.compute_cost_usd,
                "storage_cost_usd": measurement.storage_cost_usd,
                "network_cost_usd": measurement.network_cost_usd,
            }
            if measurement.total_cost_usd < min_cost:
                min_cost = measurement.total_cost_usd
                cheapest_provider = provider_name

        # Compute potential savings vs each provider
        for provider_name in provider_summary:
            provider_cost = provider_summary[provider_name]["total_cost_usd"]
            potential_savings = provider_cost - min_cost
            provider_summary[provider_name]["savings_vs_cheapest_usd"] = round(
                potential_savings, 6
            )
            provider_summary[provider_name]["savings_percent"] = round(
                (potential_savings / max(provider_cost, 0.0001)) * 100, 2
            )

        return {
            "run_id": str(run_id),
            "providers": provider_summary,
            "cheapest_provider": cheapest_provider,
            "cheapest_cost_usd": round(min_cost, 6),
        }

    def identify_cost_optimizations(
        self,
        measurements: list[OperationCostMeasurement],
    ) -> list[dict[str, Any]]:
        """Identify cost reduction opportunities from a set of measurements.

        Args:
            measurements: List of OperationCostMeasurement instances to analyze.

        Returns:
            List of optimization recommendations with estimated savings.
        """
        recommendations: list[dict[str, Any]] = []

        for measurement in measurements:
            total = measurement.total_cost_usd
            if total <= 0:
                continue

            compute_pct = measurement.compute_cost_usd / total * 100
            storage_pct = measurement.storage_cost_usd / total * 100
            network_pct = measurement.network_cost_usd / total * 100

            if compute_pct > 70:
                recommendations.append({
                    "operation": measurement.operation_name,
                    "type": "compute_optimization",
                    "description": "Compute dominates costs. Consider spot instances or smaller GPU types.",
                    "compute_percent": round(compute_pct, 1),
                    "estimated_savings_percent": 30.0,
                })
            if storage_pct > 30:
                recommendations.append({
                    "operation": measurement.operation_name,
                    "type": "storage_optimization",
                    "description": "High storage cost. Enable compression or reduce retention period.",
                    "storage_percent": round(storage_pct, 1),
                    "estimated_savings_percent": 40.0,
                })
            if network_pct > 20:
                recommendations.append({
                    "operation": measurement.operation_name,
                    "type": "network_optimization",
                    "description": "High egress costs. Consider co-locating consumers with data.",
                    "network_percent": round(network_pct, 1),
                    "estimated_savings_percent": 50.0,
                })
            if measurement.rows_produced > 0:
                cost_per_row = measurement.total_cost_usd / measurement.rows_produced
                if cost_per_row > 0.001:
                    recommendations.append({
                        "operation": measurement.operation_name,
                        "type": "batch_size_optimization",
                        "description": "High per-row cost. Increasing batch size typically reduces overhead.",
                        "cost_per_row_usd": round(cost_per_row, 8),
                        "estimated_savings_percent": 20.0,
                    })

        return recommendations

    def generate_cost_report(
        self,
        run_id: uuid.UUID,
        measurements: list[OperationCostMeasurement],
        provider_comparison: dict[str, Any] | None = None,
        optimizations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Generate a structured cost benchmark report.

        Args:
            run_id: BenchmarkRun UUID.
            measurements: List of operation cost measurements.
            provider_comparison: Optional cross-provider comparison dict.
            optimizations: Optional list of optimization recommendations.

        Returns:
            Structured cost report dict.
        """
        total_cost = sum(m.total_cost_usd for m in measurements)
        total_rows = sum(m.rows_produced for m in measurements)
        overall_cost_per_row = total_cost / max(total_rows, 1)

        report: dict[str, Any] = {
            "run_id": str(run_id),
            "report_type": "cost",
            "total_cost_usd": round(total_cost, 6),
            "total_rows_produced": total_rows,
            "overall_cost_per_row_usd": round(overall_cost_per_row, 8),
            "operations": [
                {
                    "operation_name": m.operation_name,
                    "total_cost_usd": m.total_cost_usd,
                    "compute_cost_usd": m.compute_cost_usd,
                    "storage_cost_usd": m.storage_cost_usd,
                    "network_cost_usd": m.network_cost_usd,
                    "rows_produced": m.rows_produced,
                    "duration_seconds": m.duration_seconds,
                }
                for m in measurements
            ],
        }

        if provider_comparison:
            report["provider_comparison"] = provider_comparison
        if optimizations:
            report["optimizations"] = optimizations
            report["optimization_count"] = len(optimizations)

        logger.info(
            "Cost report generated",
            run_id=str(run_id),
            total_cost_usd=total_cost,
            operation_count=len(measurements),
        )

        return report
