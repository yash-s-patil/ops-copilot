import asyncio
import logging

from opscp_common.settings import DatabaseSettings
from opscp_seed.job import seed
from opscp_seed.settings import SeedSettings

_LOG = logging.getLogger("opscp_seed")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dataset = asyncio.run(seed(DatabaseSettings(), SeedSettings()))  # pyright: ignore[reportCallIssue] -- fields come from the environment
    _LOG.info(
        "Seeded %d customers, %d orders, %d order events, %d tickets, %d ticket notes.",
        len(dataset.customers),
        len(dataset.orders),
        len(dataset.events),
        len(dataset.tickets),
        len(dataset.notes),
    )


if __name__ == "__main__":
    main()
