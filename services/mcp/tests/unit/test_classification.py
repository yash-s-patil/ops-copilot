import ssl

import pytest
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from opscp_mcp.adapters.postgres.session import classify
from opscp_mcp.reliability.guard import Failure


class _DriverError(Exception):
    def __init__(self, sqlstate: str | None) -> None:
        super().__init__("driver error")
        self.sqlstate = sqlstate


def _database_error(sqlstate: str | None, *, lost: bool = False) -> DBAPIError:
    return DBAPIError("SELECT 1", {}, _DriverError(sqlstate), connection_invalidated=lost)


@pytest.mark.parametrize(
    ("error", "verdict"),
    [
        (ConnectionRefusedError(), Failure.PASSING),
        (ConnectionResetError(), Failure.PASSING),
        (PoolTimeoutError(), Failure.PASSING),
        (_database_error(None, lost=True), Failure.PASSING),
        (_database_error("08006"), Failure.PASSING),
        (_database_error("53300"), Failure.PASSING),
        (_database_error("57P01"), Failure.PASSING),
        (_database_error("57P03"), Failure.PASSING),
        (ssl.SSLCertVerificationError("certificate verify failed"), Failure.STANDING),
        (_database_error("28P01"), Failure.STANDING),
        (_database_error("28000"), Failure.STANDING),
        (_database_error("3D000"), Failure.STANDING),
        (_database_error("57014"), None),
        (_database_error("42P01"), None),
        (_database_error("42501"), None),
        (_database_error("22003"), None),
        (_database_error(None), None),
        (ProgrammingError("SELECT 1", {}, _DriverError("42601")), None),
        (KeyError("a defect"), None),
        (ValueError("a defect"), None),
    ],
    ids=lambda value: str(value)[:40] if isinstance(value, Exception) else str(value),
)
def test_what_a_failure_says_about_the_database(error: Exception, verdict: Failure | None) -> None:
    assert classify(error) is verdict
