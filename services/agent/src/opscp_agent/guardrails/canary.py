"""The canary: a secret string placed in the system prompt and nowhere else.

A model has no reason to repeat it. If it appears in a tool argument or an answer, text
from the prompt is leaking, and the run is stopped.
"""

import json
from typing import Any


def leaked(canary: str, *values: Any) -> bool:
    """Whether the canary appears anywhere in the values, however deeply nested."""
    return any(canary in _flat(value) for value in values)


def _flat(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
