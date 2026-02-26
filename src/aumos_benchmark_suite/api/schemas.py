"""Pydantic request and response schemas for the Benchmark Suite API.

All API inputs and outputs are typed Pydantic models — never raw dicts.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared / embedded schemas
# ---------------------------------------------------------------------------


class MetricResultResponse(BaseModel):
    """Response schema for a single metric result."""

    id: uuid.UUID
    run_id: uuid.UUID
    metric_category: str
    metric_name: str
    metric_value: float
    metric_unit: str | None
    higher_is_better: bool
    baseline_competitor: str | None
    baseline_value: float | None
    delta_from_baseline: float | None
    additional_data: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Benchmark run schemas
# ---------------------------------------------------------------------------


class BenchmarkRunRequest(BaseModel):
    """Request body for submitting a new benchmark run."""

    name: str = Field(
        ...,
        min_length=3,
        max_length=255,
        description="Human-readable name for this benchmark run",
        examples=["AumOS v1.2.3 tabular fidelity run"],
    )
    config_name: str = Field(
        ...,
        max_length=255,
        description="Name of the benchmark configuration YAML to use",
        examples=["tabular_full_suite"],
    )
    dataset_name: str = Field(
        ...,
        max_length=255,
        description="Name of the dataset to benchmark on",
        examples=["adult_income"],
    )
    aumos_version: str = Field(
        ...,
        max_length=50,
        description="AumOS platform version string under test",
        examples=["1.2.3"],
    )
    run_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Benchmark configuration overrides (merged with the named config YAML)",
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional description of what this run is testing",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Optional tags for filtering and grouping (e.g., ['ci', 'nightly', 'regression'])",
    )
    triggered_by: str | None = Field(
        default=None,
        max_length=100,
        description="What triggered this run: api | ci | schedule | manual",
    )


class BenchmarkRunResponse(BaseModel):
    """Response schema for a benchmark run."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    config_name: str
    dataset_name: str
    aumos_version: str
    status: str
    description: str | None
    dataset_rows: int | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: float | None
    error_message: str | None
    run_config: dict[str, Any]
    tags: list[str]
    triggered_by: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BenchmarkRunListResponse(BaseModel):
    """Paginated list of benchmark runs."""

    items: list[BenchmarkRunResponse]
    total: int
    page: int
    page_size: int


class BenchmarkRunDetailResponse(BenchmarkRunResponse):
    """Detailed run response including metric results."""

    metrics: list[MetricResultResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Metrics schemas
# ---------------------------------------------------------------------------


class AvailableMetricsResponse(BaseModel):
    """Available metric names grouped by category."""

    fidelity: list[str]
    privacy: list[str]
    speed: list[str]


class MetricListResponse(BaseModel):
    """List of metric results for a run."""

    run_id: uuid.UUID
    items: list[MetricResultResponse]
    total: int


# ---------------------------------------------------------------------------
# Competitor baseline schemas
# ---------------------------------------------------------------------------


class CompetitorBaselineUpsertRequest(BaseModel):
    """Request body for creating or updating a competitor baseline."""

    competitor_name: str = Field(
        ...,
        pattern="^(gretel|mostly_ai|tonic)$",
        description="Competitor identifier: gretel | mostly_ai | tonic",
    )
    metric_category: str = Field(
        ...,
        pattern="^(fidelity|privacy|speed)$",
        description="Metric category: fidelity | privacy | speed",
    )
    metric_name: str = Field(
        ...,
        max_length=100,
        description="Specific metric name (must match an available metric name)",
        examples=["ks_statistic"],
    )
    metric_value: float = Field(
        ...,
        description="Baseline numeric value for this competitor on this dataset",
    )
    metric_unit: str | None = Field(
        default=None,
        max_length=50,
        description="Unit of measurement (e.g., score_0_1, rows/sec)",
    )
    higher_is_better: bool = Field(
        default=True,
        description="True if higher values represent better performance",
    )
    dataset_name: str = Field(
        ...,
        max_length=255,
        description="Name of the dataset on which this baseline was measured",
        examples=["adult_income"],
    )
    measured_at: datetime = Field(
        ...,
        description="UTC timestamp when this baseline was measured or published",
    )
    source_url: str | None = Field(
        default=None,
        max_length=500,
        description="URL to the publication or report from which this value was sourced",
    )
    notes: str | None = Field(
        default=None,
        max_length=2000,
        description="Additional context about measurement conditions",
    )


class CompetitorBaselineResponse(BaseModel):
    """Response schema for a competitor baseline."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    competitor_name: str
    metric_category: str
    metric_name: str
    metric_value: float
    metric_unit: str | None
    higher_is_better: bool
    dataset_name: str
    measured_at: datetime
    source_url: str | None
    notes: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CompetitorBaselineListResponse(BaseModel):
    """List response for competitor baselines."""

    items: list[CompetitorBaselineResponse]
    total: int


# ---------------------------------------------------------------------------
# Regression check schemas
# ---------------------------------------------------------------------------


class RegressionCheckRequest(BaseModel):
    """Request body for triggering a regression check."""

    run_id: uuid.UUID = Field(
        ...,
        description="BenchmarkRun UUID to check for regression",
    )
    ci_build_id: str | None = Field(
        default=None,
        max_length=255,
        description="Optional CI/CD build identifier for traceability",
        examples=["github-actions-run-123456789"],
    )
    ci_commit_sha: str | None = Field(
        default=None,
        max_length=40,
        description="Optional git commit SHA (40 hex characters) being tested",
        examples=["a1b2c3d4e5f6..."],
    )


class RegressionCheckResponse(BaseModel):
    """Response schema for a regression check."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    baseline_run_id: uuid.UUID | None
    status: str
    regressed_metrics: list[str]
    details: dict[str, Any]
    checked_at: datetime
    ci_build_id: str | None
    ci_commit_sha: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RegressionCheckListResponse(BaseModel):
    """Paginated list of regression checks."""

    items: list[RegressionCheckResponse]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Report generation schemas
# ---------------------------------------------------------------------------


class ReportGenerateRequest(BaseModel):
    """Request body for generating a benchmark report."""

    run_id: uuid.UUID = Field(
        ...,
        description="BenchmarkRun UUID to generate report for",
    )
    include_competitor_comparison: bool = Field(
        default=True,
        description="Include Gretel / MOSTLY AI / Tonic comparison section",
    )
    include_regression_check: bool = Field(
        default=True,
        description="Include regression analysis vs most recent baseline run",
    )
    formats: list[str] = Field(
        default=["json"],
        description="Output formats: json | html | markdown",
        examples=[["json", "markdown"]],
    )


class ReportResponse(BaseModel):
    """Response schema for a generated benchmark report."""

    run_id: str
    name: str
    config_name: str
    dataset_name: str
    aumos_version: str
    status: str
    started_at: str | None
    completed_at: str | None
    duration_seconds: float | None
    generated_at: str
    formats: list[str]
    summary: dict[str, Any]
    metrics: dict[str, list[dict[str, Any]]]
    competitor_comparison: dict[str, Any] | None = None
    regression: dict[str, Any] | None = None
