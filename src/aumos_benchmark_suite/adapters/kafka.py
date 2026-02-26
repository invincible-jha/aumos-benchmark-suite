"""Kafka event publishing adapter for the Benchmark Suite service.

Wraps aumos-common's EventPublisher with benchmark-domain-specific
topic constants and structured event payloads.
"""

from aumos_common.events import EventPublisher
from aumos_common.observability import get_logger

logger = get_logger(__name__)


class BenchmarkEventPublisher(EventPublisher):
    """Event publisher specialised for benchmark domain events.

    Extends EventPublisher from aumos-common, adding benchmark-specific
    helpers. Topic names follow the benchmark.* convention.

    Topics published:
        benchmark.run.started       — benchmark run began execution
        benchmark.run.completed     — benchmark run finished successfully
        benchmark.run.failed        — benchmark run failed with error
        benchmark.regression.checked — CI regression check result available
        benchmark.report.generated  — benchmark report generation complete
        benchmark.baseline.upserted — competitor baseline updated
    """

    pass
