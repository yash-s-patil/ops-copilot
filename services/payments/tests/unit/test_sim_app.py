"""The simulated payment service over HTTP: what it answers, and to whom."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from opscp_payments.sim.app import build_app
from opscp_payments.sim.auth import CallerTokens, FixedKey, KeysUnavailableError
from opscp_payments.sim.store import Store
from opscp_payments_testing.keys import AUDIENCE, ISSUER, NOW, public_pem, signed, worker_claims

ORDER = {"order_id": 4471, "amount_minor": 62000, "currency": "INR"}
DELAY_S = 3


class _Sim:
    def __init__(self, *, allow_forced_outcome: bool = False) -> None:
        self.seconds = 0.0
        refs = iter(f"sim_{n}" for n in range(1, 1000))
        self.store = Store(
            settle_delay_s=DELAY_S,
            capacity=1000,
            clock=lambda: self.seconds,
            new_ref=lambda: next(refs),
        )
        callers = CallerTokens(
            FixedKey(public_pem()),
            issuer=ISSUER,
            audience=AUDIENCE,
            tenant_id=None,
            clock_skew_s=60,
            now=lambda: NOW,
        )
        self.app = build_app(self.store, callers, allow_forced_outcome=allow_forced_outcome)
        self.http: TestClient

    def submit(
        self,
        key: str | None = "refund-1",
        order: Any = ORDER,
        token: str | None = "",
        **headers: str,
    ) -> Any:
        sent = dict(headers) | _bearer(token)
        if key is not None:
            sent["Idempotency-Key"] = key
        return self.http.post("/refunds", json=order, headers=sent)

    def status(self, provider_ref: str, token: str | None = "") -> Any:
        return self.http.get(f"/refunds/{provider_ref}", headers=_bearer(token))


def _bearer(token: str | None) -> dict[str, str]:
    """An empty string stands for the worker's own token; None for no token at all."""
    if token is None:
        return {}
    return {"Authorization": f"Bearer {token or signed(worker_claims())}"}


def _opened(sim: _Sim) -> Iterator[_Sim]:
    with TestClient(sim.app) as http:
        sim.http = http
        yield sim


@pytest.fixture
def sim() -> Iterator[_Sim]:
    yield from _opened(_Sim())


@pytest.fixture
def drill() -> Iterator[_Sim]:
    yield from _opened(_Sim(allow_forced_outcome=True))


def test_a_refund_is_accepted_and_reported_as_processing(sim: _Sim) -> None:
    response = sim.submit()

    assert response.status_code == 202
    assert response.json() == {"provider_ref": "sim_1", "status": "processing", "reason_code": None}


def test_after_the_delay_the_refund_has_succeeded(sim: _Sim) -> None:
    sim.submit()
    sim.seconds = DELAY_S

    response = sim.status("sim_1")

    assert response.status_code == 200
    assert response.json() == {"provider_ref": "sim_1", "status": "succeeded", "reason_code": None}


def test_the_same_key_again_returns_the_first_refund(sim: _Sim) -> None:
    first = sim.submit().json()
    sim.seconds = 10

    again = sim.submit()

    assert again.status_code == 202
    assert again.json() == first | {"status": "succeeded"}
    assert sim.status("sim_2").status_code == 404


def test_a_key_reused_for_another_order_is_a_conflict(sim: _Sim) -> None:
    sim.submit()

    response = sim.submit(order=ORDER | {"amount_minor": 62001})

    assert (response.status_code, response.json()) == (409, {"detail": "idempotency_key_reused"})


def test_an_unknown_reference_is_not_found(sim: _Sim) -> None:
    assert sim.status("sim_404").status_code == 404


@pytest.mark.parametrize(
    "order",
    [
        ORDER | {"customer_name": "Asha Rao"},
        ORDER | {"email": "asha@example.in"},
        ORDER | {"amount_minor": 0},
        ORDER | {"amount_minor": -5},
        ORDER | {"order_id": 0},
        ORDER | {"currency": "inr"},
        ORDER | {"currency": "RUPEES"},
        {"order_id": 4471},
        [],
    ],
)
def test_an_order_with_anything_extra_missing_or_out_of_range_is_refused(
    sim: _Sim, order: Any
) -> None:
    response = sim.submit(order=order)

    assert (response.status_code, response.json()) == (422, {"detail": "invalid_request"})
    assert sim.store.find("sim_1") is None


@pytest.mark.parametrize("key", [None, "", "has space", "x" * 65, "refund/1"])
def test_a_missing_or_malformed_idempotency_key_is_refused(sim: _Sim, key: str | None) -> None:
    assert sim.submit(key=key).status_code == 422
    assert sim.store.find("sim_1") is None


def test_a_forced_decline_is_ignored_unless_the_simulator_was_told_to_allow_it(sim: _Sim) -> None:
    sim.submit(**{"X-Sim-Forced-Outcome": "decline"})
    sim.seconds = DELAY_S

    assert sim.status("sim_1").json()["status"] == "succeeded"


def test_a_forced_decline_is_honoured_when_allowed(drill: _Sim) -> None:
    drill.submit(**{"X-Sim-Forced-Outcome": "decline"})
    drill.seconds = DELAY_S

    assert drill.status("sim_1").json() == {
        "provider_ref": "sim_1",
        "status": "declined",
        "reason_code": "provider_declined",
    }


def test_any_other_forced_outcome_means_nothing(drill: _Sim) -> None:
    drill.submit(**{"X-Sim-Forced-Outcome": "explode"})
    drill.seconds = DELAY_S

    assert drill.status("sim_1").json()["status"] == "succeeded"


def test_a_declined_refund_can_be_submitted_again_under_the_same_key(drill: _Sim) -> None:
    drill.submit(**{"X-Sim-Forced-Outcome": "decline"})
    drill.seconds = DELAY_S

    again = drill.submit()
    drill.seconds = 2 * DELAY_S

    assert again.json()["provider_ref"] == "sim_2"
    assert drill.status("sim_2").json()["status"] == "succeeded"
    assert drill.status("sim_1").json()["status"] == "declined"


@pytest.mark.parametrize("token", [None, "garbage", signed(worker_claims(), kid="stranger")])
def test_without_a_token_this_service_trusts_nothing_is_done(sim: _Sim, token: str | None) -> None:
    refused = sim.submit(token=token)

    assert (refused.status_code, refused.headers["WWW-Authenticate"]) == (401, "Bearer")
    assert sim.status("sim_1", token=token).status_code == 401
    assert sim.store.find("sim_1") is None


def test_a_bearer_header_with_no_token_is_refused(sim: _Sim) -> None:
    response = sim.http.post("/refunds", json=ORDER, headers={"Authorization": "Bearer "})

    assert response.status_code == 401


@pytest.mark.parametrize(
    "claims",
    [worker_claims(roles=["Support.L2"]), worker_claims(roles=None), worker_claims(scp="x")],
    ids=["an engineer", "no role", "a person"],
)
def test_a_trusted_caller_who_is_not_the_worker_is_forbidden(
    sim: _Sim, claims: dict[str, Any]
) -> None:
    token = signed(claims)

    assert sim.submit(token=token).status_code == 403
    assert sim.status("sim_1", token=token).status_code == 403
    assert sim.store.find("sim_1") is None


def test_when_the_signing_keys_cannot_be_had_the_service_says_unavailable(sim: _Sim) -> None:
    class NoKeys:
        async def verdict(self, _token: str) -> str:
            raise KeysUnavailableError

    sim.app.state.callers = NoKeys()

    assert sim.submit().status_code == 503


def test_the_health_probe_needs_no_token(sim: _Sim) -> None:
    response = sim.http.get("/healthz")

    assert (response.status_code, response.json()) == (200, {"status": "ok"})


def test_the_service_publishes_no_description_of_itself(sim: _Sim) -> None:
    assert [sim.http.get(path).status_code for path in ("/docs", "/redoc", "/openapi.json")] == [
        404,
        404,
        404,
    ]
