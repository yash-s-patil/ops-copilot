"""G3: what a person may send as a message.

Characters that do not show on a screen are removed before the message is stored or read
by a model, since they can hide text from the person who appears to have written it. Line
breaks and the joiners that Indic scripts need are kept.
"""

import re
import unicodedata

from opscp_common.errors import ValidationFailedError

_KEPT = frozenset({"\n", "\t", "‌", "‍"})
_INVISIBLE = frozenset({"Cc", "Cf", "Co", "Cs", "Cn"})
_ORDER_NAMED = re.compile(r"\border\s*(?:number\s*|no\.?\s*)?#?\s*(\d{1,15})\b", re.IGNORECASE)


def order_named_in(message: str) -> int | None:
    """The order number a person wrote, when they wrote one as "order 4471"."""
    found = _ORDER_NAMED.search(message)
    return None if found is None else int(found.group(1))


def clean_message(text: str, max_chars: int) -> str:
    """Raises ValidationFailedError for a message that is empty or too long."""
    if len(text) > max_chars:
        raise ValidationFailedError(
            f"The message is longer than {max_chars} characters.", max_chars=max_chars
        )
    shown = "".join(
        character
        for character in text
        if character in _KEPT or unicodedata.category(character) not in _INVISIBLE
    ).strip()
    if not shown:
        raise ValidationFailedError("Type a message first.")
    return shown
