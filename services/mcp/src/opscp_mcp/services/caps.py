"""Length limits on free text, always reported to the caller."""

from pydantic import BaseModel, ConfigDict


class CappedText(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    truncated: bool


def cap_text(text: str, max_chars: int) -> CappedText:
    if len(text) <= max_chars:
        return CappedText(text=text, truncated=False)
    return CappedText(text=text[:max_chars], truncated=True)
