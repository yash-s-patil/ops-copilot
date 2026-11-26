from opscp_common.errors import ValidationFailedError
from opscp_common.limits import Limits
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.policy import Permission
from opscp_mcp.ports.knowledge import STANDARD, Collection, RunbookChunk, Runbooks
from opscp_mcp.services.caps import Result, cap_text

_SEARCHED: frozenset[Collection] = frozenset({STANDARD})


class RunbookHit(Result):
    runbook_id: str
    title: str
    section: str
    content: str
    truncated: bool
    score: float
    quarantined: bool


class RunbookSearchResult(Result):
    results: tuple[RunbookHit, ...]
    degraded: bool


class RunbookService:
    def __init__(self, runbooks: Runbooks, limits: Limits) -> None:
        self._runbooks = runbooks
        self._limits = limits

    async def search(
        self, identity: Identity, query: str, *, top_k: int | None = None
    ) -> RunbookSearchResult:
        identity.require(Permission.RUNBOOKS_READ)
        chunks = await self._runbooks.search(
            self._query(query), limit=self._top_k(top_k), collections=_SEARCHED
        )
        return RunbookSearchResult(
            results=tuple(self._hit(chunk) for chunk in chunks), degraded=False
        )

    def _query(self, query: str) -> str:
        limits = self._limits.search
        text = " ".join(query.split())
        if (
            not limits.query_chars_min <= len(text) <= limits.query_chars_max
            or not text.isprintable()
        ):
            raise ValidationFailedError(
                f"query must be {limits.query_chars_min} to {limits.query_chars_max} characters.",
                min=limits.query_chars_min,
                max=limits.query_chars_max,
            )
        return text

    def _top_k(self, requested: int | None) -> int:
        limits = self._limits.runbooks
        top_k = limits.top_k_default if requested is None else requested
        if not 0 < top_k <= limits.top_k_max:
            raise ValidationFailedError(
                f"top_k must be between 1 and {limits.top_k_max}.", max=limits.top_k_max
            )
        return top_k

    def _hit(self, chunk: RunbookChunk) -> RunbookHit:
        limits = self._limits.runbook
        content = cap_text(chunk.content, limits.chunk_chars, limits.chunk_bytes)
        return RunbookHit(
            runbook_id=chunk.runbook_id,
            title=chunk.title,
            section=chunk.section,
            content=content.text,
            truncated=content.truncated,
            score=chunk.score,
            quarantined=False,
        )
