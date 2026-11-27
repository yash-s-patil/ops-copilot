"""Brings the libraries' own logging into the service's one log format.

The HTTP client writes every request's address at info level, and the MCP client writes
requests and whole responses at debug level. A response holds customer data, so both are
held at warning whatever level the service is given. An exception a library attaches to a
line can quote what it failed on, so no line is written with one.
"""

import logging

FRAMEWORK = "fastmcp"
HELD_AT_WARNING = ("httpx2", "mcp", "langgraph")


class NoExceptionText(logging.Filter):
    """This service logs a failure by its kind and place. Nothing else may add its text."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.exc_info = None
        record.exc_text = None
        return True


def adopt_library_logs() -> None:
    """Call after logging is configured, and after the libraries have been imported."""
    framework = logging.getLogger(FRAMEWORK)
    for handler in framework.handlers[:]:
        framework.removeHandler(handler)
    framework.propagate = True
    # On the handlers, because a filter on a logger is not applied to the loggers below it.
    for handler in logging.getLogger().handlers:
        if not any(isinstance(existing, NoExceptionText) for existing in handler.filters):
            handler.addFilter(NoExceptionText())
    for quiet in HELD_AT_WARNING:
        logging.getLogger(quiet).setLevel(logging.WARNING)
