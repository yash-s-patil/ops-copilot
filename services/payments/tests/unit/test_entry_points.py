"""How the two programs start: what they read, what they build, and what they close."""

import asyncio
import logging
import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient
from pydantic import ValidationError

from opscp_common.limits import Limits
from opscp_common.settings import DatabaseSettings
from opscp_payments.adapters.tokens import DevTokens, ManagedIdentityTokens
from opscp_payments.settings import WorkerSettings
from opscp_payments.sim import __main__ as sim_entry
from opscp_payments.sim import app as sim_app
from opscp_payments.worker import main as worker_main
from opscp_payments.worker.outbox import Handoffs
from opscp_payments_testing.keys import AUDIENCE, ISSUER, one_line, private_pem, public_pem

LIMITS = str(Path(__file__).resolve().parents[4] / "config" / "limits.yaml")
CLIENT = "0a1b2c3d-aaaa-4bbb-8ccc-ddddeeeeffff"
SCOPE = "api://opscp-payments/.default"


@pytest.fixture
def sim_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "ENVIRONMENT": "local",
        "AUTH_MODE": "dev",
        "LIMITS_FILE": LIMITS,
        "PAYMENTS_API_AUDIENCE": AUDIENCE,
        "DEV_ISSUER": ISSUER,
        "DEV_ISSUER_PUBLIC_KEY": one_line(public_pem()),
        "SIM_ALLOW_FORCED_OUTCOME": "true",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def worker_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "ENVIRONMENT": "local",
        "AUTH_MODE": "dev",
        "LIMITS_FILE": LIMITS,
        "PAYMENTS_URL": "http://payments-sim:8080",
        "PAYMENTS_API_AUDIENCE": AUDIENCE,
        "DEV_ISSUER": ISSUER,
        "DEV_ISSUER_PRIVATE_KEY": one_line(private_pem()),
        "DATABASE_HOST": "127.0.0.1",
        "DATABASE_PORT": "1",
        "DATABASE_NAME": "nothing",
        "DATABASE_USER": "payments_worker",
        "DATABASE_PASSWORD": "unused",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    started: list[tuple[str, dict[str, Any]]] = []

    def run(application: str, **options: Any) -> None:
        started.append((application, options))

    monkeypatch.setattr(uvicorn, "run", run)
    return started


def test_the_simulator_is_served_so_that_no_request_target_is_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = _started(monkeypatch)

    sim_entry.main()

    assert started == [
        (
            "opscp_payments.sim.app:create_app",
            {
                "factory": True,
                "host": "0.0.0.0",  # noqa: S104 -- the value under test
                "port": 8080,
                "log_config": None,
                "access_log": False,
                "ws": "none",
                "server_header": False,
            },
        )
    ]


@pytest.mark.usefixtures("sim_environment")
def test_the_simulator_starts_from_its_settings_and_closes_what_it_opened() -> None:
    app = sim_app.create_app()

    with TestClient(app) as http:
        healthy = http.get("/healthz").status_code
        unsigned = http.post("/refunds", json={}).status_code

    assert (healthy, unsigned) == (200, 401)


@pytest.mark.usefixtures("sim_environment")
def test_the_simulator_does_not_start_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "prod")

    with pytest.raises(ValidationError, match="does not run when ENVIRONMENT is prod"):
        sim_app.create_app()


@pytest.mark.usefixtures("worker_environment")
def test_the_worker_reads_its_settings_and_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    served: list[tuple[WorkerSettings, DatabaseSettings, Limits]] = []

    async def serve(settings: WorkerSettings, database: DatabaseSettings, limits: Limits) -> None:
        served.append((settings, database, limits))

    monkeypatch.setattr(worker_main, "serve", serve)

    worker_main.main()

    ((settings, database, limits),) = served
    assert (settings.auth_mode, database.user) == ("dev", "payments_worker")
    assert limits.payments.max_attempts > 0
    assert logging.getLogger("httpx2").level == logging.WARNING


@pytest.mark.usefixtures("worker_environment")
def test_the_worker_does_not_start_with_a_setting_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PAYMENTS_URL")

    with pytest.raises(ValidationError, match="payments_url"):
        worker_main.main()


@pytest.mark.usefixtures("worker_environment")
async def test_serving_wires_the_worker_runs_it_and_closes_everything(
    limits: Limits, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ran: list[tuple[Handoffs, float, asyncio.Event]] = []

    async def run(
        handoffs: Handoffs,
        poll_interval_s: float,
        stop: asyncio.Event,
        *,
        full_batch: int,
        beat: Callable[[], None],
    ) -> None:
        ran.append((handoffs, poll_interval_s, stop))
        assert full_batch == limits.payments.claim_batch
        beat()

    monkeypatch.setattr(worker_main, "run", run)
    monkeypatch.setenv("HEARTBEAT_FILE", str(tmp_path / "heartbeat"))
    settings = WorkerSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment
    database = DatabaseSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment

    await worker_main.serve(settings, database, limits)

    ((handoffs, poll_interval_s, stop),) = ran
    assert isinstance(handoffs, Handoffs)
    assert (poll_interval_s, stop.is_set()) == (limits.payments.poll_interval_s, False)
    assert (tmp_path / "heartbeat").exists()


@pytest.mark.usefixtures("worker_environment")
async def test_locally_the_worker_signs_its_own_tokens() -> None:
    settings = WorkerSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment

    async with worker_main._tokens(settings) as tokens:  # pyright: ignore[reportPrivateUsage] -- the wiring under test
        assert isinstance(tokens, DevTokens)


async def test_in_azure_the_worker_asks_as_its_own_identity_and_closes_the_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: list[dict[str, Any]] = []
    closed: list[bool] = []

    class Credential:
        def __init__(self, **options: Any) -> None:
            made.append(options)

        async def __aenter__(self) -> "Credential":
            return self

        async def __aexit__(self, *_exc: object) -> None:
            closed.append(True)

    monkeypatch.setattr(worker_main, "ManagedIdentityCredential", Credential)
    settings = WorkerSettings(
        _env_file=None,  # pyright: ignore[reportCallIssue] -- a setting of the loader, not a field
        environment="demo",
        limits_file=LIMITS,
        auth_mode="entra",
        payments_url="https://payments-sim.internal.example",
        payments_scope=SCOPE,
        azure_client_id=CLIENT,
    )

    async with worker_main._tokens(settings) as tokens:  # pyright: ignore[reportPrivateUsage] -- the wiring under test
        assert isinstance(tokens, ManagedIdentityTokens)
        assert closed == []

    assert (made, closed) == ([{"client_id": CLIENT}], [True])


def test_the_worker_module_runs_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[bool] = []
    monkeypatch.setattr(worker_main, "main", lambda: ran.append(True))

    runpy.run_module("opscp_payments.worker", run_name="__main__")

    assert ran == [True]
