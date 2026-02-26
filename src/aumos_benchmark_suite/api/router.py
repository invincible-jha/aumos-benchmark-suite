"""FastAPI router for the AumOS Benchmark Suite REST API.

All endpoints are prefixed with /api/v1/benchmarks. Authentication and tenant
extraction are handled by aumos-auth-gateway upstream; tenant_id is available
via the X-Tenant-ID header.

Business logic is never implemented here — routes delegate entirely to services.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status

from aumos_common.errors import ConflictError, NotFoundError
from aumos_common.observability import get_logger

from aumos_benchmark_suite.api.schemas import (
    AvailableMetricsResponse,
    BenchmarkRunDetailResponse,
    BenchmarkRunListResponse,
    BenchmarkRunRequest,
    BenchmarkRunResponse,
    CompetitorBaselineListResponse,
    CompetitorBaselineResponse,
    CompetitorBaselineUpsertRequest,
    MetricListResponse,
    MetricResultResponse,
    RegressionCheckListResponse,
    RegressionCheckRequest,
    RegressionCheckResponse,
    ReportGenerateRequest,
    ReportResponse,
)
from aumos_benchmark_suite.core.services import (
    BenchmarkRunnerService,
    CompetitorBaselineService,
    MetricService,
    RegressionService,
    ReportGeneratorService,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/benchmarks", tags=["benchmark-suite"])


# ---------------------------------------------------------------------------
# Dependency helpers — replaced by real DI in production startup
# ---------------------------------------------------------------------------


def _get_runner_service(request: Request) -> BenchmarkRunnerService:
    """Retrieve BenchmarkRunnerService from app state.

    Args:
        request: FastAPI request with app state populated in lifespan.

    Returns:
        BenchmarkRunnerService instance.
    """
    return request.app.state.runner_service  # type: ignore[no-any-return]


def _get_metric_service(request: Request) -> MetricService:
    """Retrieve MetricService from app state.

    Args:
        request: FastAPI request with app state populated in lifespan.

    Returns:
        MetricService instance.
    """
    return request.app.state.metric_service  # type: ignore[no-any-return]


def _get_baseline_service(request: Request) -> CompetitorBaselineService:
    """Retrieve CompetitorBaselineService from app state.

    Args:
        request: FastAPI request with app state populated in lifespan.

    Returns:
        CompetitorBaselineService instance.
    """
    return request.app.state.baseline_service  # type: ignore[no-any-return]


def _get_regression_service(request: Request) -> RegressionService:
    """Retrieve RegressionService from app state.

    Args:
        request: FastAPI request with app state populated in lifespan.

    Returns:
        RegressionService instance.
    """
    return request.app.state.regression_service  # type: ignore[no-any-return]


def _get_report_service(request: Request) -> ReportGeneratorService:
    """Retrieve ReportGeneratorService from app state.

    Args:
        request: FastAPI request with app state populated in lifespan.

    Returns:
        ReportGeneratorService instance.
    """
    return request.app.state.report_service  # type: ignore[no-any-return]


def _tenant_id_from_request(request: Request) -> uuid.UUID:
    """Extract tenant UUID from request headers (set by auth middleware).

    Falls back to a random UUID in development mode.

    Args:
        request: Incoming FastAPI request.

    Returns:
        Tenant UUID.
    """
    tenant_header = request.headers.get("X-Tenant-ID")
    if tenant_header:
        return uuid.UUID(tenant_header)
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Benchmark run endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/run",
    response_model=BenchmarkRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run benchmark suite",
    description=(
        "Submit and execute a benchmark run against AumOS. "
        "Executes synchronously and returns results when complete. "
        "Compares fidelity, privacy, and speed metrics against competitor baselines."
    ),
)
async def run_benchmark(
    request_body: BenchmarkRunRequest,
    request: Request,
    service: BenchmarkRunnerService = Depends(_get_runner_service),
) -> BenchmarkRunResponse:
    """Submit and execute a benchmark run.

    Args:
        request_body: Benchmark run parameters.
        request: FastAPI request for tenant extraction.
        service: BenchmarkRunnerService dependency.

    Returns:
        BenchmarkRunResponse for the completed or failed run.

    Raises:
        HTTPException 400: If configuration is invalid.
    """
    tenant_id = _tenant_id_from_request(request)

    try:
        run = await service.submit_run(
            tenant_id=tenant_id,
            name=request_body.name,
            config_name=request_body.config_name,
            dataset_name=request_body.dataset_name,
            aumos_version=request_body.aumos_version,
            run_config=request_body.run_config,
            description=request_body.description,
            tags=request_body.tags,
            triggered_by=request_body.triggered_by,
        )
    except ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    logger.info(
        "Benchmark run API call",
        tenant_id=str(tenant_id),
        run_id=str(run.id),
        status=run.status,
    )
    return BenchmarkRunResponse.model_validate(run)


@router.get(
    "/runs",
    response_model=BenchmarkRunListResponse,
    summary="List benchmark runs",
    description="List all benchmark runs for the current tenant with pagination.",
)
async def list_runs(
    page: int = 1,
    page_size: int = 20,
    status_filter: str | None = None,
    config_name: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
    service: BenchmarkRunnerService = Depends(_get_runner_service),
) -> BenchmarkRunListResponse:
    """List benchmark runs for the current tenant.

    Args:
        page: 1-based page number (default 1).
        page_size: Results per page (default 20, max 100).
        status_filter: Optional status to filter by.
        config_name: Optional config name to filter by.
        request: FastAPI request for tenant extraction.
        service: BenchmarkRunnerService dependency.

    Returns:
        BenchmarkRunListResponse with pagination metadata.
    """
    tenant_id = _tenant_id_from_request(request)
    runs, total = await service.list_runs(
        tenant_id=tenant_id,
        page=page,
        page_size=min(page_size, 100),
        status=status_filter,
        config_name=config_name,
    )

    return BenchmarkRunListResponse(
        items=[BenchmarkRunResponse.model_validate(r) for r in runs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/runs/{run_id}",
    response_model=BenchmarkRunDetailResponse,
    summary="Get benchmark run results",
    description="Retrieve a single benchmark run with all its metric results.",
)
async def get_run(
    run_id: uuid.UUID,
    request: Request,
    runner_service: BenchmarkRunnerService = Depends(_get_runner_service),
    metric_service: MetricService = Depends(_get_metric_service),
) -> BenchmarkRunDetailResponse:
    """Retrieve a benchmark run with all metric results.

    Args:
        run_id: BenchmarkRun UUID.
        request: FastAPI request for tenant extraction.
        runner_service: BenchmarkRunnerService dependency.
        metric_service: MetricService dependency.

    Returns:
        BenchmarkRunDetailResponse with metrics.

    Raises:
        HTTPException 404: If run not found.
    """
    tenant_id = _tenant_id_from_request(request)

    try:
        run = await runner_service.get_run(run_id, tenant_id)
        metrics = await metric_service.get_metrics_for_run(run_id, tenant_id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    response = BenchmarkRunDetailResponse.model_validate(run)
    response.metrics = [MetricResultResponse.model_validate(m) for m in metrics]
    return response


# ---------------------------------------------------------------------------
# Metrics endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/metrics",
    response_model=AvailableMetricsResponse,
    summary="Available metrics",
    description=(
        "Return the complete catalogue of benchmark metrics available, "
        "grouped by category (fidelity, privacy, speed)."
    ),
)
async def get_available_metrics(
    request: Request,
    service: MetricService = Depends(_get_metric_service),
) -> AvailableMetricsResponse:
    """Return available metric names grouped by category.

    Args:
        request: FastAPI request (unused — no tenant filtering needed).
        service: MetricService dependency.

    Returns:
        AvailableMetricsResponse with per-category metric names.
    """
    metrics = await service.get_available_metrics()
    return AvailableMetricsResponse(
        fidelity=metrics["fidelity"],
        privacy=metrics["privacy"],
        speed=metrics["speed"],
    )


@router.get(
    "/runs/{run_id}/metrics",
    response_model=MetricListResponse,
    summary="Get run metrics",
    description="Retrieve all metric results for a specific benchmark run.",
)
async def get_run_metrics(
    run_id: uuid.UUID,
    category: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
    service: MetricService = Depends(_get_metric_service),
) -> MetricListResponse:
    """Retrieve metric results for a run.

    Args:
        run_id: BenchmarkRun UUID.
        category: Optional metric category filter (fidelity | privacy | speed).
        request: FastAPI request for tenant extraction.
        service: MetricService dependency.

    Returns:
        MetricListResponse with metric results.

    Raises:
        HTTPException 404: If run not found.
        HTTPException 400: If category is invalid.
    """
    tenant_id = _tenant_id_from_request(request)

    try:
        metrics = await service.get_metrics_for_run(run_id, tenant_id, category)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return MetricListResponse(
        run_id=run_id,
        items=[MetricResultResponse.model_validate(m) for m in metrics],
        total=len(metrics),
    )


# ---------------------------------------------------------------------------
# Competitor baseline endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/baselines",
    response_model=CompetitorBaselineListResponse,
    summary="Competitor baselines",
    description=(
        "List all competitor performance baselines (Gretel, MOSTLY AI, Tonic). "
        "Optionally filter by competitor name."
    ),
)
async def list_baselines(
    competitor: str | None = None,
    active_only: bool = True,
    request: Request = ...,  # type: ignore[assignment]
    service: CompetitorBaselineService = Depends(_get_baseline_service),
) -> CompetitorBaselineListResponse:
    """List competitor performance baselines.

    Args:
        competitor: Optional competitor filter (gretel | mostly_ai | tonic).
        active_only: If True, exclude soft-deleted baselines.
        request: FastAPI request for tenant extraction.
        service: CompetitorBaselineService dependency.

    Returns:
        CompetitorBaselineListResponse with baselines.

    Raises:
        HTTPException 400: If competitor name is invalid.
    """
    tenant_id = _tenant_id_from_request(request)

    try:
        baselines = await service.list_baselines(tenant_id, competitor, active_only)
    except ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return CompetitorBaselineListResponse(
        items=[CompetitorBaselineResponse.model_validate(b) for b in baselines],
        total=len(baselines),
    )


@router.put(
    "/baselines",
    response_model=CompetitorBaselineResponse,
    status_code=status.HTTP_200_OK,
    summary="Upsert competitor baseline",
    description=(
        "Create or update a competitor performance baseline. "
        "Uses competitor_name + metric_name + dataset_name as the unique key."
    ),
)
async def upsert_baseline(
    request_body: CompetitorBaselineUpsertRequest,
    request: Request,
    service: CompetitorBaselineService = Depends(_get_baseline_service),
) -> CompetitorBaselineResponse:
    """Create or update a competitor performance baseline.

    Args:
        request_body: Baseline parameters.
        request: FastAPI request for tenant extraction.
        service: CompetitorBaselineService dependency.

    Returns:
        CompetitorBaselineResponse for the created or updated baseline.

    Raises:
        HTTPException 400: If competitor_name or metric_category is invalid.
    """
    tenant_id = _tenant_id_from_request(request)

    try:
        baseline = await service.upsert_baseline(
            tenant_id=tenant_id,
            competitor_name=request_body.competitor_name,
            metric_category=request_body.metric_category,
            metric_name=request_body.metric_name,
            metric_value=request_body.metric_value,
            dataset_name=request_body.dataset_name,
            measured_at=request_body.measured_at,
            higher_is_better=request_body.higher_is_better,
            metric_unit=request_body.metric_unit,
            source_url=request_body.source_url,
            notes=request_body.notes,
        )
    except ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return CompetitorBaselineResponse.model_validate(baseline)


# ---------------------------------------------------------------------------
# Regression check endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/regression/check",
    response_model=RegressionCheckResponse,
    status_code=status.HTTP_200_OK,
    summary="Check for regression",
    description=(
        "Check a completed benchmark run for performance regressions against "
        "the most recently completed run of the same configuration. "
        "Used in CI pipelines to gate releases on benchmark quality."
    ),
)
async def check_regression(
    request_body: RegressionCheckRequest,
    request: Request,
    service: RegressionService = Depends(_get_regression_service),
) -> RegressionCheckResponse:
    """Perform a regression check on a completed benchmark run.

    Args:
        request_body: Regression check parameters.
        request: FastAPI request for tenant extraction.
        service: RegressionService dependency.

    Returns:
        RegressionCheckResponse with status and details.

    Raises:
        HTTPException 404: If run not found.
        HTTPException 400: If run is not in completed status.
    """
    tenant_id = _tenant_id_from_request(request)

    try:
        regression = await service.check_regression(
            tenant_id=tenant_id,
            run_id=request_body.run_id,
            ci_build_id=request_body.ci_build_id,
            ci_commit_sha=request_body.ci_commit_sha,
        )
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return RegressionCheckResponse.model_validate(regression)


# ---------------------------------------------------------------------------
# Report generation endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/reports/generate",
    response_model=ReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate benchmark report",
    description=(
        "Generate a comprehensive benchmark report for a completed run. "
        "Includes metric summaries, Pareto curve data, competitor comparisons, "
        "and regression analysis."
    ),
)
async def generate_report(
    request_body: ReportGenerateRequest,
    request: Request,
    service: ReportGeneratorService = Depends(_get_report_service),
) -> ReportResponse:
    """Generate a benchmark report for a completed run.

    Args:
        request_body: Report generation parameters.
        request: FastAPI request for tenant extraction.
        service: ReportGeneratorService dependency.

    Returns:
        ReportResponse with structured benchmark analysis.

    Raises:
        HTTPException 404: If run not found.
        HTTPException 400: If run is not completed.
    """
    tenant_id = _tenant_id_from_request(request)

    try:
        report = await service.generate_report(
            tenant_id=tenant_id,
            run_id=request_body.run_id,
            include_competitor_comparison=request_body.include_competitor_comparison,
            include_regression_check=request_body.include_regression_check,
            formats=request_body.formats,
        )
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return ReportResponse(**report)
