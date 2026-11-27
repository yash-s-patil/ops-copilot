"""The two probes. Neither needs a token and neither says anything about a caller."""

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict


class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str


OK = Probe(status="ok")
UNAVAILABLE = Probe(status="unavailable")


def health_router(ready: Callable[[], Awaitable[bool]]) -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def healthz() -> Probe:
        """The process is running."""
        return OK

    @router.get("/readyz")
    async def readyz(response: Response) -> Probe:
        """The process can reach its own database and has its tables."""
        if await ready():
            return OK
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return UNAVAILABLE

    return router
