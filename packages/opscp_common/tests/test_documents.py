from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from opscp_common.documents import (
    RUNBOOK_SECTIONS,
    SUMMARY_MAX_CHARS,
    DocumentFormatError,
    load_policies,
    load_runbooks,
    parse_policy,
    parse_runbook,
)

RUNBOOK = """---
id: rb-001
title: Address validation failed
summary: "Confirm the address, then re-trigger validation."
version: 4
updated: 2026-09-22
---

## Symptoms

The order is in `packed`.

## Checks

1. Open the order timeline.
2. Read the failure reason.

## Resolution

Confirm the address.

### After the fix

Tell the customer.

## Compensation

See rb-021.

## Escalation

Escalate to L2.
"""

POLICY = """---
name: refund-policy
version: 3
effective_date: 2026-10-01
---

## Goodwill refunds {#goodwill}

Up to 15% for premium customers.

- One per order.

## Full refunds {#full-refund}

Three cases only.
"""


def test_a_runbook_is_read_with_its_front_matter_and_five_sections() -> None:
    runbook = parse_runbook(RUNBOOK)

    assert runbook.id == "rb-001"
    assert runbook.title == "Address validation failed"
    assert runbook.summary == "Confirm the address, then re-trigger validation."
    assert runbook.version == 4
    assert runbook.updated == date(2026, 9, 22)
    assert tuple(section.id for section in runbook.sections) == RUNBOOK_SECTIONS
    assert [section.title for section in runbook.sections][:2] == ["Symptoms", "Checks"]


def test_section_content_excludes_the_heading_and_surrounding_blank_lines() -> None:
    checks = parse_runbook(RUNBOOK).sections[1]

    assert checks.content == "1. Open the order timeline.\n2. Read the failure reason."


def test_a_deeper_heading_stays_inside_its_section() -> None:
    resolution = parse_runbook(RUNBOOK).sections[2]

    assert resolution.content.endswith("### After the fix\n\nTell the customer.")


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param(RUNBOOK.replace("## Compensation", "## Refunds"), id="renamed section"),
        pytest.param(RUNBOOK.replace("## Escalation\n\nEscalate to L2.\n", ""), id="missing"),
        pytest.param(RUNBOOK + "\n## Notes\n\nMore.\n", id="extra section"),
        pytest.param(
            RUNBOOK.replace("## Symptoms", "## Swap")
            .replace("## Checks", "## Symptoms")
            .replace("## Swap", "## Checks"),
            id="out of order",
        ),
        pytest.param(
            RUNBOOK.replace("---\n\n## Symptoms", "---\n\nIntro.\n\n## Symptoms"),
            id="text before the first section",
        ),
        pytest.param(RUNBOOK.partition("## Symptoms")[0], id="no sections"),
        pytest.param(RUNBOOK.removeprefix("---\n"), id="no front matter"),
        pytest.param(RUNBOOK.replace("version: 4", "version: [4"), id="invalid yaml"),
        pytest.param(RUNBOOK.replace("## Checks", "## Checks {#Checks}"), id="malformed id"),
        pytest.param(RUNBOOK.replace("## Checks", "## {#checks}"), id="no title"),
    ],
)
def test_a_runbook_with_the_wrong_layout_is_refused(broken: str) -> None:
    with pytest.raises(DocumentFormatError):
        parse_runbook(broken)


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param(RUNBOOK.replace("id: rb-001", "id: runbook-1"), id="id pattern"),
        pytest.param(RUNBOOK.replace("version: 4", "version: 0"), id="version zero"),
        pytest.param(RUNBOOK.replace("version: 4", 'version: "4"'), id="version as text"),
        pytest.param(RUNBOOK.replace("updated: 2026-09-22", "updated: yesterday"), id="date"),
        pytest.param(RUNBOOK.replace("title: Address validation failed\n", ""), id="no title"),
        pytest.param(RUNBOOK.replace("version: 4", "version: 4\nowner: ops"), id="unknown key"),
        pytest.param(
            RUNBOOK.replace("Confirm the address, then", "x" * SUMMARY_MAX_CHARS), id="summary"
        ),
        pytest.param(RUNBOOK.replace("Escalate to L2.\n", ""), id="empty section"),
        pytest.param("---\n- rb-001\n---\n" + RUNBOOK.partition("---\n\n")[2], id="list meta"),
        pytest.param(RUNBOOK.replace("version: 4", "version: 4\n2026: x"), id="key not text"),
        pytest.param(RUNBOOK.replace("version: 4", "version: 4\nsections: []"), id="sections"),
    ],
)
def test_a_runbook_with_invalid_values_is_refused(broken: str) -> None:
    with pytest.raises(ValidationError):
        parse_runbook(broken)


def test_a_policy_is_read_with_section_ids_and_summaries() -> None:
    policy = parse_policy(POLICY)

    assert policy.name == "refund-policy"
    assert policy.version == 3
    assert policy.effective_date == date(2026, 10, 1)
    assert [(section.id, section.title) for section in policy.sections] == [
        ("goodwill", "Goodwill refunds"),
        ("full-refund", "Full refunds"),
    ]
    goodwill = policy.sections[0]
    assert goodwill.summary == "Up to 15% for premium customers."
    assert goodwill.content == "Up to 15% for premium customers.\n\n- One per order."


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param(POLICY.replace(" {#goodwill}", ""), id="no id"),
        pytest.param(POLICY.replace("{#full-refund}", "{#goodwill}"), id="duplicate id"),
        pytest.param(POLICY.replace("{#full-refund}", "{#Full-Refund}"), id="id not a slug"),
        pytest.param(POLICY.replace("{#full-refund}", "{#full_refund}"), id="underscore in id"),
        pytest.param(POLICY.replace(" {#full-refund}", "{#full-refund} x"), id="text after id"),
        pytest.param(POLICY.replace("Full refunds", "Full {refunds}"), id="brace in title"),
    ],
)
def test_a_policy_section_needs_a_unique_slug_id(broken: str) -> None:
    with pytest.raises(DocumentFormatError):
        parse_policy(broken)


def test_a_policy_section_must_open_with_a_one_line_summary() -> None:
    wrapped = POLICY.replace("Three cases only.", "Three cases\nonly.")

    with pytest.raises(DocumentFormatError, match="one-line summary"):
        parse_policy(wrapped)


def test_a_policy_summary_cannot_be_a_list_item() -> None:
    with pytest.raises(ValidationError):
        parse_policy(POLICY.replace("Three cases only.", "- Three cases only."))


def test_a_heading_padded_with_spaces_is_read_quickly() -> None:
    padded = POLICY.replace("Full refunds {#", "Full refunds" + " " * 50_000 + "{#")

    assert parse_policy(padded).sections[1].title == "Full refunds"


def test_runbooks_are_loaded_in_id_order_and_subfolders_are_left_out(tmp_path: Path) -> None:
    (tmp_path / "rb-002.md").write_text(RUNBOOK.replace("rb-001", "rb-002"), encoding="utf-8")
    (tmp_path / "rb-001.md").write_text(RUNBOOK, encoding="utf-8")
    (tmp_path / "chaos").mkdir()
    (tmp_path / "chaos" / "rb-901.md").write_text(RUNBOOK, encoding="utf-8")

    assert [runbook.id for runbook in load_runbooks(tmp_path)] == ["rb-001", "rb-002"]


def test_a_runbook_file_must_be_named_after_its_id(tmp_path: Path) -> None:
    (tmp_path / "rb-007.md").write_text(RUNBOOK, encoding="utf-8")

    with pytest.raises(DocumentFormatError, match=r"rb-007\.md declares itself as rb-001"):
        load_runbooks(tmp_path)


def test_a_broken_file_is_named_in_the_error(tmp_path: Path) -> None:
    (tmp_path / "rb-001.md").write_text(RUNBOOK.replace("version: 4", "version: 0"), "utf-8")

    with pytest.raises(DocumentFormatError, match=r"^rb-001\.md: "):
        load_runbooks(tmp_path)


def test_a_missing_directory_is_an_error_not_an_empty_result(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        load_runbooks(tmp_path / "runbooks")


def test_policies_are_loaded_by_name(tmp_path: Path) -> None:
    (tmp_path / "refund-policy.md").write_text(POLICY, encoding="utf-8")

    assert [policy.name for policy in load_policies(tmp_path)] == ["refund-policy"]


def test_a_policy_file_must_be_named_after_its_document(tmp_path: Path) -> None:
    (tmp_path / "refunds.md").write_text(POLICY, encoding="utf-8")

    with pytest.raises(DocumentFormatError, match=r"refunds\.md declares itself as refund-policy"):
        load_policies(tmp_path)
