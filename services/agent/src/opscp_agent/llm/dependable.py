"""A model call that is tried again when the model is busy or away, within limits."""

import asyncio
import random
from collections.abc import Awaitable, Callable

import structlog

from opscp_agent.llm.base import ChatModel, ModelRequest, ModelTurn, ModelUnavailableError
from opscp_common.limits import ModelLimits

_MS_PER_S = 1000
_LOG = structlog.get_logger(__name__)


class Dependable:
    """Each attempt has a time limit. A wait the model asks for is honoured up to a limit."""

    def __init__(
        self,
        model: ChatModel,
        limits: ModelLimits,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._model = model
        self._limits = limits
        self._sleep = sleep
        self._jitter = jitter

    @property
    def name(self) -> str:
        return self._model.name

    async def complete(self, request: ModelRequest) -> ModelTurn:
        for attempt in range(self._limits.retries + 1):
            try:
                async with asyncio.timeout(self._limits.timeout_s):
                    return await self._model.complete(request)
            except (ModelUnavailableError, TimeoutError) as error:
                asked = error.retry_after_s if isinstance(error, ModelUnavailableError) else None
                if attempt == self._limits.retries or (
                    asked is not None and asked > self._limits.retry_after_max_s
                ):
                    raise ModelUnavailableError from error
                _LOG.warning("model_retry", attempt=attempt + 1, waited_for_model=asked is not None)
                await self._sleep(self._backoff_s(attempt) if asked is None else asked)
        raise ModelUnavailableError

    def _backoff_s(self, attempt: int) -> float:
        """Exponential, the second half random, so that retries do not arrive together."""
        whole = self._limits.backoff_base_ms * 2**attempt / _MS_PER_S
        return whole / 2 + whole / 2 * self._jitter()
