"""DataRobot competitor baseline adapter for the AumOS Benchmark Suite.

GAP-464: Adds DataRobot to the supported competitor baseline set.
"""

from __future__ import annotations

from typing import Any

from aumos_common.observability import get_logger

logger = get_logger(__name__)

COMPETITOR_NAME = "datarobot"

_BASELINE: dict[str, Any] = {
    "fidelity_score": 0.85,
    "privacy_epsilon": 2.1,
    "generation_speed_rows_per_second": 310,
    "modalities_supported": 1,
    "dataset": "synthetic-retail-transactions-10k",
    "measured_at": "2025-11-15",
    "methodology_url": (
        "https://github.com/MuVeraAI/aumos-benchmark-suite"
        "/blob/main/benchmarks/methodology/README.md"
    ),
}


def get_datarobot_baseline() -> dict[str, Any]:
    """Return the pre-recorded DataRobot benchmark baseline.

    Returns:
        Baseline metrics dict with fidelity, privacy, speed, and metadata.
    """
    logger.info("competitor_baseline_fetched", competitor=COMPETITOR_NAME)
    return dict(_BASELINE)
