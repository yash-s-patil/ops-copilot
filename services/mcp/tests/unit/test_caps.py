import pytest

from opscp_mcp.services.caps import CappedText, cap_text


@pytest.mark.parametrize("text", ["", "short", "exactly10!"])
def test_text_within_the_limit_is_returned_whole(text: str) -> None:
    assert cap_text(text, 10) == CappedText(text=text, truncated=False)


def test_longer_text_is_cut_to_the_limit_and_flagged() -> None:
    assert cap_text("exactly10!+", 10) == CappedText(text="exactly10!", truncated=True)


def test_the_limit_counts_characters_not_bytes() -> None:
    capped = cap_text("पार्सल कहाँ है", 6)

    assert len(capped.text) == 6
    assert capped.truncated
