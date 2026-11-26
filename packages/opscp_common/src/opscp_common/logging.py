"""One structured log line per event, as JSON outside a terminal, for our code and libraries."""

import logging
import sys
from typing import Literal

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


def _stamp(service: str) -> Processor:
    def add_service(_logger: WrappedLogger, _method: str, event: EventDict) -> EventDict:
        event["service"] = service
        return event

    return add_service


def configure_logging(*, service: str, level: LogLevel, json: bool) -> None:
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _stamp(service),
    ]
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if json
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                renderer,
            ],
        )
    )
    logging.basicConfig(handlers=[handler], level=level, force=True)
