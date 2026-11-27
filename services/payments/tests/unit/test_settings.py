"""Settings of the two payments programs: what must be set, and what must never run in Azure."""

from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError

from opscp_payments.settings import (
    DevCallers,
    DevIssuer,
    EntraCallers,
    ManagedIdentity,
    SimSettings,
    WorkerSettings,
)
from opscp_payments_testing.keys import AUDIENCE, ISSUER, one_line, private_pem, public_pem

LIMITS = str(Path(__file__).resolve().parents[4] / "config" / "limits.yaml")
TENANT = "7d0c2f4e-1111-4222-8333-444455556666"
CLIENT = "0a1b2c3d-aaaa-4bbb-8ccc-ddddeeeeffff"


def _ec_key() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return one_line(pem.decode("ascii"))


EC_PRIVATE = _ec_key()


def _worker(**changed: Any) -> WorkerSettings:
    values: dict[str, Any] = {
        "environment": "local",
        "limits_file": LIMITS,
        "auth_mode": "dev",
        "payments_url": "http://payments-sim:8080",
        "payments_api_audience": AUDIENCE,
        "dev_issuer": ISSUER,
        "dev_issuer_private_key": one_line(private_pem()),
    }
    return WorkerSettings(_env_file=None, **(values | changed))  # pyright: ignore[reportCallIssue] -- set by keyword here


def _azure_worker(**changed: Any) -> WorkerSettings:
    values: dict[str, Any] = {
        "environment": "demo",
        "auth_mode": "entra",
        "payments_url": "https://payments-sim.internal.example",
        "payments_scope": "api://opscp-payments/.default",
        "azure_client_id": CLIENT,
        "payments_api_audience": None,
        "dev_issuer": None,
        "dev_issuer_private_key": None,
    }
    return _worker(**(values | changed))


def _sim(**changed: Any) -> SimSettings:
    values: dict[str, Any] = {
        "environment": "local",
        "limits_file": LIMITS,
        "auth_mode": "dev",
        "payments_api_audience": AUDIENCE,
        "dev_issuer": ISSUER,
        "dev_issuer_public_key": one_line(public_pem()),
    }
    return SimSettings(_env_file=None, **(values | changed))  # pyright: ignore[reportCallIssue] -- set by keyword here


def _refusal(build: Any, **changed: Any) -> str:
    with pytest.raises(ValidationError) as refused:
        build(**changed)
    return str(refused.value)


@pytest.fixture(autouse=True)
def no_settings_from_this_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ENVIRONMENT",
        "AUTH_MODE",
        "DEV_ISSUER",
        "DEV_ISSUER_PRIVATE_KEY",
        "DEV_ISSUER_PUBLIC_KEY",
        "PAYMENTS_URL",
        "PAYMENTS_SCOPE",
        "PAYMENTS_API_AUDIENCE",
        "AZURE_CLIENT_ID",
        "TENANT_ID",
        "CHAOS_ENABLED",
        "SIM_ALLOW_FORCED_OUTCOME",
        "LIMITS_FILE",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_locally_the_worker_signs_its_own_token() -> None:
    settings = _worker()
    identity = settings.identity()

    assert isinstance(identity, DevIssuer)
    assert (identity.issuer, identity.audience) == (ISSUER, AUDIENCE)
    assert identity.private_key_pem.get_secret_value() == private_pem()
    assert (settings.json_logs, settings.chaos_enabled) == (False, False)


def test_the_private_key_is_not_shown_when_settings_are_printed() -> None:
    printed = repr(_worker()) + repr(_worker().identity())

    assert one_line(private_pem())[:40] not in printed
    assert "PRIVATE KEY" not in printed


def test_in_azure_the_worker_uses_its_managed_identity() -> None:
    settings = _azure_worker()

    assert settings.identity() == ManagedIdentity(
        scope="api://opscp-payments/.default", client_id=UUID(CLIENT)
    )
    assert settings.json_logs is True


@pytest.mark.parametrize("missing", ["payments_scope", "azure_client_id"])
def test_entra_mode_needs_the_scope_and_the_identity(missing: str) -> None:
    assert "AUTH_MODE=entra needs" in _refusal(_azure_worker, **{missing: ""})


@pytest.mark.parametrize("environment", ["demo", "prod"])
def test_the_worker_signs_its_own_token_only_on_a_developers_machine(environment: str) -> None:
    refused = _refusal(_worker, environment=environment, payments_url="https://sim.example")

    assert "AUTH_MODE=dev is allowed only when ENVIRONMENT is local" in refused


@pytest.mark.parametrize(
    "missing", ["dev_issuer", "payments_api_audience", "dev_issuer_private_key"]
)
def test_dev_mode_needs_all_of_its_settings(missing: str) -> None:
    assert "AUTH_MODE=dev needs" in _refusal(_worker, **{missing: ""})


@pytest.mark.parametrize(
    "key",
    ["not base64 !", "aGVsbG8=", one_line(public_pem()), EC_PRIVATE],
    ids=["not base64", "not a key", "a public key", "an elliptic-curve key"],
)
def test_a_private_key_that_is_not_an_rsa_private_key_is_refused_without_being_shown(
    key: str,
) -> None:
    refused = _refusal(_worker, dev_issuer_private_key=key)

    assert "DEV_ISSUER_PRIVATE_KEY must be a base64-encoded PEM RSA key" in refused
    assert key not in refused


def test_failure_drills_never_run_in_production() -> None:
    refused = _refusal(_azure_worker, environment="prod", chaos_enabled=True)

    assert "CHAOS_ENABLED is not allowed when ENVIRONMENT is prod" in refused
    assert _azure_worker(environment="demo", chaos_enabled=True).chaos_enabled is True
    assert _azure_worker(environment="prod").chaos_enabled is False


@pytest.mark.parametrize("environment", ["demo", "prod"])
def test_outside_local_development_the_payment_service_is_reached_over_tls(
    environment: str,
) -> None:
    refused = _refusal(_azure_worker, environment=environment, payments_url="http://sim.example")

    assert "PAYMENTS_URL must be https outside local development" in refused


def test_the_payment_service_address_must_be_a_web_address() -> None:
    assert "payments_url" in _refusal(_worker, payments_url="file:///etc/passwd")


def test_locally_the_simulator_trusts_the_development_key() -> None:
    callers = _sim().callers()

    assert callers == DevCallers(issuer=ISSUER, audience=AUDIENCE, public_key_pem=public_pem())
    assert _sim().sim_allow_forced_outcome is False


def test_in_the_demo_the_simulator_trusts_the_tenant() -> None:
    settings = _sim(
        environment="demo",
        auth_mode="entra",
        tenant_id=TENANT,
        payments_api_audience=CLIENT,
        dev_issuer="",
        dev_issuer_public_key="",
        sim_allow_forced_outcome=True,
    )

    assert settings.callers() == EntraCallers(tenant_id=UUID(TENANT), audience=CLIENT)
    assert settings.callers().model_dump()["authority"] == "login.microsoftonline.com"
    assert settings.sim_allow_forced_outcome is True


@pytest.mark.parametrize("auth_mode", ["entra", "dev"])
def test_the_simulator_refuses_to_start_in_production(auth_mode: str) -> None:
    refused = _refusal(_sim, environment="prod", auth_mode=auth_mode, tenant_id=TENANT)

    assert "The payment simulator does not run when ENVIRONMENT is prod" in refused


def test_the_simulator_trusts_the_development_key_only_on_a_developers_machine() -> None:
    assert "AUTH_MODE=dev is allowed only when ENVIRONMENT is local" in _refusal(
        _sim, environment="demo"
    )


def test_the_simulator_needs_a_tenant_in_entra_mode() -> None:
    assert "AUTH_MODE=entra needs TENANT_ID" in _refusal(
        _sim, environment="demo", auth_mode="entra"
    )


@pytest.mark.parametrize("missing", ["dev_issuer", "dev_issuer_public_key"])
def test_the_simulator_needs_the_development_issuer_and_key_in_dev_mode(missing: str) -> None:
    assert "AUTH_MODE=dev needs" in _refusal(_sim, **{missing: ""})


def test_the_simulator_refuses_a_public_key_that_is_not_one() -> None:
    refused = _refusal(_sim, dev_issuer_public_key=one_line(private_pem()))

    assert "DEV_ISSUER_PUBLIC_KEY must be a base64-encoded PEM RSA key" in refused


def test_the_simulator_must_be_told_its_audience() -> None:
    assert "payments_api_audience" in _refusal(_sim, payments_api_audience="")


@pytest.mark.parametrize("auth_mode", ["eval", "none", ""])
def test_an_unknown_auth_mode_is_refused(auth_mode: str) -> None:
    assert "auth_mode" in _refusal(_sim, auth_mode=auth_mode)
    assert "auth_mode" in _refusal(_worker, auth_mode=auth_mode)
