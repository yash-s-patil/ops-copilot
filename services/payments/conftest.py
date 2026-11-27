from pathlib import Path

import pytest

from opscp_common.limits import Limits, load_limits

pytest.register_assert_rewrite("opscp_payments_testing")

_LIMITS_FILE = Path(__file__).resolve().parents[2] / "config" / "limits.yaml"


@pytest.fixture(scope="session")
def limits() -> Limits:
    return load_limits(_LIMITS_FILE)
