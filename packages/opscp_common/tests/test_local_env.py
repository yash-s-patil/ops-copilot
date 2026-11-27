import runpy
from pathlib import Path

import pytest

from opscp_common.settings import DatabaseSettings, StorageSettings

SCRIPT = Path(__file__).resolve().parents[3] / "infra" / "local" / "make_env.py"
VARIABLES = (
    "ENVIRONMENT",
    "POSTGRES_PASSWORD",
    "DATABASE_HOST",
    "DATABASE_NAME",
    "DATABASE_USER",
    "DATABASE_PASSWORD",
    "AZURITE_ACCOUNTS",
    "POLICIES_CONTAINER",
    "BLOB_CONNECTION_STRING",
    "BLOB_CONNECTION_STRING_IN_COMPOSE",
    "AUTH_MODE",
    "MCP_PUBLIC_URL",
    "MCP_API_AUDIENCE",
    "DEV_ISSUER",
    "DEV_ISSUER_PUBLIC_KEY",
    "DEV_ISSUER_PRIVATE_KEY",
    "APPROVAL_SIGNING_KEY",
    "PAYMENTS_API_AUDIENCE",
    "PAYMENTS_DEV_ISSUER",
    "PAYMENTS_DEV_PUBLIC_KEY",
    "PAYMENTS_DEV_PRIVATE_KEY",
    "AGENT_API_AUDIENCE",
    "AGENT_API_CLIENT_ID",
    "AGENT_CLIENT_SECRET",
    "AGENT_CANARY",
    "AGENT_DATABASE_PASSWORD",
)


@pytest.fixture(autouse=True)
def only_the_file(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in VARIABLES:
        monkeypatch.delenv(variable, raising=False)


def _generate(capsys: pytest.CaptureFixture[str]) -> str:
    runpy.run_path(str(SCRIPT), run_name="__main__")
    return capsys.readouterr().out


def _values(generated: str) -> dict[str, str]:
    pairs = (line.split("=", 1) for line in generated.splitlines())
    return {name: value.strip('"') for name, value in pairs}


def test_the_generated_file_holds_exactly_the_documented_variables(
    capsys: pytest.CaptureFixture[str],
) -> None:
    example = (SCRIPT.parents[2] / ".env.example").read_text(encoding="utf-8")
    documented = [line.split("=")[0] for line in example.splitlines() if "=" in line]

    assert list(_values(_generate(capsys))) == documented == list(VARIABLES)


def test_the_settings_accept_the_generated_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(_generate(capsys), encoding="utf-8")

    storage = StorageSettings(_env_file=env_file)  # pyright: ignore[reportCallIssue] -- fields come from the env file
    database = DatabaseSettings(_env_file=env_file)  # pyright: ignore[reportCallIssue] -- fields come from the env file

    assert (storage.environment, storage.policies_container) == ("local", "policies")
    assert storage.blob_connection_string is not None
    assert (database.host, database.name, database.user) == ("127.0.0.1", "meridian", "migrator")


def test_the_storage_key_is_the_one_the_emulator_is_started_with(
    capsys: pytest.CaptureFixture[str],
) -> None:
    values = _values(_generate(capsys))
    account, key = values["AZURITE_ACCOUNTS"].split(":", 1)

    assert f"AccountName={account};AccountKey={key};" in values["BLOB_CONNECTION_STRING"]
    assert f"BlobEndpoint=http://127.0.0.1:10000/{account};" in values["BLOB_CONNECTION_STRING"]


def test_every_run_generates_new_secrets(capsys: pytest.CaptureFixture[str]) -> None:
    first, second = _values(_generate(capsys)), _values(_generate(capsys))

    assert first["POSTGRES_PASSWORD"] != second["POSTGRES_PASSWORD"]
    assert first["DEV_ISSUER_PRIVATE_KEY"] != second["DEV_ISSUER_PRIVATE_KEY"]
    assert first["AZURITE_ACCOUNTS"] != second["AZURITE_ACCOUNTS"]
    assert first["POSTGRES_PASSWORD"] == first["DATABASE_PASSWORD"]
