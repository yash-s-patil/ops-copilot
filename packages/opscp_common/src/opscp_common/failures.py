"""What is written to the log about a failure that is a defect of ours.

An exception's text can quote the data it failed on, so the text is never logged. The kind
of failure and the place it happened are enough to find it.
"""

import traceback

_CHAIN_DEPTH = 8
_FRAMES_LOGGED = 6


def chain_of(error: BaseException) -> list[BaseException]:
    """The error and what led to it, outermost first."""
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and len(chain) < _CHAIN_DEPTH:
        chain.append(current)
        handled_first = None if current.__suppress_context__ else current.__context__
        current = current.__cause__ or handled_first
    return chain


def failure_site(error: BaseException) -> dict[str, object]:
    """The kind of the first failure in the chain and where it happened, as log fields."""
    root = chain_of(error)[-1]
    frames = traceback.extract_tb(root.__traceback__)[-_FRAMES_LOGGED:]
    return {
        "error": type(root).__name__,
        "where": [f"{frame.filename}:{frame.lineno} in {frame.name}" for frame in frames],
    }
