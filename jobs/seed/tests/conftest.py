from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from opscp_common.limits import Limits, load_limits
from opscp_seed.dataset import build_dataset
from opscp_seed.model import Dataset, Engineer
from opscp_seed.settings import DemoUsers

REPO_ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 11, 28, 4, 30, tzinfo=UTC)
PRIYA = Engineer(oid=UUID("0f1e2d3c-0001-4000-8000-000000000001"), name="Priya Nair")
ROHAN = Engineer(oid=UUID("0f1e2d3c-0002-4000-8000-000000000002"), name="Rohan Iyer")
ARJUN = Engineer(oid=UUID("0f1e2d3c-0003-4000-8000-000000000003"), name="Arjun Mehta")
DEMO_USERS = DemoUsers(l1_west=PRIYA, l1_south=ROHAN, l2_west=ARJUN)


@pytest.fixture(scope="session")
def limits() -> Limits:
    return load_limits(REPO_ROOT / "config" / "limits.yaml")


@pytest.fixture(scope="session")
def dataset(limits: Limits) -> Dataset:
    return build_dataset(now=NOW, limits=limits, demo=DEMO_USERS)
