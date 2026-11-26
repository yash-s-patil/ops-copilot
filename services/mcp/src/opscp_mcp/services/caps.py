"""Length limits on free text, always reported to the caller."""

from pydantic import BaseModel, ConfigDict

_ENCODING = "utf-8"
_KEPT_CONTROL_CHARACTERS = "\n"


class Result(BaseModel):
    """What a service returns: fixed fields, never changed after it is built."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CappedText(Result):
    text: str
    truncated: bool


def cap_text(text: str, max_chars: int, max_bytes: int | None = None) -> CappedText:
    """Cut to a number of characters and, for scripts that need several bytes each, of bytes.

    Control characters other than a line break become spaces first: they carry no meaning
    here and each would take six bytes once the result is written as JSON.
    """
    readable = "".join(
        character if character.isprintable() or character in _KEPT_CONTROL_CHARACTERS else " "
        for character in text
    )
    capped = readable[:max_chars]
    if max_bytes is not None:
        capped = capped.encode(_ENCODING)[:max_bytes].decode(_ENCODING, errors="ignore")
    return CappedText(text=capped, truncated=len(capped) < len(readable))
