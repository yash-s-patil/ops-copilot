"""The wrapper every tool result is returned in."""

from datetime import datetime

from opscp_mcp.services.caps import Result


class Provenance(Result):
    """Identifies one tool result, so an answer can cite exactly where a fact came from."""

    id: str
    tool: str
    retrieved_at: datetime


class ToolOutput[T: Result](Result):
    data: T
    provenance: Provenance
