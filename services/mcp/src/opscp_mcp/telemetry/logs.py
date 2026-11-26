"""Brings the frameworks' own logging into the server's one log format."""

import logging

FRAMEWORK = "fastmcp"
REQUEST_RUNNER = "fastmcp.server.server"
TRANSPORT = "mcp.server.streamable_http"
EVENT_STREAM = "sse_starlette"

_REPORTED_BY_THE_ENVELOPE = (
    "Error calling tool",
    "Invalid arguments for tool",
    "Error reading resource",
    "Error rendering prompt",
)


class OneReportPerFailure(logging.Filter):
    """Drops the framework's line for a failed request, and exception text from the rest.

    The envelope reports every failed request once, by code or by type and place. The
    framework's own line says the same at error level even for an ordinary refusal, names
    what the caller asked for, and attaches an exception that can quote their arguments.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.getMessage().startswith(_REPORTED_BY_THE_ENVELOPE):
            return False
        record.exc_info = None
        record.exc_text = None
        return True


def adopt_framework_logs() -> None:
    """Call after logging is configured, and after the framework has been imported.

    The transport and the event-stream library write requests and whole responses at debug
    level, so both are held at warning whatever level the server is given.
    """
    framework = logging.getLogger(FRAMEWORK)
    for handler in framework.handlers[:]:
        framework.removeHandler(handler)
    framework.propagate = True
    runner = logging.getLogger(REQUEST_RUNNER)
    if not any(isinstance(existing, OneReportPerFailure) for existing in runner.filters):
        runner.addFilter(OneReportPerFailure())
    for quiet in (TRANSPORT, EVENT_STREAM):
        logging.getLogger(quiet).setLevel(logging.WARNING)
