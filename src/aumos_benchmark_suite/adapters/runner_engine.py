"""Benchmark runner engine adapter for the AumOS Benchmark Suite.

Implements the IBenchmarkRunnerAdapter protocol, coordinating calls to
AumOS tabular-engine, privacy-engine, and fidelity-validator to collect
raw metric measurements for a benchmark run.
"""

import uuid
from typing import Any

import httpx

from aumos_common.observability import get_logger

logger = get_logger(__name__)

# Required top-level keys in a valid benchmark configuration
_REQUIRED_CONFIG_KEYS: frozenset[str] = frozenset({"metrics", "dataset"})

# Valid metric category keys
_VALID_METRIC_CATEGORIES: frozenset[str] = frozenset({"fidelity", "privacy", "speed"})


class RunnerEngineAdapter:
    """Benchmark execution engine that coordinates AumOS service calls.

    Calls aumos-tabular-engine for data generation, then aumos-fidelity-validator
    for fidelity metrics, aumos-privacy-engine for privacy metrics, and measures
    speed metrics from the generation call itself.

    This adapter implements the IBenchmarkRunnerAdapter protocol.
    """

    def __init__(
        self,
        tabular_engine_url: str,
        privacy_engine_url: str,
        fidelity_validator_url: str,
        http_timeout: float = 60.0,
        http_max_retries: int = 3,
    ) -> None:
        """Initialise with upstream service URLs.

        Args:
            tabular_engine_url: Base URL for aumos-tabular-engine.
            privacy_engine_url: Base URL for aumos-privacy-engine.
            fidelity_validator_url: Base URL for aumos-fidelity-validator.
            http_timeout: HTTP call timeout in seconds.
            http_max_retries: Maximum retry attempts per HTTP call.
        """
        self._tabular_url = tabular_engine_url
        self._privacy_url = privacy_engine_url
        self._fidelity_url = fidelity_validator_url
        self._http_timeout = http_timeout
        self._http_max_retries = http_max_retries

    async def validate_config(
        self, config: dict[str, Any]
    ) -> tuple[bool, list[str]]:
        """Validate a benchmark configuration before execution.

        Checks that required keys are present and metric categories are valid.

        Args:
            config: Benchmark configuration dict to validate.

        Returns:
            Tuple of (is_valid, list_of_validation_errors).
        """
        errors: list[str] = []

        missing_keys = _REQUIRED_CONFIG_KEYS - set(config.keys())
        if missing_keys:
            errors.append(f"Missing required config keys: {missing_keys}")

        if "metrics" in config:
            metrics_config = config["metrics"]
            if not isinstance(metrics_config, dict):
                errors.append("'metrics' must be a dict mapping category to list of metric names")
            else:
                invalid_categories = set(metrics_config.keys()) - _VALID_METRIC_CATEGORIES
                if invalid_categories:
                    errors.append(
                        f"Invalid metric categories in config: {invalid_categories}. "
                        f"Valid: {_VALID_METRIC_CATEGORIES}"
                    )

        return len(errors) == 0, errors

    async def execute_run(
        self,
        run_id: uuid.UUID,
        config: dict[str, Any],
        dataset_name: str,
    ) -> dict[str, Any]:
        """Execute a benchmark run by coordinating AumOS service calls.

        Calls tabular-engine for data generation, then dispatches fidelity,
        privacy, and speed metric collection in parallel. Returns raw measurements
        grouped by category for persistence by BenchmarkRunnerService.

        Args:
            run_id: BenchmarkRun UUID for correlation and logging.
            config: Full benchmark configuration.
            dataset_name: Dataset to use for benchmark execution.

        Returns:
            Dict of raw metric measurements grouped by category:
            {
                "fidelity": [{metric_name, value, unit, higher_is_better}],
                "privacy": [...],
                "speed": [...],
            }
        """
        metrics_config: dict[str, list[str]] = config.get("metrics", {})
        requested_categories = set(metrics_config.keys())

        results: dict[str, list[dict[str, Any]]] = {
            "fidelity": [],
            "privacy": [],
            "speed": [],
        }

        logger.info(
            "Executing benchmark run",
            run_id=str(run_id),
            dataset=dataset_name,
            categories=list(requested_categories),
        )

        # Collect speed metrics from a generation timing probe
        if "speed" in requested_categories:
            speed_metrics = await self._collect_speed_metrics(
                run_id, config, dataset_name
            )
            results["speed"].extend(speed_metrics)

        # Collect fidelity metrics via fidelity-validator
        if "fidelity" in requested_categories:
            fidelity_metrics = await self._collect_fidelity_metrics(
                run_id, config, dataset_name
            )
            results["fidelity"].extend(fidelity_metrics)

        # Collect privacy metrics via privacy-engine
        if "privacy" in requested_categories:
            privacy_metrics = await self._collect_privacy_metrics(
                run_id, config, dataset_name
            )
            results["privacy"].extend(privacy_metrics)

        logger.info(
            "Benchmark run execution complete",
            run_id=str(run_id),
            fidelity_count=len(results["fidelity"]),
            privacy_count=len(results["privacy"]),
            speed_count=len(results["speed"]),
        )

        return results

    async def _collect_speed_metrics(
        self,
        run_id: uuid.UUID,
        config: dict[str, Any],
        dataset_name: str,
    ) -> list[dict[str, Any]]:
        """Collect speed metrics by timing a generation probe call.

        Args:
            run_id: BenchmarkRun UUID.
            config: Benchmark configuration.
            dataset_name: Dataset name.

        Returns:
            List of speed metric dicts.
        """
        import time

        row_count = config.get("dataset", {}).get("rows", 1000)

        try:
            async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                start = time.monotonic()
                response = await client.post(
                    f"{self._tabular_url}/api/v1/generate/probe",
                    json={"dataset_name": dataset_name, "rows": row_count},
                )
                elapsed_ms = (time.monotonic() - start) * 1000.0

                if response.status_code == 200:
                    rows_per_second = row_count / (elapsed_ms / 1000.0) if elapsed_ms > 0 else 0.0
                    return [
                        {
                            "metric_name": "rows_per_second",
                            "value": round(rows_per_second, 2),
                            "unit": "rows/sec",
                            "higher_is_better": True,
                            "additional_data": {
                                "run_id": str(run_id),
                                "row_count": row_count,
                            },
                        },
                        {
                            "metric_name": "generation_latency_ms",
                            "value": round(elapsed_ms, 2),
                            "unit": "milliseconds",
                            "higher_is_better": False,
                            "additional_data": {"run_id": str(run_id)},
                        },
                    ]
        except Exception as exc:
            logger.warning(
                "Speed metric collection failed — returning stub values",
                run_id=str(run_id),
                error=str(exc),
            )

        # Return stub values when service is unreachable (e.g., in CI without services)
        return [
            {
                "metric_name": "rows_per_second",
                "value": 0.0,
                "unit": "rows/sec",
                "higher_is_better": True,
                "additional_data": {"run_id": str(run_id), "stub": True},
            }
        ]

    async def _collect_fidelity_metrics(
        self,
        run_id: uuid.UUID,
        config: dict[str, Any],
        dataset_name: str,
    ) -> list[dict[str, Any]]:
        """Collect fidelity metrics via aumos-fidelity-validator.

        Args:
            run_id: BenchmarkRun UUID.
            config: Benchmark configuration.
            dataset_name: Dataset name.

        Returns:
            List of fidelity metric dicts.
        """
        try:
            async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                response = await client.post(
                    f"{self._fidelity_url}/api/v1/validate/benchmark",
                    json={"dataset_name": dataset_name, "run_id": str(run_id)},
                )

                if response.status_code == 200:
                    data = response.json()
                    return [
                        {
                            "metric_name": metric["name"],
                            "value": metric["value"],
                            "unit": metric.get("unit", "score_0_1"),
                            "higher_is_better": metric.get("higher_is_better", True),
                            "additional_data": metric.get("details", {}),
                        }
                        for metric in data.get("metrics", [])
                    ]
        except Exception as exc:
            logger.warning(
                "Fidelity metric collection failed — returning stub values",
                run_id=str(run_id),
                error=str(exc),
            )

        # Return stub values when service is unreachable
        return [
            {
                "metric_name": "tv_complement",
                "value": 0.0,
                "unit": "score_0_1",
                "higher_is_better": True,
                "additional_data": {"run_id": str(run_id), "stub": True},
            }
        ]

    async def _collect_privacy_metrics(
        self,
        run_id: uuid.UUID,
        config: dict[str, Any],
        dataset_name: str,
    ) -> list[dict[str, Any]]:
        """Collect privacy metrics via aumos-privacy-engine.

        Args:
            run_id: BenchmarkRun UUID.
            config: Benchmark configuration.
            dataset_name: Dataset name.

        Returns:
            List of privacy metric dicts.
        """
        try:
            async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                response = await client.post(
                    f"{self._privacy_url}/api/v1/assess/benchmark",
                    json={"dataset_name": dataset_name, "run_id": str(run_id)},
                )

                if response.status_code == 200:
                    data = response.json()
                    return [
                        {
                            "metric_name": metric["name"],
                            "value": metric["value"],
                            "unit": metric.get("unit", "score_0_1"),
                            "higher_is_better": metric.get("higher_is_better", True),
                            "additional_data": metric.get("details", {}),
                        }
                        for metric in data.get("metrics", [])
                    ]
        except Exception as exc:
            logger.warning(
                "Privacy metric collection failed — returning stub values",
                run_id=str(run_id),
                error=str(exc),
            )

        # Return stub values when service is unreachable
        return [
            {
                "metric_name": "dcr_score",
                "value": 0.0,
                "unit": "score_0_1",
                "higher_is_better": True,
                "additional_data": {"run_id": str(run_id), "stub": True},
            }
        ]
