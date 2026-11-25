from pathlib import Path

import pytest

from opscp_common.settings import DatabaseSettings
from opscp_seed.job import WrongTargetError, seed
from opscp_seed.settings import SeedSettings

from .conftest import DEMO_USERS


async def test_seeding_is_refused_when_the_database_is_not_the_named_target() -> None:
    database = DatabaseSettings.model_validate(
        {"host": "prod.internal", "name": "meridian", "user": "migrator", "password": "unused"}
    )
    settings = SeedSettings(
        target_host="127.0.0.1", limits_file=Path("unused.yaml"), demo=DEMO_USERS
    )

    with pytest.raises(WrongTargetError, match=r"prod\.internal.*127\.0\.0\.1"):
        await seed(database, settings)
