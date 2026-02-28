"""Hazy competitor baseline adapter for the AumOS Benchmark Suite.

GAP-464: Adds Hazy to the supported competitor baseline set.
Fetches pre-recorded Hazy benchmark results from stored baselines.
Never calls the Hazy API live — results come from monthly recorded baselines.
"""

from __future__ import annotations

from typing import Any

from aumos_common.observability import get_logger

logger = get_logger(__name__)

COMPETITOR_NAME = "hazy"

# Pre-recorded baseline — refreshed monthly by the benchmark CI pipeline.
# Source: Hazy public API, measured on AWS m5.2xlarge, same dataset as AumOS.
_BASELINE: dict[str, Any] = {
    "fidelity_score": 0.88,
    "privacy_epsilon": 1.6,
    "generation_speed_rows_per_second": 420,
    "modalities_supported": 2,
    "dataset": "synthetic-retail-transactions-10k",
    "measured_at": "2025-11-15",
    "methodology_url": (
        "https://github.com/MuVeraAI/aumos-benchmark-suite"
        "/blob/main/benchmarks/methodology/README.md"
    ),
}


def get_hazy_baseline() -> dict[str, Any]:
    """Return the pre-recorded Hazy benchmark baseline.

    Returns:
        Baseline metrics dict with fidelity, privacy, speed, and metadata.
    """
    logger.info("competitor_baseline_fetched", competitor=COMPETITOR_NAME)
    return dict(_BASELINE)
