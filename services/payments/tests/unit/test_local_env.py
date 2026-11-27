"""What `make env` and docker compose give the two payments programs on a developer's machine."""

import runpy
from pathlib import Path

import pytest

from opscp_payments.adapters.tokens import DevTokens
from opscp_payments.settings import DevCallers, DevIssuer, SimSettings, WorkerSettings
from opscp_payments.sim.auth import CallerTokens, FixedKey

REPO_ROOT = Path(__file__).resolve().parents[4]
LIMITS = str(REPO_ROOT / "config" / "limits.yaml")


@pytest.fixture
def generated(capsys: pytest.CaptureFixture[str]) -> dict[str, str]:
    runpy.run_path(str(REPO_ROOT / "infra" / "local" / "make_env.py"), run_name="__main__")
    lines = capsys.readouterr().out.splitlines()
    return {name: value.strip('"') for name, _, value in (line.partition("=") for line in lines)}


def _compose_block(service: str) -> str:
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    return compose.split(f"  {service}:\n")[1].split("\n\n")[0]


async def test_the_generated_keys_let_the_worker_and_only_the_worker_call_the_simulator(
    generated: dict[str, str],
) -> None:
    worker = WorkerSettings(
        _env_file=None,  # pyright: ignore[reportCallIssue] -- a setting of the loader, not a field
        environment="local",
        limits_file=LIMITS,
        auth_mode="dev",
        payments_url="http://payments-sim:8080",
        payments_api_audience=generated["PAYMENTS_API_AUDIENCE"],
        dev_issuer=generated["PAYMENTS_DEV_ISSUER"],
        dev_issuer_private_key=generated["PAYMENTS_DEV_PRIVATE_KEY"],
    ).identity()
    sim = SimSettings(
        _env_file=None,  # pyright: ignore[reportCallIssue] -- a setting of the loader, not a field
        environment="local",
        limits_file=LIMITS,
        auth_mode="dev",
        payments_api_audience=generated["PAYMENTS_API_AUDIENCE"],
        dev_issuer=generated["PAYMENTS_DEV_ISSUER"],
        dev_issuer_public_key=generated["PAYMENTS_DEV_PUBLIC_KEY"],
    ).callers()
    assert isinstance(worker, DevIssuer)
    assert isinstance(sim, DevCallers)
    callers = CallerTokens(
        FixedKey(sim.public_key_pem),
        issuer=sim.issuer,
        audience=sim.audience,
        tenant_id=None,
        clock_skew_s=60,
    )

    assert await callers.verdict(await DevTokens(worker).token()) == "accepted"


def test_payments_has_a_key_of_its_own_and_not_the_one_that_signs_people_in(
    generated: dict[str, str],
) -> None:
    assert generated["PAYMENTS_DEV_PRIVATE_KEY"] != generated["DEV_ISSUER_PRIVATE_KEY"]
    assert generated["PAYMENTS_DEV_PUBLIC_KEY"] != generated["DEV_ISSUER_PUBLIC_KEY"]
    assert generated["PAYMENTS_DEV_ISSUER"] != generated["DEV_ISSUER"]
    assert generated["PAYMENTS_API_AUDIENCE"] != generated["MCP_API_AUDIENCE"]


def test_compose_gives_the_worker_the_payments_key_and_the_simulator_no_private_key() -> None:
    worker, sim = _compose_block("payments-worker"), _compose_block("payments-sim")

    assert "DEV_ISSUER_PRIVATE_KEY: ${PAYMENTS_DEV_PRIVATE_KEY:-}" in worker
    assert "DATABASE_USER: payments_worker" in worker
    assert "PRIVATE_KEY" not in sim
    assert "DATABASE" not in sim
    assert "ports:" not in worker


@pytest.mark.parametrize("service", ["payments-worker", "payments-sim"])
def test_both_containers_run_locked_down(service: str) -> None:
    block = _compose_block(service)

    assert "read_only: true" in block
    assert "cap_drop:\n      - ALL" in block
    assert "no-new-privileges:true" in block
    assert "env_file" not in block


def test_the_image_runs_as_an_unprivileged_user_and_copies_no_secrets() -> None:
    image = (REPO_ROOT / "services" / "payments" / "Dockerfile").read_text(encoding="utf-8")
    ignored = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "USER 10001:10001" in image
    assert ".env" not in image
    assert ignored.splitlines()[1] == "*"
