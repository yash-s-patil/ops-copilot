import shutil
from pathlib import Path

import pytest

from opscp_common.documents import DocumentFormatError, parse_policy
from opscp_seed.policies import read_policies

from .conftest import REPO_ROOT

POLICIES_DIR = REPO_ROOT / "data" / "policies"
NAMES = ["premium-customer-handling", "refund-policy", "shipping-sla"]


def test_every_policy_is_read_by_name_in_order() -> None:
    texts = read_policies(POLICIES_DIR)

    assert list(texts) == NAMES
    assert all(parse_policy(text).name == name for name, text in texts.items())


def test_windows_line_endings_are_stored_as_plain_line_breaks(tmp_path: Path) -> None:
    original = (POLICIES_DIR / "shipping-sla.md").read_text(encoding="utf-8")
    (tmp_path / "shipping-sla.md").write_bytes(original.replace("\n", "\r\n").encode())

    texts = read_policies(tmp_path)

    assert texts == {"shipping-sla": original}


def test_files_that_are_not_markdown_are_left_alone(tmp_path: Path) -> None:
    shutil.copytree(POLICIES_DIR, tmp_path, dirs_exist_ok=True)
    (tmp_path / "notes.txt").write_text("not a policy", encoding="utf-8")

    assert list(read_policies(tmp_path)) == NAMES


def test_a_malformed_policy_is_named_in_the_error(tmp_path: Path) -> None:
    (tmp_path / "refund-policy.md").write_text("no front matter", encoding="utf-8")

    with pytest.raises(DocumentFormatError, match=r"^refund-policy\.md: "):
        read_policies(tmp_path)


def test_a_policy_filed_under_another_name_is_refused(tmp_path: Path) -> None:
    shutil.copy(POLICIES_DIR / "refund-policy.md", tmp_path / "refunds.md")

    with pytest.raises(DocumentFormatError, match=r"refunds\.md declares itself as refund-policy"):
        read_policies(tmp_path)


def test_a_missing_directory_is_an_error_not_an_empty_upload(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        read_policies(tmp_path / "policies")
