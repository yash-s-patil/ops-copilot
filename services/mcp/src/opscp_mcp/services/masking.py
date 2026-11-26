"""Contact details as shown to a caller without the permission to read them."""

import re

_MASK = "*"
_EMAIL_MASK = _MASK * 3
_VISIBLE_PHONE_DIGITS = 2
_PHONE_LAYOUT = frozenset("+-() ")
_DIGIT = re.compile(r"\d")


def mask_email(email: str) -> str:
    """Keep the first character and the domain: p***@example.in."""
    local, at, domain = email.rpartition("@")
    if not at or not local:
        return _EMAIL_MASK
    first = local[0] if len(local) > 1 else ""
    return f"{first}{_EMAIL_MASK}@{domain}"


def mask_phone(phone: str) -> str:
    """Keep the layout and the last two digits: +** ***** ***71."""
    digits = [match.start() for match in _DIGIT.finditer(phone)]
    visible = set[int](
        digits[-_VISIBLE_PHONE_DIGITS:] if len(digits) > _VISIBLE_PHONE_DIGITS else ()
    )
    return "".join(
        character if index in visible or character in _PHONE_LAYOUT else _MASK
        for index, character in enumerate(phone)
    )
