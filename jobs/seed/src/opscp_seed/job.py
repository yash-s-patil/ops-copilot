"""The seed job: build the dataset and load it in one transaction."""

from datetime import UTC, datetime

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from opscp_common.limits import load_limits
from opscp_common.settings import DatabaseSettings
from opscp_seed.dataset import build_dataset
from opscp_seed.load import load
from opscp_seed.model import Dataset
from opscp_seed.settings import SeedSettings


class WrongTargetError(RuntimeError):
    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(
            f"Refusing to seed: the database host is {actual!r} but SEED_TARGET_HOST "
            f"names {expected!r}. Seeding deletes every business row first."
        )


async def seed(database: DatabaseSettings, settings: SeedSettings) -> Dataset:
    if database.host != settings.target_host:
        raise WrongTargetError(settings.target_host, database.host)
    dataset = build_dataset(
        now=datetime.now(UTC), limits=load_limits(settings.limits_file), demo=settings.demo
    )
    engine = create_async_engine(database.url(), poolclass=pool.NullPool)
    try:
        async with engine.begin() as connection:
            await load(connection, dataset)
    finally:
        await engine.dispose()
    return dataset
