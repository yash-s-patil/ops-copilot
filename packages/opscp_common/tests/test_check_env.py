import runpy
from collections.abc import Callable
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "infra" / "local" / "check_env.py"
EXAMPLE = "# Local development only.\nENVIRONMENT=local\nPOSTGRES_PASSWORD=\nNEW_SETTING=\n"

type Missing = Callable[[Path, Path], list[str]]


@pytest.fixture
def missing() -> Missing:
    return runpy.run_path(str(SCRIPT))["missing"]


def _files(tmp_path: Path, env: str) -> tuple[Path, Path]:
    (tmp_path / ".env").write_text(env, encoding="utf-8")
    (tmp_path / ".env.example").write_text(EXAMPLE, encoding="utf-8")
    return tmp_path / ".env", tmp_path / ".env.example"


def test_a_setting_added_since_the_file_was_made_is_reported(
    tmp_path: Path, missing: Missing
) -> None:
    env, example = _files(tmp_path, "ENVIRONMENT=local\nPOSTGRES_PASSWORD=secret\n")

    assert missing(env, example) == ["NEW_SETTING"]


@pytest.mark.parametrize("blank", ["NEW_SETTING=", 'NEW_SETTING=""', "NEW_SETTING=  "])
def test_a_setting_left_empty_counts_as_missing(
    tmp_path: Path, missing: Missing, blank: str
) -> None:
    env, example = _files(tmp_path, f"ENVIRONMENT=local\nPOSTGRES_PASSWORD=secret\n{blank}\n")

    assert missing(env, example) == ["NEW_SETTING"]


def test_a_current_file_has_nothing_missing(tmp_path: Path, missing: Missing) -> None:
    env, example = _files(
        tmp_path, 'ENVIRONMENT=local\nPOSTGRES_PASSWORD=secret\nNEW_SETTING="a=b;c=d"\nEXTRA=1\n'
    )

    assert missing(env, example) == []


def test_the_repository_example_and_generator_agree(
    tmp_path: Path, missing: Missing, capsys: pytest.CaptureFixture[str]
) -> None:
    runpy.run_path(str(SCRIPT.with_name("make_env.py")), run_name="__main__")
    generated = tmp_path / ".env"
    generated.write_text(capsys.readouterr().out, encoding="utf-8")

    assert missing(generated, SCRIPT.parents[2] / ".env.example") == []
    assert missing(SCRIPT.parents[2] / ".env.example", generated) != []
