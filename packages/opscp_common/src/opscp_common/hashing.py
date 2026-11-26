"""Hashes of canonical JSON: one binds an approval to its arguments, one names a result."""

import hashlib
import json
from collections.abc import Mapping

import rfc8785

type JsonValue = bool | int | str | list[JsonValue] | dict[str, JsonValue] | None


def args_hash(tool: str, args: dict[str, JsonValue]) -> str:
    """SHA-256 of the RFC 8785 canonical JSON of the tool name and its arguments."""
    return hashlib.sha256(rfc8785.dumps({"tool": tool, "args": args})).hexdigest()


def content_hash(content: Mapping[str, object] | None) -> str:
    """SHA-256 of a result as JSON with sorted keys, so equal results hash equally."""
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()
