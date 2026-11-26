import asyncio
import sys

import structlog

from opscp_common.logging import configure_logging
from opscp_common.settings import DatabaseSettings
from opscp_seed.job import seed
from opscp_seed.settings import SeedSettings


def main() -> None:
    configure_logging(service="seed", level="INFO", json=not sys.stderr.isatty())
    dataset = asyncio.run(seed(DatabaseSettings(), SeedSettings()))  # pyright: ignore[reportCallIssue] -- fields come from the environment
    structlog.get_logger().info(
        "seeded",
        customers=len(dataset.customers),
        orders=len(dataset.orders),
        order_events=len(dataset.events),
        tickets=len(dataset.tickets),
        ticket_notes=len(dataset.notes),
    )


if __name__ == "__main__":
    main()
