"""What is kept of a call's arguments in the audit log.

The log is read by people who must not see customer contact details, and it is kept for a
long time. Only the arguments the tool declares are kept. An email address or phone number
is replaced by a short hash, so two calls about the same customer can still be matched, and
other text is cut short. Matching by pattern cannot catch every way of writing an address,
which is why an argument that exists to carry one is hashed whole.
"""

import hashlib
import re
import unicodedata
from collections.abc import Collection, Mapping
from typing import cast

from opscp_common.hashing import JsonValue

NAMES_KEPT = 16
NAME_CHARS = 64
CONTACT_ARGUMENTS = {"email": "email", "phone": "phone"}
CREDENTIAL_ARGUMENTS = frozenset({"approval_token"})
CREDENTIAL = "<credential>"
_HASH_CHARS = 16
_WINDOW = 4
_LONGEST_NUMBER_KEPT = 999_999_999
_LOCAL_PART, _LABEL, _MORE_LABELS = 64, 63, 8
_LONGEST_EMAIL = _LOCAL_PART + 1 + _LABEL + _MORE_LABELS * (_LABEL + 1)
_ATEXT = r"\w.!#$%&'*+/=?^`{|}~-"
_CONTACT = re.compile(
    rf"(?P<email>[{_ATEXT}]{{1,{_LOCAL_PART}}}(?:@|%40)"
    rf"[\w-]{{1,{_LABEL}}}(?:\.[\w-]{{1,{_LABEL}}}){{1,{_MORE_LABELS}}})"
    r"|(?P<phone>(?<![\w-])\+?\d(?:[\s().\-/_,]{0,3}\d){9,14}(?![\w-]))"
)
_UNSTORABLE = str.maketrans({"\x00": "�"})
_INVISIBLE = re.compile(r"[​-‏⁠﻿]")


def is_argument_name(name: str) -> bool:
    """Whether a caller's key could be a real argument name, and so may be written down."""
    return name.isascii() and name.isidentifier() and len(name) <= NAME_CHARS


def _hash(kind: str, value: str) -> str:
    return f"<{kind}:{hashlib.sha256(value.encode()).hexdigest()[:_HASH_CHARS]}>"


def _hashed(kind: str, written: str) -> str:
    """The same address or number gives the same hash however it was written."""
    if kind == "email":
        return _hash(kind, written.lower().replace("%40", "@"))
    return _hash(kind, "".join(digit for digit in written if digit.isdigit()))


def _readable(text: str) -> str:
    """Look-alike characters made plain, and what the database cannot store replaced."""
    plain = unicodedata.normalize("NFKC", text).translate(_UNSTORABLE)
    return _INVISIBLE.sub("", plain)


def redacted_text(text: str, text_chars: int) -> str:
    """Contact details replaced by hashes, then cut to length.

    Only the start of a long text is examined. Where that cuts the text, the end of what was
    examined may hold the first part of an address, so it is left out.
    """
    examined = text_chars * _WINDOW + _LONGEST_EMAIL
    window = _readable(text[:examined])
    kept: list[str] = []
    done = 0
    for found in _CONTACT.finditer(window):
        kept += [window[done : found.start()], _hashed(str(found.lastgroup), found.group())]
        done = found.end()
    whole = len(text) <= examined
    kept.append(window[done : len(window) if whole else max(done, len(window) - _LONGEST_EMAIL)])
    return "".join(kept)[:text_chars]


def _redacted_value(
    name: str, value: object, text_chars: int, *, nested: bool = False
) -> JsonValue:
    if name in CREDENTIAL_ARGUMENTS:
        return None if value is None else CREDENTIAL
    match value:
        case None | bool():
            return value
        case int() if abs(value) <= _LONGEST_NUMBER_KEPT:
            return value
        case str() if name in CONTACT_ARGUMENTS:
            return _hashed(CONTACT_ARGUMENTS[name], _readable(value[: text_chars * _WINDOW]))
        case str():
            return redacted_text(value, text_chars)
        case dict() if not nested:
            # A JSON object: its keys are text, its values anything.
            return _redacted_object(cast("Mapping[object, object]", value), text_chars)
        case _:
            return f"<{type(value).__name__}>"


def _redacted_object(value: Mapping[object, object], text_chars: int) -> dict[str, JsonValue]:
    names = sorted(key for key in value if isinstance(key, str) and is_argument_name(key))
    return {
        key: _redacted_value(key, value[key], text_chars, nested=True) for key in names[:NAMES_KEPT]
    }


def redacted(
    arguments: Mapping[str, object], declared: Collection[str], text_chars: int
) -> dict[str, JsonValue]:
    """The arguments the tool declares, each made safe to keep. Anything else is left out.

    A number of ten digits or more could be a phone number, so it is recorded by its kind.
    """
    return {
        name: _redacted_value(name, arguments[name], text_chars)
        for name in sorted(declared)
        if name in arguments
    }
