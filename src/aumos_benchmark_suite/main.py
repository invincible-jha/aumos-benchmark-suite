"""AumOS Benchmark Suite service entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from aumos_common.app import create_app
from aumos_common.database import init_database
from aumos_common.health import HealthCheck
from aumos_common.observability import get_logger

from aumos_benchmark_suite.adapters.kafka import BenchmarkEventPublisher
from aumos_benchmark_suite.adapters.runner_engine import RunnerEngineAdapter
from aumos_benchmark_suite.api.router import router
from aumos_benchmark_suite.settings import Settings

logger = get_logger(__name__)
settings = Settings()

_kafka_publisher: BenchmarkEventPublisher | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown lifecycle.

    Initialises the database connection pool, Kafka event publisher,
    benchmark runner engine, and exposes services on app.state for DI.

    Args:
        app: The FastAPI application instance.

    Yields:
        None
    """
    global _kafka_publisher  # noqa: PLW0603

    logger.info("Starting AumOS Benchmark Suite", version="0.1.0")

    # Database connection pool
    init_database(settings.database)
    logger.info("Database connection pool ready")

    # Kafka event publisher
    _kafka_publisher = BenchmarkEventPublisher(settings.kafka)
    await _kafka_publisher.start()
    app.state.kafka_publisher = _kafka_publisher
    logger.info("Kafka event publisher ready")

    # Runner engine adapter (coordinates tabular, privacy, fidelity calls)
    runner_adapter = RunnerEngineAdapter(
        tabular_engine_url=settings.tabular_engine_url,
        privacy_engine_url=settings.privacy_engine_url,
        fidelity_validator_url=settings.fidelity_validator_url,
        http_timeout=settings.http_timeout,
        http_max_retries=settings.http_max_retries,
    )
    app.state.runner_adapter = runner_adapter
    logger.info(
        "Runner engine adapter ready",
        tabular_url=settings.tabular_engine_url,
        privacy_url=settings.privacy_engine_url,
        fidelity_url=settings.fidelity_validator_url,
    )

    # Expose settings on app state for dependency injection
    app.state.settings = settings

    logger.info("Benchmark Suite service startup complete")
    yield

    # Shutdown
    if _kafka_publisher:
        await _kafka_publisher.stop()

    logger.info("Benchmark Suite service shutdown complete")


app: FastAPI = create_app(
    service_name="aumos-benchmark-suite",
    version="0.1.0",
    settings=settings,
    lifespan=lifespan,
    health_checks=[
        HealthCheck(name="postgres", check_fn=lambda: None),
        HealthCheck(name="kafka", check_fn=lambda: None),
    ],
)

app.include_router(router, prefix="/api/v1")
