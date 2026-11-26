"""The local stand-in for sign-in: the generated key, the token command, and the server."""

import runpy
from typing import Any

import pytest
from pydantic import AnyHttpUrl, ValidationError

from opscp_mcp.authz.identity import identity_from_claims
from opscp_mcp.authz.tokens import sign_in_with
from opscp_mcp.settings import McpSettings
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA, ROHAN, SAM
from opscp_mcp_testing.paths import REPO_ROOT, RUNBOOKS_DIR

LOCAL = REPO_ROOT / "infra" / "local"
PEOPLE = {"priya": PRIYA, "rohan": ROHAN, "arjun": ARJUN, "kavita": KAVITA, "sam": SAM}


def _generated(capsys: pytest.CaptureFixture[str]) -> dict[str, str]:
    runpy.run_path(str(LOCAL / "make_env.py"), run_name="__main__")
    pairs = (line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    return {name: value.strip('"') for name, value in pairs}


@pytest.fixture
def local_env(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> dict[str, str]:
    env = _generated(capsys) | {
        "LIMITS_FILE": str(REPO_ROOT / "config" / "limits.yaml"),
        "RUNBOOKS_DIR": str(RUNBOOKS_DIR),
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return env


@pytest.fixture
def token_command() -> dict[str, Any]:
    return runpy.run_path(str(LOCAL / "dev_token.py"))


@pytest.mark.usefixtures("local_env")
@pytest.mark.parametrize("name", PEOPLE)
async def test_a_token_from_the_command_is_accepted_as_that_demo_user(
    token_command: dict[str, Any], name: str
) -> None:
    settings = McpSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment
    sign_in = sign_in_with(settings.sign_in(), settings.mcp_public_url)

    accepted = await sign_in.verify_token(token_command["token_for"](name))

    assert accepted is not None
    assert identity_from_claims(accepted.claims) == PEOPLE[name]


@pytest.mark.usefixtures("local_env")
async def test_a_token_signed_with_another_machines_key_is_refused(
    token_command: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    token = token_command["token_for"]("kavita")
    elsewhere = _generated(capsys)
    settings = McpSettings(dev_issuer_public_key=elsewhere["DEV_ISSUER_PUBLIC_KEY"])  # pyright: ignore[reportCallIssue] -- fields come from the environment
    sign_in = sign_in_with(settings.sign_in(), AnyHttpUrl("http://127.0.0.1:8000"))

    assert await sign_in.verify_token(token) is None


def test_the_command_knows_exactly_the_five_demo_users(token_command: dict[str, Any]) -> None:
    assert sorted(token_command["DEMO_USERS"]) == sorted(PEOPLE)


def test_the_private_key_is_never_given_to_the_server() -> None:
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    image = (REPO_ROOT / "services" / "mcp" / "Dockerfile").read_text(encoding="utf-8")
    ignored = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "DEV_ISSUER_PUBLIC_KEY" in compose
    assert "PRIVATE_KEY" not in compose + image
    assert "env_file" not in compose
    assert "!infra" not in ignored


def test_the_demo_users_are_the_engineers_the_seed_assigns_tickets_to(
    token_command: dict[str, Any],
) -> None:
    lines = (LOCAL / "seed.env").read_text(encoding="utf-8").splitlines()
    seeded = dict(line.split("=", 1) for line in lines if line.startswith("SEED_DEMO__"))
    users = token_command["DEMO_USERS"]

    for slot, name in (("L1_WEST", "priya"), ("L1_SOUTH", "rohan"), ("L2_WEST", "arjun")):
        assert seeded[f"SEED_DEMO__{slot}__OID"] == users[name].oid
        assert seeded[f"SEED_DEMO__{slot}__NAME"].strip('"') == users[name].name


def test_the_command_names_the_setting_an_old_env_file_lacks(
    token_command: dict[str, Any], local_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEV_ISSUER_PRIVATE_KEY")

    with pytest.raises(ValidationError, match="dev_issuer_private_key") as refusal:
        token_command["token_for"]("priya")

    assert local_env["DEV_ISSUER_PUBLIC_KEY"] not in str(refusal.value)
