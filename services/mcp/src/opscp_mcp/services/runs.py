"""Which investigation a request says it belongs to.

The agent names its run in a header so that the audit log can be read run by run. The value
is the caller's word and proves nothing; it is kept only when it has the shape of a run id.
"""

from collections.abc import Mapping
from uuid import UUID

RUN_HEADER = "x-opscp-run-id"


def run_named_in(headers: Mapping[str, str]) -> str | None:
    named = headers.get(RUN_HEADER)
    if named is None:
        return None
    try:
        return str(UUID(named))
    except ValueError:
        return None
