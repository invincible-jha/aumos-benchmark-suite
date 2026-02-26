"""Benchmark Suite service settings extending AumOS base configuration."""

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from aumos_common.config import AumOSSettings


class Settings(AumOSSettings):
    """Configuration for the AumOS Benchmark Suite service.

    Extends base AumOS settings with benchmark-specific configuration
    for run execution, competitor baselines, and report generation.

    All settings use the AUMOS_BENCHMARK_ environment variable prefix.
    """

    service_name: str = "aumos-benchmark-suite"

    # ---------------------------------------------------------------------------
    # Benchmark runner configuration
    # ---------------------------------------------------------------------------
    max_concurrent_runs: int = Field(
        default=4,
        description="Maximum number of benchmark runs that can execute in parallel",
    )
    run_timeout_seconds: int = Field(
        default=3600,
        description="Maximum execution time in seconds per benchmark run before timeout",
    )
    dataset_storage_path: str = Field(
        default="/data/benchmarks/datasets",
        description="Local filesystem path where benchmark datasets are stored",
    )
    config_path: str = Field(
        default="/data/benchmarks/configs",
        description="Local filesystem path where benchmark YAML configs are stored",
    )

    # ---------------------------------------------------------------------------
    # Metric thresholds
    # ---------------------------------------------------------------------------
    fidelity_regression_threshold: float = Field(
        default=0.05,
        description=(
            "Maximum allowable fidelity score drop (0–1) before a regression is flagged. "
            "A drop of 0.05 means a 5-point fidelity decrease triggers a regression alert."
        ),
    )
    privacy_regression_threshold: float = Field(
        default=0.03,
        description=(
            "Maximum allowable privacy score drop (0–1) before a regression is flagged. "
            "Privacy regressions are treated with higher urgency than fidelity regressions."
        ),
    )
    speed_regression_threshold_percent: float = Field(
        default=20.0,
        description=(
            "Maximum allowable throughput decrease as a percentage before flagging regression. "
            "A 20% slowdown in rows-per-second triggers an alert."
        ),
    )

    # ---------------------------------------------------------------------------
    # Competitor baseline configuration
    # ---------------------------------------------------------------------------
    baseline_staleness_days: int = Field(
        default=30,
        description=(
            "Number of days after which competitor baseline data is considered stale "
            "and should be refreshed from official benchmark publications."
        ),
    )

    # ---------------------------------------------------------------------------
    # Report generation
    # ---------------------------------------------------------------------------
    report_output_path: str = Field(
        default="/data/benchmarks/reports",
        description="Filesystem path where generated benchmark reports are written",
    )
    report_formats: list[str] = Field(
        default=["json", "html", "markdown"],
        description="Output formats for generated benchmark reports",
    )

    # ---------------------------------------------------------------------------
    # Upstream service URLs
    # ---------------------------------------------------------------------------
    tabular_engine_url: str = Field(
        default="http://localhost:8004",
        description="Base URL for aumos-tabular-engine (primary data generation for benchmarks)",
    )
    privacy_engine_url: str = Field(
        default="http://localhost:8010",
        description="Base URL for aumos-privacy-engine (privacy metric computation)",
    )
    fidelity_validator_url: str = Field(
        default="http://localhost:8013",
        description="Base URL for aumos-fidelity-validator (fidelity metric computation)",
    )

    # ---------------------------------------------------------------------------
    # HTTP client settings
    # ---------------------------------------------------------------------------
    http_timeout: float = Field(
        default=60.0,
        description="Timeout in seconds for HTTP calls to downstream services",
    )
    http_max_retries: int = Field(
        default=3,
        description="Maximum retry attempts for HTTP calls to upstream services",
    )

    model_config = SettingsConfigDict(env_prefix="AUMOS_BENCHMARK_")
