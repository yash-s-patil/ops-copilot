"""What the services need from the runbook index and the policy store."""

from typing import Annotated, Literal, Protocol

from pydantic import Field

from opscp_common.documents import RUNBOOK_ID_PATTERN, SectionId, Summary, Title
from opscp_mcp.ports.base import Record

type Collection = Literal["standard", "chaos"]

STANDARD: Collection = "standard"
CHAOS: Collection = "chaos"

RunbookId = Annotated[str, Field(pattern=RUNBOOK_ID_PATTERN)]


class RunbookChunk(Record):
    """One section of one runbook, as a search returns it.

    An index is written outside this server, so its ids, titles and scores are held to the
    same shape as the runbook files before a service sees them.
    """

    runbook_id: RunbookId
    title: Title
    section: SectionId
    content: str
    score: Annotated[float, Field(ge=0, allow_inf_nan=False)]


class RunbookEntry(Record):
    runbook_id: RunbookId
    title: Title
    summary: Summary


class Runbooks(Protocol):
    async def search(
        self, query: str, *, limit: int, collections: frozenset[Collection]
    ) -> tuple[RunbookChunk, ...]:
        """The best-matching section of each matching runbook, best first.

        A runbook appears at most once. Only the named collections are searched.
        """
        ...

    async def catalog(self) -> tuple[RunbookEntry, ...]:
        """Every runbook of the standard collection, in id order."""
        ...


class Policies(Protocol):
    async def read(self, name: str) -> str | None:
        """The stored text of a policy document, or None when there is no such document.

        Raises BackendUnavailableError or BackendTimeoutError when the store cannot be read,
        and InternalError when what is stored is not a text document of a sane size.
        """
        ...
