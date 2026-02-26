"""SQLAlchemy repository implementations for the Benchmark Suite service.

All repositories extend BaseRepository from aumos-common and implement the
Protocol interfaces defined in core/interfaces.py.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aumos_common.database import BaseRepository
from aumos_common.observability import get_logger

from aumos_benchmark_suite.core.models import (
    BenchmarkRun,
    CompetitorBaseline,
    MetricResult,
    RegressionCheck,
)

logger = get_logger(__name__)


class BenchmarkRunRepository(BaseRepository[BenchmarkRun]):
    """Persistence for BenchmarkRun entities.

    Extends BaseRepository which provides RLS-enforced CRUD and pagination.
    All queries are tenant-scoped via aumos-common's RLS session setup.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialise with an async SQLAlchemy session.

        Args:
            session: AsyncSession with RLS context already set.
        """
        super().__init__(session, BenchmarkRun)

    async def create(
        self,
        tenant_id: uuid.UUID,
        name: str,
        config_name: str,
        dataset_name: str,
        aumos_version: str,
        run_config: dict[str, Any],
        description: str | None,
        tags: list[str],
        triggered_by: str | None,
    ) -> BenchmarkRun:
        """Create and persist a new BenchmarkRun in pending status.

        Args:
            tenant_id: Owning tenant UUID.
            name: Human-readable run name.
            config_name: Benchmark configuration YAML name.
            dataset_name: Dataset name.
            aumos_version: AumOS version string.
            run_config: Full configuration snapshot.
            description: Optional description.
            tags: Tag list for filtering.
            triggered_by: Trigger source.

        Returns:
            Newly created BenchmarkRun with status=pending.
        """
        run = BenchmarkRun(
            tenant_id=tenant_id,
            name=name,
            config_name=config_name,
            dataset_name=dataset_name,
            aumos_version=aumos_version,
            run_config=run_config,
            description=description,
            tags=tags,
            triggered_by=triggered_by,
            status="pending",
        )
        self._session.add(run)
        await self._session.flush()
        await self._session.refresh(run)
        return run

    async def get_by_id(
        self, run_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> BenchmarkRun | None:
        """Retrieve a run by UUID within a tenant.

        Args:
            run_id: BenchmarkRun UUID.
            tenant_id: Requesting tenant for scoping.

        Returns:
            BenchmarkRun or None if not found.
        """
        result = await self._session.execute(
            select(BenchmarkRun).where(
                BenchmarkRun.id == run_id,
                BenchmarkRun.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        page: int,
        page_size: int,
        status: str | None,
        config_name: str | None,
    ) -> tuple[list[BenchmarkRun], int]:
        """List runs for a tenant with pagination and optional filters.

        Args:
            tenant_id: Requesting tenant.
            page: 1-based page number.
            page_size: Results per page.
            status: Optional status filter.
            config_name: Optional config name filter.

        Returns:
            Tuple of (runs, total_count).
        """
        query = select(BenchmarkRun).where(BenchmarkRun.tenant_id == tenant_id)

        if status is not None:
            query = query.where(BenchmarkRun.status == status)
        if config_name is not None:
            query = query.where(BenchmarkRun.config_name == config_name)

        count_result = await self._session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        offset = (page - 1) * page_size
        result = await self._session.execute(
            query.order_by(BenchmarkRun.created_at.desc()).offset(offset).limit(page_size)
        )
        return list(result.scalars().all()), total

    async def update_status(
        self,
        run_id: uuid.UUID,
        status: str,
        started_at: datetime | None,
        completed_at: datetime | None,
        duration_seconds: float | None,
        error_message: str | None,
    ) -> BenchmarkRun:
        """Update run status and lifecycle timestamps.

        Args:
            run_id: BenchmarkRun UUID.
            status: New status value.
            started_at: Optional execution start timestamp.
            completed_at: Optional completion timestamp.
            duration_seconds: Optional total duration.
            error_message: Optional error detail.

        Returns:
            Updated BenchmarkRun.
        """
        values: dict[str, Any] = {"status": status}
        if started_at is not None:
            values["started_at"] = started_at
        if completed_at is not None:
            values["completed_at"] = completed_at
        if duration_seconds is not None:
            values["duration_seconds"] = duration_seconds
        if error_message is not None:
            values["error_message"] = error_message

        await self._session.execute(
            update(BenchmarkRun).where(BenchmarkRun.id == run_id).values(**values)
        )
        await self._session.flush()

        result = await self._session.execute(
            select(BenchmarkRun).where(BenchmarkRun.id == run_id)
        )
        return result.scalar_one()

    async def get_latest_completed(
        self,
        tenant_id: uuid.UUID,
        config_name: str,
    ) -> BenchmarkRun | None:
        """Retrieve the most recently completed run for a given config.

        Args:
            tenant_id: Requesting tenant.
            config_name: Benchmark configuration name.

        Returns:
            Most recent completed BenchmarkRun or None.
        """
        result = await self._session.execute(
            select(BenchmarkRun)
            .where(
                BenchmarkRun.tenant_id == tenant_id,
                BenchmarkRun.config_name == config_name,
                BenchmarkRun.status == "completed",
            )
            .order_by(BenchmarkRun.completed_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


class MetricResultRepository(BaseRepository[MetricResult]):
    """Persistence for MetricResult entities."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialise with an async SQLAlchemy session.

        Args:
            session: AsyncSession with RLS context already set.
        """
        super().__init__(session, MetricResult)

    async def create_bulk(
        self,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        metrics: list[dict[str, Any]],
    ) -> list[MetricResult]:
        """Bulk-create metric results for a benchmark run.

        Args:
            tenant_id: Owning tenant UUID.
            run_id: Parent BenchmarkRun UUID.
            metrics: List of metric dicts with all MetricResult fields.

        Returns:
            List of created MetricResult instances.
        """
        created: list[MetricResult] = []
        for metric_dict in metrics:
            metric = MetricResult(
                tenant_id=tenant_id,
                run_id=run_id,
                metric_category=metric_dict["metric_category"],
                metric_name=metric_dict["metric_name"],
                metric_value=metric_dict["metric_value"],
                metric_unit=metric_dict.get("metric_unit"),
                higher_is_better=metric_dict.get("higher_is_better", True),
                baseline_competitor=metric_dict.get("baseline_competitor"),
                baseline_value=metric_dict.get("baseline_value"),
                delta_from_baseline=metric_dict.get("delta_from_baseline"),
                additional_data=metric_dict.get("additional_data", {}),
            )
            self._session.add(metric)
            created.append(metric)

        await self._session.flush()
        for metric in created:
            await self._session.refresh(metric)

        return created

    async def list_by_run(
        self,
        run_id: uuid.UUID,
        category: str | None,
    ) -> list[MetricResult]:
        """Retrieve metric results for a run, optionally filtered by category.

        Args:
            run_id: Parent BenchmarkRun UUID.
            category: Optional category filter.

        Returns:
            List of MetricResult instances.
        """
        query = select(MetricResult).where(MetricResult.run_id == run_id)
        if category is not None:
            query = query.where(MetricResult.metric_category == category)

        result = await self._session.execute(
            query.order_by(
                MetricResult.metric_category.asc(),
                MetricResult.metric_name.asc(),
            )
        )
        return list(result.scalars().all())

    async def get_summary(
        self,
        run_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Aggregate metric summary statistics for a run.

        Args:
            run_id: BenchmarkRun UUID.
            tenant_id: Requesting tenant.

        Returns:
            Dict with per-category counts and overall metric count.
        """
        total_result = await self._session.execute(
            select(func.count(MetricResult.id)).where(
                MetricResult.run_id == run_id,
                MetricResult.tenant_id == tenant_id,
            )
        )
        total = total_result.scalar_one()

        fidelity_result = await self._session.execute(
            select(func.avg(MetricResult.metric_value)).where(
                MetricResult.run_id == run_id,
                MetricResult.metric_category == "fidelity",
            )
        )
        avg_fidelity = fidelity_result.scalar_one()

        privacy_result = await self._session.execute(
            select(func.avg(MetricResult.metric_value)).where(
                MetricResult.run_id == run_id,
                MetricResult.metric_category == "privacy",
            )
        )
        avg_privacy = privacy_result.scalar_one()

        return {
            "total_metrics": total,
            "avg_fidelity_score": round(float(avg_fidelity), 4) if avg_fidelity else None,
            "avg_privacy_score": round(float(avg_privacy), 4) if avg_privacy else None,
        }


class CompetitorBaselineRepository(BaseRepository[CompetitorBaseline]):
    """Persistence for CompetitorBaseline entities."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialise with an async SQLAlchemy session.

        Args:
            session: AsyncSession with RLS context already set.
        """
        super().__init__(session, CompetitorBaseline)

    async def create(
        self,
        tenant_id: uuid.UUID,
        competitor_name: str,
        metric_category: str,
        metric_name: str,
        metric_value: float,
        dataset_name: str,
        measured_at: datetime,
        higher_is_better: bool,
        metric_unit: str | None,
        source_url: str | None,
        notes: str | None,
    ) -> CompetitorBaseline:
        """Create a competitor baseline record.

        Args:
            tenant_id: Owning tenant UUID.
            competitor_name: Competitor identifier.
            metric_category: fidelity | privacy | speed.
            metric_name: Specific metric name.
            metric_value: Baseline value.
            dataset_name: Dataset name.
            measured_at: Measurement timestamp.
            higher_is_better: Metric direction.
            metric_unit: Unit of measurement.
            source_url: Optional source URL.
            notes: Optional context.

        Returns:
            Newly created CompetitorBaseline.
        """
        baseline = CompetitorBaseline(
            tenant_id=tenant_id,
            competitor_name=competitor_name,
            metric_category=metric_category,
            metric_name=metric_name,
            metric_value=metric_value,
            dataset_name=dataset_name,
            measured_at=measured_at,
            higher_is_better=higher_is_better,
            metric_unit=metric_unit,
            source_url=source_url,
            notes=notes,
            is_active=True,
        )
        self._session.add(baseline)
        await self._session.flush()
        await self._session.refresh(baseline)
        return baseline

    async def get_by_competitor_and_metric(
        self,
        competitor_name: str,
        metric_name: str,
        dataset_name: str,
        tenant_id: uuid.UUID,
    ) -> CompetitorBaseline | None:
        """Retrieve a baseline by the unique competitor+metric+dataset combination.

        Args:
            competitor_name: Competitor identifier.
            metric_name: Specific metric name.
            dataset_name: Dataset name.
            tenant_id: Requesting tenant.

        Returns:
            CompetitorBaseline or None if not found.
        """
        result = await self._session.execute(
            select(CompetitorBaseline).where(
                CompetitorBaseline.competitor_name == competitor_name,
                CompetitorBaseline.metric_name == metric_name,
                CompetitorBaseline.dataset_name == dataset_name,
                CompetitorBaseline.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_competitor(
        self,
        competitor_name: str,
        tenant_id: uuid.UUID,
        active_only: bool,
    ) -> list[CompetitorBaseline]:
        """List all baselines for a specific competitor.

        Args:
            competitor_name: Competitor identifier.
            tenant_id: Requesting tenant.
            active_only: If True, exclude soft-deleted baselines.

        Returns:
            List of CompetitorBaseline instances.
        """
        query = select(CompetitorBaseline).where(
            CompetitorBaseline.competitor_name == competitor_name,
            CompetitorBaseline.tenant_id == tenant_id,
        )
        if active_only:
            query = query.where(CompetitorBaseline.is_active.is_(True))

        result = await self._session.execute(
            query.order_by(
                CompetitorBaseline.metric_category.asc(),
                CompetitorBaseline.metric_name.asc(),
            )
        )
        return list(result.scalars().all())

    async def list_all(
        self,
        tenant_id: uuid.UUID,
        active_only: bool,
    ) -> list[CompetitorBaseline]:
        """List all competitor baselines.

        Args:
            tenant_id: Requesting tenant.
            active_only: If True, exclude soft-deleted baselines.

        Returns:
            List of all CompetitorBaseline instances.
        """
        query = select(CompetitorBaseline).where(
            CompetitorBaseline.tenant_id == tenant_id
        )
        if active_only:
            query = query.where(CompetitorBaseline.is_active.is_(True))

        result = await self._session.execute(
            query.order_by(
                CompetitorBaseline.competitor_name.asc(),
                CompetitorBaseline.metric_name.asc(),
            )
        )
        return list(result.scalars().all())

    async def upsert(
        self,
        tenant_id: uuid.UUID,
        competitor_name: str,
        metric_category: str,
        metric_name: str,
        metric_value: float,
        dataset_name: str,
        measured_at: datetime,
        higher_is_better: bool,
        metric_unit: str | None,
        source_url: str | None,
        notes: str | None,
    ) -> CompetitorBaseline:
        """Create or update a competitor baseline by unique key.

        Args:
            tenant_id: Owning tenant UUID.
            competitor_name: Competitor identifier.
            metric_category: fidelity | privacy | speed.
            metric_name: Specific metric name.
            metric_value: Baseline value.
            dataset_name: Dataset name.
            measured_at: Measurement timestamp.
            higher_is_better: Metric direction.
            metric_unit: Unit of measurement.
            source_url: Optional source URL.
            notes: Optional context.

        Returns:
            Created or updated CompetitorBaseline.
        """
        existing = await self.get_by_competitor_and_metric(
            competitor_name, metric_name, dataset_name, tenant_id
        )

        if existing is not None:
            await self._session.execute(
                update(CompetitorBaseline)
                .where(CompetitorBaseline.id == existing.id)
                .values(
                    metric_value=metric_value,
                    metric_category=metric_category,
                    metric_unit=metric_unit,
                    higher_is_better=higher_is_better,
                    measured_at=measured_at,
                    source_url=source_url,
                    notes=notes,
                    is_active=True,
                )
            )
            await self._session.flush()

            result = await self._session.execute(
                select(CompetitorBaseline).where(CompetitorBaseline.id == existing.id)
            )
            return result.scalar_one()

        return await self.create(
            tenant_id=tenant_id,
            competitor_name=competitor_name,
            metric_category=metric_category,
            metric_name=metric_name,
            metric_value=metric_value,
            dataset_name=dataset_name,
            measured_at=measured_at,
            higher_is_better=higher_is_better,
            metric_unit=metric_unit,
            source_url=source_url,
            notes=notes,
        )


class RegressionCheckRepository(BaseRepository[RegressionCheck]):
    """Persistence for RegressionCheck entities."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialise with an async SQLAlchemy session.

        Args:
            session: AsyncSession with RLS context already set.
        """
        super().__init__(session, RegressionCheck)

    async def create(
        self,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        baseline_run_id: uuid.UUID | None,
        status: str,
        regressed_metrics: list[str],
        details: dict[str, Any],
        checked_at: datetime,
        ci_build_id: str | None,
        ci_commit_sha: str | None,
    ) -> RegressionCheck:
        """Create a regression check record.

        Args:
            tenant_id: Owning tenant UUID.
            run_id: BenchmarkRun UUID being checked.
            baseline_run_id: Previous run used as baseline.
            status: passed | failed | skipped.
            regressed_metrics: List of regressed metric names.
            details: Full per-metric analysis dict.
            checked_at: Check timestamp.
            ci_build_id: Optional CI build identifier.
            ci_commit_sha: Optional git commit SHA.

        Returns:
            Newly created RegressionCheck.
        """
        check = RegressionCheck(
            tenant_id=tenant_id,
            run_id=run_id,
            baseline_run_id=baseline_run_id,
            status=status,
            regressed_metrics=regressed_metrics,
            details=details,
            checked_at=checked_at,
            ci_build_id=ci_build_id,
            ci_commit_sha=ci_commit_sha,
        )
        self._session.add(check)
        await self._session.flush()
        await self._session.refresh(check)
        return check

    async def get_by_run(
        self, run_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> RegressionCheck | None:
        """Retrieve the regression check for a specific run.

        Args:
            run_id: BenchmarkRun UUID.
            tenant_id: Requesting tenant.

        Returns:
            RegressionCheck or None if no check performed.
        """
        result = await self._session.execute(
            select(RegressionCheck).where(
                RegressionCheck.run_id == run_id,
                RegressionCheck.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        page: int,
        page_size: int,
        status: str | None,
    ) -> tuple[list[RegressionCheck], int]:
        """List regression checks for a tenant with pagination.

        Args:
            tenant_id: Requesting tenant.
            page: 1-based page number.
            page_size: Results per page.
            status: Optional status filter.

        Returns:
            Tuple of (checks, total_count).
        """
        query = select(RegressionCheck).where(
            RegressionCheck.tenant_id == tenant_id
        )
        if status is not None:
            query = query.where(RegressionCheck.status == status)

        count_result = await self._session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        offset = (page - 1) * page_size
        result = await self._session.execute(
            query.order_by(RegressionCheck.checked_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        return list(result.scalars().all()), total
