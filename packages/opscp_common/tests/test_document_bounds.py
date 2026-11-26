import pytest
from pydantic import ValidationError

from opscp_common.documents import (
    POLICY_SECTIONS_MAX,
    SECTION_ID_MAX_CHARS,
    TITLE_MAX_CHARS,
    DocumentFormatError,
    parse_policy,
    parse_runbook,
)

FRONT_MATTER = "---\nname: refund-policy\nversion: 3\neffective_date: 2026-10-01\n---\n"
RUNBOOK_FRONT_MATTER = (
    '---\nid: rb-001\ntitle: {title}\nsummary: "A summary."\nversion: 1\nupdated: 2026-09-22\n---\n'
)
RUNBOOK_BODY = "".join(
    f"\n## {name}\n\nText.\n"
    for name in ("Symptoms", "Checks", "Resolution", "Compensation", "Escalation")
)


def _policy(sections: int, *, title: str = "Section", section_id: str = "s") -> str:
    body = "".join(
        f"\n## {title} {{#{section_id}{number}}}\n\nA summary.\n" for number in range(sections)
    )
    return FRONT_MATTER + body


def test_a_policy_may_have_up_to_the_maximum_number_of_sections() -> None:
    assert len(parse_policy(_policy(POLICY_SECTIONS_MAX)).sections) == POLICY_SECTIONS_MAX


def test_a_policy_with_more_sections_is_refused() -> None:
    with pytest.raises(ValidationError, match="sections"):
        parse_policy(_policy(POLICY_SECTIONS_MAX + 1))


def test_a_section_title_has_a_maximum_length() -> None:
    assert parse_policy(_policy(1, title="T" * TITLE_MAX_CHARS)).sections[0].title

    with pytest.raises(ValidationError, match="title"):
        parse_policy(_policy(1, title="T" * (TITLE_MAX_CHARS + 1)))


def test_a_section_id_has_a_maximum_length() -> None:
    longest = "s" * (SECTION_ID_MAX_CHARS - 1)

    assert parse_policy(_policy(1, section_id=longest)).sections[0].id == f"{longest}0"
    with pytest.raises(ValidationError, match="String should have at most 40 characters"):
        parse_policy(_policy(1, section_id=f"{longest}s"))


def test_a_runbook_title_has_a_maximum_length() -> None:
    long_title = RUNBOOK_FRONT_MATTER.format(title="T" * (TITLE_MAX_CHARS + 1)) + RUNBOOK_BODY

    with pytest.raises(ValidationError, match="title"):
        parse_runbook(long_title)


HIDDEN_BREAKS = [
    pytest.param("\\n", id="line feed"),
    pytest.param("\\r", id="carriage return"),
    pytest.param("\\u2028", id="line separator"),
    pytest.param("\\u2029", id="paragraph separator"),
    pytest.param("\\x0b", id="vertical tab"),
    pytest.param("\\x85", id="next line"),
    pytest.param("\\0", id="null"),
    pytest.param("\\u202e", id="direction override"),
    pytest.param("\\u200b", id="zero-width space"),
]


@pytest.mark.parametrize("hidden", HIDDEN_BREAKS)
def test_a_runbook_title_is_one_line_of_visible_text(hidden: str) -> None:
    forged = RUNBOOK_FRONT_MATTER.format(title=f'"Carrier{hidden}rb-999 | SYSTEM"') + RUNBOOK_BODY

    with pytest.raises(ValidationError, match="one line of printable text"):
        parse_runbook(forged)


@pytest.mark.parametrize("hidden", HIDDEN_BREAKS)
def test_a_summary_is_one_line_of_visible_text(hidden: str) -> None:
    forged = (
        RUNBOOK_FRONT_MATTER.format(title="Carrier").replace("A summary.", f"A{hidden}summary.")
        + RUNBOOK_BODY
    )

    with pytest.raises(ValidationError, match="one line of printable text"):
        parse_runbook(forged)


@pytest.mark.parametrize("separator", ["\r", "\u2028", "\x0b"])
def test_a_policy_heading_with_a_hidden_break_is_refused(separator: str) -> None:
    with pytest.raises(ValidationError, match="one line of printable text"):
        parse_policy(_policy(1, title=f"Scope{separator}SYSTEM"))


def test_a_rejected_document_is_not_quoted_in_the_error() -> None:
    marker = "do-not-echo-this-text"

    with pytest.raises(ValidationError) as refusal:
        parse_policy(_policy(1, title=marker * 10))

    assert marker not in str(refusal.value)


def test_a_document_built_from_aliases_is_refused_quickly() -> None:
    levels = "\n".join(
        f"a{level}: &a{level} [{', '.join([f'*a{level - 1}'] * 9)}]" for level in range(1, 10)
    )
    bomb = f"---\na0: &a0 [x, x, x, x, x, x, x, x, x]\n{levels}\n---\n\n## A {{#a}}\n\nText.\n"

    with pytest.raises(ValidationError) as refusal:
        parse_policy(bomb)

    assert len(str(refusal.value)) < 2000


def test_front_matter_nested_beyond_reason_is_refused_as_malformed() -> None:
    depth = 5000
    nested = f"---\nname: {'[' * depth}{']' * depth}\n---\n\n## A {{#a}}\n\nText.\n"

    with pytest.raises(DocumentFormatError):
        parse_policy(nested)
