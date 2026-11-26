import asyncio
import sys

import structlog

from opscp_common.blob import policies_container
from opscp_common.logging import configure_logging
from opscp_common.settings import DatabaseSettings, StorageSettings
from opscp_seed.job import seed
from opscp_seed.policies import read_policies, upload_policies
from opscp_seed.settings import SeedSettings


async def _run() -> None:
    settings = SeedSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment
    storage = StorageSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment
    policies = read_policies(settings.policies_dir)
    log = structlog.get_logger()
    dataset = await seed(DatabaseSettings(), settings)  # pyright: ignore[reportCallIssue] -- fields come from the environment
    log.info(
        "seeded",
        customers=len(dataset.customers),
        orders=len(dataset.orders),
        order_events=len(dataset.events),
        tickets=len(dataset.tickets),
        ticket_notes=len(dataset.notes),
    )
    async with policies_container(storage) as container:
        await upload_policies(container, policies)
    log.info("policies_uploaded", policies=list(policies))


def main() -> None:
    configure_logging(service="seed", level="INFO", json=not sys.stderr.isatty())
    asyncio.run(_run())


if __name__ == "__main__":
    main()
