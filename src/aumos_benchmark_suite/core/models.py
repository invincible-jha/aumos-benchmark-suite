"""SQLAlchemy ORM models for the AumOS Benchmark Suite service.

All tables use the `bnk_` prefix. Tenant-scoped tables extend AumOSModel
which supplies id (UUID), tenant_id, created_at, and updated_at columns.

Domain model:
  BenchmarkRun           — top-level benchmark execution record
  MetricResult           — individual metric measurement per run (fidelity, privacy, speed)
  CompetitorBaseline     — static competitor performance reference data
  RegressionCheck        — CI regression check results comparing a run to a baseline
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aumos_common.database import AumOSModel


class BenchmarkRun(AumOSModel):
    """A top-level benchmark execution record.

    Tracks the lifecycle of a full benchmark suite run from submission
    through execution to completion or failure.

    Status transitions:
        pending → running → completed
        pending → running → failed
        pending → cancelled

    Table: bnk_benchmark_runs
    """

    __tablename__ = "bnk_benchmark_runs"

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Human-readable name for this benchmark run",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Optional description of what this benchmark run is testing",
    )
    config_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Name of the benchmark configuration YAML used for this run",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        index=True,
        comment="pending | running | completed | failed | cancelled",
    )
    dataset_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Name of the dataset used in this benchmark run",
    )
    dataset_rows: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Number of rows in the dataset used for this run",
    )
    aumos_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="AumOS platform version string under test (e.g., 1.2.3)",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC timestamp when run execution began",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC timestamp when run reached a terminal state",
    )
    duration_seconds: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Total run duration in seconds (completed_at - started_at)",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Error detail when status=failed",
    )
    run_config: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="Full benchmark configuration snapshot used for this run",
    )
    tags: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        comment="Optional tags for filtering and grouping runs (e.g., ['ci', 'nightly'])",
    )
    triggered_by: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="What triggered this run: api | ci | schedule | manual",
    )

    metric_results: Mapped[list["MetricResult"]] = relationship(
        "MetricResult",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="MetricResult.metric_category",
    )
    regression_checks: Mapped[list["RegressionCheck"]] = relationship(
        "RegressionCheck",
        back_populates="run",
        cascade="all, delete-orphan",
    )


class MetricResult(AumOSModel):
    """An individual metric measurement from a benchmark run.

    Stores a single metric value (fidelity, privacy, or speed) along with
    comparison data against a competitor baseline if available.

    Metric categories:
        fidelity — statistical similarity between real and synthetic data
        privacy  — re-identification risk and membership inference scores
        speed    — rows per second, generation latency, memory utilization

    Table: bnk_metric_results
    """

    __tablename__ = "bnk_metric_results"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bnk_benchmark_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Parent BenchmarkRun UUID",
    )
    metric_category: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="fidelity | privacy | speed",
    )
    metric_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment=(
            "Specific metric name within category. "
            "Examples: ks_statistic, tvd, membership_inference_auc, rows_per_second"
        ),
    )
    metric_value: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Numeric metric value (interpretation depends on metric_name)",
    )
    metric_unit: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Unit of measurement (e.g., rows/sec, milliseconds, score_0_1)",
    )
    higher_is_better: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="True if higher values are better for this metric (e.g., fidelity score)",
    )
    baseline_competitor: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="Competitor name if this metric is a baseline reference (gretel | mostly_ai | tonic | aumos)",
    )
    baseline_value: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Competitor baseline value for comparison (NULL if no baseline exists)",
    )
    delta_from_baseline: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment=(
            "Difference between metric_value and baseline_value "
            "(positive means AumOS is better when higher_is_better=True)"
        ),
    )
    additional_data: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="Additional metric metadata (confidence intervals, sample sizes, breakdown by column, etc.)",
    )

    run: Mapped["BenchmarkRun"] = relationship(
        "BenchmarkRun",
        back_populates="metric_results",
    )


class CompetitorBaseline(AumOSModel):
    """Static competitor performance baseline reference data.

    Stores the published or measured performance scores for competitor
    products (Gretel, MOSTLY AI, Tonic) to enable relative comparisons.

    Baselines are refreshed periodically from official benchmark publications
    or controlled internal evaluations.

    Table: bnk_competitor_baselines
    """

    __tablename__ = "bnk_competitor_baselines"
    __table_args__ = (
        UniqueConstraint(
            "competitor_name",
            "metric_name",
            "dataset_name",
            name="uq_bnk_competitor_baselines_competitor_metric_dataset",
        ),
    )

    competitor_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Competitor identifier: gretel | mostly_ai | tonic",
    )
    metric_category: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="fidelity | privacy | speed",
    )
    metric_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Specific metric name matching MetricResult.metric_name",
    )
    metric_value: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Baseline metric value for this competitor on this dataset",
    )
    metric_unit: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Unit of measurement consistent with MetricResult.metric_unit",
    )
    higher_is_better: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="True if higher values are better for this metric",
    )
    dataset_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Name of the dataset on which this baseline was measured",
    )
    measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="UTC timestamp when this baseline was recorded or last verified",
    )
    source_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="URL to the publication or benchmark report from which this value was sourced",
    )
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Additional context about conditions under which baseline was measured",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Soft-delete flag — inactive baselines are excluded from comparisons",
    )


class RegressionCheck(AumOSModel):
    """CI regression check result comparing a benchmark run to historical baselines.

    Captures whether a new benchmark run represents a statistically significant
    regression from the previous stable run on the same configuration.

    Status values:
        passed   — no metrics regressed beyond threshold
        failed   — one or more metrics regressed beyond threshold
        skipped  — no baseline exists for comparison

    Table: bnk_regression_checks
    """

    __tablename__ = "bnk_regression_checks"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bnk_benchmark_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="BenchmarkRun UUID being checked for regressions",
    )
    baseline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="BenchmarkRun UUID used as the comparison baseline (NULL if none exists)",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="skipped",
        index=True,
        comment="passed | failed | skipped",
    )
    regressed_metrics: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        comment=(
            "List of metric names that regressed beyond threshold. "
            "Empty list when status=passed or status=skipped."
        ),
    )
    details: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment=(
            "Full regression analysis: per-metric delta, threshold applied, pass/fail. "
            "Example: {metric_name: {delta, threshold, passed, current_value, baseline_value}}"
        ),
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="UTC timestamp when this regression check was performed",
    )
    ci_build_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="CI/CD build identifier (e.g., GitHub Actions run ID) that triggered this check",
    )
    ci_commit_sha: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
        comment="Git commit SHA being tested in this regression check",
    )

    run: Mapped["BenchmarkRun"] = relationship(
        "BenchmarkRun",
        back_populates="regression_checks",
    )
