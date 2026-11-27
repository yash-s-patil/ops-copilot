"""G9: what one run may spend. The limits are the server's backstop in the agent.

A tool result is counted before the model reads it, by its length, so that a run stops
before it pays for an oversized result and not after.
"""

from typing import TypedDict

from opscp_common.limits import BudgetLimits


class Budget(TypedDict):
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    tokens_waiting: int
    tool_calls: int
    model_turns: int
    elapsed_ms: int


def new_budget() -> Budget:
    return Budget(
        tokens_in=0,
        tokens_out=0,
        tokens_cached=0,
        tokens_waiting=0,
        tool_calls=0,
        model_turns=0,
        elapsed_ms=0,
    )


def estimated_tokens(text: str, chars_per_token: int) -> int:
    return -(-len(text) // chars_per_token)


_MS_PER_S = 1000


_TOO_LONG = "The investigation took longer than one investigation may take."


def exceeded(budget: Budget, limits: BudgetLimits) -> str | None:
    """Why the model may not be called again, in plain words; None when it may."""
    spent = budget["tokens_in"] + budget["tokens_out"] + budget["tokens_waiting"]
    if spent > limits.tokens:
        return (
            "There is too much to read in one investigation. "
            "What was gathered is more than the assistant may read at once."
        )
    if budget["model_turns"] >= limits.model_turns:
        return "The investigation took more steps than one investigation may take."
    if budget["elapsed_ms"] >= limits.wall_clock_s * _MS_PER_S:
        return _TOO_LONG
    return None


def no_room_for(budget: Budget, limits: BudgetLimits, tool_calls: int) -> str | None:
    """Why this many more tool calls may not be made; None when they may."""
    if budget["tool_calls"] + tool_calls > limits.tool_calls:
        return "The investigation needed more look-ups than one investigation may make."
    if budget["elapsed_ms"] >= limits.wall_clock_s * _MS_PER_S:
        return _TOO_LONG
    return None
