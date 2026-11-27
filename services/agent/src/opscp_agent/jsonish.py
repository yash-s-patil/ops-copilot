"""Reading JSON whose shape another program decides.

A tool's result is JSON from the MCP server. These two functions give a value as an object
or a list when it is one and as an empty one when it is not, so that the code reading a
result never fails on a shape it did not expect.
"""

from typing import Any, cast


def as_object(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def as_list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []
