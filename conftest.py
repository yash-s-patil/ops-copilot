from pathlib import Path

import pytest

from opscp_common.limits import Limits, load_limits

# The helper packages hold assertions of their own. They are named here, before any test
# imports one, so that a failed assertion inside them is explained like any other.
pytest.register_assert_rewrite("opscp_agent_testing", "opscp_mcp_testing", "opscp_payments_testing")

_LIMITS_FILE = Path(__file__).resolve().parent / "config" / "limits.yaml"


@pytest.fixture(scope="session")
def limits() -> Limits:
    return load_limits(_LIMITS_FILE)
