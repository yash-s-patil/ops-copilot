"""The two probes. Neither needs a token and neither says anything about a caller."""

import asyncio

import structlog
from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

_LOG = structlog.get_logger(__name__)
_PING = text("SELECT 1")


class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str


OK = Probe(status="ok")
UNAVAILABLE = Probe(status="unavailable")


def health_router(engine: AsyncEngine, *, database_timeout_s: int) -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def healthz() -> Probe:
        """The process is running."""
        return OK

    @router.get("/readyz")
    async def readyz(response: Response) -> Probe:
        """The process can reach its database."""
        try:
            async with asyncio.timeout(database_timeout_s), engine.connect() as connection:
                await connection.execute(_PING)
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            _LOG.warning("not_ready", error=type(error).__name__)
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return UNAVAILABLE
        return OK

    return router
