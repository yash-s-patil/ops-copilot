"""Checks on caller-supplied values that the database would otherwise reject with an error."""

from opscp_common.errors import ValidationFailedError

MAX_ID = 2**63 - 1
EMAIL_MAX_CHARS = 254


def require_id(value: int, name: str) -> int:
    if not 0 < value <= MAX_ID:
        raise ValidationFailedError(f"{name} is not a valid number.")
    return value


def require_email(value: str) -> str:
    if not 0 < len(value) <= EMAIL_MAX_CHARS or not value.isprintable() or "@" not in value:
        raise ValidationFailedError("email is not a valid address.")
    return value
