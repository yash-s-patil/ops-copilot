"""The agent's HTTP interface, held as a snapshot.

The web application is written against this. To accept a change on purpose, run
`UPDATE_CONTRACT_SNAPSHOTS=1 uv run pytest services/agent/tests/contract` and review the
diff of the snapshot.
"""

import json
import os
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

from opscp_agent.main import build_app
from opscp_common.limits import Limits

SNAPSHOT = Path(__file__).parent / "snapshots" / "openapi.json"
UPDATE = "UPDATE_CONTRACT_SNAPSHOTS"


def _canonical(document: object) -> str:
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_the_routes_and_their_shapes_are_the_published_ones(limits: Limits) -> None:
    app = build_app(_never_opened, limits, demo_tools=True)
    current = _canonical(app.openapi())

    if os.environ.get(UPDATE):
        SNAPSHOT.write_text(current, encoding="utf-8")
    assert current == SNAPSHOT.read_text(encoding="utf-8"), (
        f"The agent's HTTP interface changed. Review it, then set {UPDATE}=1 to accept."
    )


def _never_opened() -> AbstractAsyncContextManager[Any]:
    raise AssertionError("the document is built without starting the application")
