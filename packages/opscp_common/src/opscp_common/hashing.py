"""The hash that binds an approval to the exact arguments of a write."""

import hashlib

import rfc8785

type JsonValue = bool | int | str | list[JsonValue] | dict[str, JsonValue] | None


def args_hash(tool: str, args: dict[str, JsonValue]) -> str:
    """SHA-256 of the RFC 8785 canonical JSON of the tool name and its arguments."""
    return hashlib.sha256(rfc8785.dumps({"tool": tool, "args": args})).hexdigest()
