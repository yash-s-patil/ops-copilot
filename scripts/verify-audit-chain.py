"""Checks that no row of the audit log was changed or removed from the middle.

    make verify-audit

Exits 0 when the chain is whole, 1 when it is not, naming the first rows that fail, and 2
when the log could not be read. A whole chain does not show that rows were not removed from
the end, or that the table's owner did not rewrite it from some row onward: compare the row
count and the latest rows with the copy in the log stream for that.
"""

import asyncio
import sys

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from opscp_common.settings import DatabaseSettings
from opscp_mcp.adapters.postgres.audit import broken_links

ROWS_NAMED = 20
UNREADABLE = 2


async def main() -> int:
    database = DatabaseSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment
    engine = create_async_engine(database.url(), hide_parameters=True)
    try:
        async with engine.connect() as connection:
            rows, broken = await broken_links(connection, limit=ROWS_NAMED)
    except (OSError, DBAPIError) as error:
        sys.stderr.write(f"The audit log could not be read ({type(error).__name__}).\n")
        return UNREADABLE
    finally:
        await engine.dispose()
    if broken:
        named = ", ".join(str(row) for row in broken)
        sys.stderr.write(f"The audit chain is broken. First rows that fail: {named}.\n")
        return 1
    sys.stdout.write(f"The audit chain is whole: {rows} rows.\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
