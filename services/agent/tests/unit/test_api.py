"""The HTTP routes: who may call them and what each answers."""

import asyncio
import time
from typing import Any
from uuid import UUID, uuid4

import httpx2
import pytest
from joserfc.jwk import RSAKey

from opscp_agent.auth.verifier import token_verifier
from opscp_agent.events import types as events
from opscp_agent.runs.store import Run
from opscp_agent.settings import EntraSignIn
from opscp_agent_testing.desk import AGENT_AUDIENCE, AGENT_SCOPE, Desk, bearer, desk
from opscp_agent_testing.memory_store import InMemoryRunStore
from opscp_agent_testing.models import Told, reads, says
from opscp_agent_testing.reference import (
    FIRST_HALF,
    FRESH_ORDER,
    FRESH_TICKET,
    ORDER,
    SECOND_HALF,
    SOUTH_TICKET,
    TICKET,
)
from opscp_common.errors import BackendUnavailableError, RateLimitedError
from opscp_common.limits import Limits
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA, ROHAN, SAM
from opscp_mcp_testing.signin import claims_of, signed, token_for

ASK = f"What is wrong with order {FRESH_ORDER}?"
CLICK = {"Idempotency-Key": "confirm-resolve-click-one"}
PROTECTED = [
    ("GET", "/api/me"),
    ("GET", "/api/tickets"),
    ("GET", f"/api/tickets/{TICKET}"),
    ("POST", f"/api/tickets/{TICKET}/resolve"),
    ("POST", "/api/refunds/1/retry"),
    ("POST", "/api/runs"),
    ("GET", "/api/runs"),
    ("GET", f"/api/runs/{uuid4()}"),
    ("GET", f"/api/runs/{uuid4()}/events"),
    ("POST", f"/api/runs/{uuid4()}/cancel"),
    ("POST", f"/api/approvals/{uuid4()}/execute"),
    ("GET", "/api/admin/runs"),
    ("GET", "/api/admin/runs/latest/events"),
]


def _other_key() -> RSAKey:
    return RSAKey.generate_key(2048, private=True)


@pytest.mark.parametrize(("method", "path"), PROTECTED)
async def test_every_route_but_the_probes_needs_a_token(
    limits: Limits, method: str, path: str
) -> None:
    async with desk(limits, FIRST_HALF) as at:
        response = await at.agent.request(method, path)

        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.content == b""


@pytest.mark.parametrize(
    "token",
    [
        "not-a-token",
        token_for(PRIYA),
        signed(claims_of(PRIYA, aud=AGENT_AUDIENCE, scp="Mcp.Access")),
        signed(claims_of(PRIYA, aud=AGENT_AUDIENCE, scp=AGENT_SCOPE, exp=int(time.time()) - 300)),
        signed(claims_of(PRIYA, aud=AGENT_AUDIENCE, scp=AGENT_SCOPE, exp=None)),
        signed(claims_of(PRIYA, aud=AGENT_AUDIENCE, scp=AGENT_SCOPE, oid=None)),
        signed(claims_of(PRIYA, aud=AGENT_AUDIENCE, scp=AGENT_SCOPE, iss="http://elsewhere/")),
        signed(claims_of(PRIYA, aud=AGENT_AUDIENCE, scp=AGENT_SCOPE), _other_key()),
    ],
    ids=[
        "garbage",
        "meant for the MCP server",
        "without this service's scope",
        "expired",
        "never expires",
        "names nobody",
        "another issuer",
        "another key",
    ],
)
async def test_a_token_this_service_does_not_accept_is_refused(limits: Limits, token: str) -> None:
    async with desk(limits, FIRST_HALF) as at:
        response = await at.agent.get("/api/me", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 401
        assert at.mcp_requests == []


async def test_the_probes_need_no_token(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        assert (await at.agent.get("/healthz")).json() == {"status": "ok"}
        assert (await at.agent.get("/readyz")).json() == {"status": "ok"}


async def test_a_request_from_a_web_page_is_refused(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        response = await at.agent.get(
            "/api/me", headers=bearer(PRIYA) | {"Origin": "https://evil.example"}
        )

        assert response.status_code == 403


async def test_a_body_larger_than_any_route_needs_is_refused_unread(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        response = await at.agent.post(
            "/api/runs",
            json={"message": "x" * (limits.runs.body_bytes_max + 1)},
            headers=bearer(PRIYA),
        )

        assert response.status_code == 413
        assert at.store.runs == {}


async def test_the_service_publishes_no_description_of_itself(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await at.agent.get(path, headers=bearer(PRIYA))).status_code == 404


@pytest.mark.parametrize(
    ("who", "permission", "held"),
    [
        (PRIYA, "refunds.request", False),
        (PRIYA, "tickets.write", True),
        (ARJUN, "refunds.request", True),
        (ARJUN, "pii.read", True),
        (KAVITA, "refunds.approve", True),
        (SAM, "orders.read", False),
        (SAM, "admin.read", True),
    ],
)
async def test_me_reports_the_permissions_the_mcp_server_grants(
    limits: Limits, who: Any, permission: str, held: bool
) -> None:
    async with desk(limits, FIRST_HALF) as at:
        me = (await at.agent.get("/api/me", headers=bearer(who))).json()

        assert me["name"] == who.name
        assert (permission in me["permissions"]) is held
        assert me["demo_tools"] is False


async def test_demo_tools_follow_the_demonstration_switch(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF, demo_tools=True) as at:
        me = (await at.agent.get("/api/me", headers=bearer(PRIYA))).json()

        assert me["demo_tools"] is True


async def test_the_inbox_is_one_tool_call_with_the_caller_s_own_view(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        mine = (await at.agent.get("/api/tickets", headers=bearer(PRIYA))).json()
        south = (await at.agent.get("/api/tickets?view=all_open", headers=bearer(ROHAN))).json()

        assert {item["ticket_id"] for item in mine["items"]} == {TICKET, FRESH_TICKET}
        assert {item["ticket_id"] for item in south["items"]} == {SOUTH_TICKET}
        assert set(mine["counts"]) == {"my_queue", "all_open", "waiting", "done"}
        listed = [entry for entry in at.audit.entries if entry.target == "list_tickets"]
        assert len(listed) == 2
        assert all(entry.run_id is None for entry in listed)
        assert listed[0].args_redacted["page_size"] == limits.inbox.page_size


async def test_an_unknown_view_or_an_overlong_cursor_is_refused(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        for query in ("view=everything", "cursor=" + "a" * 401):
            response = await at.agent.get(f"/api/tickets?{query}", headers=bearer(PRIYA))
            assert response.status_code == 422
            assert response.json()["code"] == "VALIDATION_FAILED"
        tampered = await at.agent.get("/api/tickets?cursor=abc", headers=bearer(PRIYA))
        assert tampered.status_code == 422


async def test_the_workspace_is_composed_from_the_caller_s_own_reads(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        seen = (await at.agent.get(f"/api/tickets/{TICKET}", headers=bearer(PRIYA))).json()
        seen_by_l2 = (await at.agent.get(f"/api/tickets/{TICKET}", headers=bearer(ARJUN))).json()

        assert seen["ticket"]["ticket_id"] == TICKET
        assert len(seen["ticket"]["notes"]) == 2
        assert seen["order"]["order_id"] == ORDER
        assert seen["customer"]["contact_masked"] is True
        assert "ananya.k@example.in" not in str(seen)
        assert seen_by_l2["customer"]["email"] == "ananya.k@example.in"
        assert seen["approvals"] == []
        assert seen["investigations"] == []


async def test_a_ticket_in_another_region_does_not_exist_and_shows_no_investigation(
    limits: Limits,
) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, "Investigate.", FRESH_TICKET)
        await at.settled(run_id, "completed")

        for query in ("", "?part=status"):
            response = await at.agent.get(
                f"/api/tickets/{FRESH_TICKET}{query}", headers=bearer(ROHAN)
            )
            assert response.status_code == 404
            assert response.json()["code"] == "NOT_FOUND"
            assert "investigations" not in response.text


async def test_another_engineer_sees_a_summary_of_an_investigation_and_not_its_text(
    limits: Limits,
) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, "Investigate this please.", FRESH_TICKET)
        await at.settled(run_id, "completed")

        own = (await at.agent.get(f"/api/tickets/{FRESH_TICKET}", headers=bearer(PRIYA))).json()
        other = (await at.agent.get(f"/api/tickets/{FRESH_TICKET}", headers=bearer(ARJUN))).json()

        (mine,) = own["investigations"]
        (theirs,) = other["investigations"]
        assert (mine["mine"], theirs["mine"]) == (True, False)
        assert theirs["by_name"] == PRIYA.name
        assert theirs["root_cause"].startswith(f"Order {FRESH_ORDER}")
        assert set(theirs) == {
            "run_id",
            "by_name",
            "mine",
            "status",
            "started_at",
            "finished_at",
            "root_cause",
            "actions",
        }
        hidden = await at.agent.get(f"/api/runs/{run_id}", headers=bearer(ARJUN))
        assert hidden.status_code == 404


async def test_the_status_part_is_one_small_read(limits: Limits) -> None:
    async with desk(limits, SECOND_HALF) as at:
        response = await at.agent.get(f"/api/tickets/{TICKET}?part=status", headers=bearer(ARJUN))

        assert response.json() == {
            "ticket_id": TICKET,
            "status": "open",
            "queue": "L2",
            "refund_state": None,
            "version": 2,
        }
        (read,) = at.audit.entries
        assert (read.target, read.args_redacted["notes"]) == ("get_ticket", 0)


async def test_resolve_is_recorded_asked_approved_and_carried_out(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        response = await at.agent.post(
            f"/api/tickets/{TICKET}/resolve", headers=bearer(PRIYA) | CLICK
        )

        assert response.status_code == 200
        assert response.json() == {"ticket_id": TICKET, "status": "resolved", "version": 2}
        assert [(entry.action, entry.outcome) for entry in at.audit.entries] == [
            ("propose", "ok"),
            ("approve", "ok"),
            ("execute", "ok"),
        ]
        assert {entry.actor_oid for entry in at.audit.entries} == {PRIYA.oid}
        assert at.written.tickets[TICKET].status == "resolved"


async def test_resolve_asked_again_for_the_same_click_changes_nothing_more(
    limits: Limits,
) -> None:
    async with desk(limits, FIRST_HALF) as at:
        first = await at.agent.post(f"/api/tickets/{TICKET}/resolve", headers=bearer(PRIYA) | CLICK)
        again = await at.agent.post(f"/api/tickets/{TICKET}/resolve", headers=bearer(PRIYA) | CLICK)

        assert again.json() == first.json()
        assert [entry.action for entry in at.audit.entries].count("approve") == 1
        assert at.written.tickets[TICKET].version == 2


async def test_resolve_goes_on_from_where_an_interrupted_attempt_stopped(limits: Limits) -> None:
    lost = {"n": 0}

    def execution_lost_once(request: httpx2.Request) -> httpx2.Response | None:
        body = request.content.decode()
        if "approval_token" in body and not lost["n"]:
            lost["n"] += 1
            raise httpx2.ReadTimeout("no answer", request=request)
        return None

    async with desk(limits, FIRST_HALF, mcp_handler=execution_lost_once) as at:
        first = await at.agent.post(f"/api/tickets/{TICKET}/resolve", headers=bearer(PRIYA) | CLICK)
        again = await at.agent.post(f"/api/tickets/{TICKET}/resolve", headers=bearer(PRIYA) | CLICK)

        assert first.status_code == 503
        assert again.json() == {"ticket_id": TICKET, "status": "resolved", "version": 2}
        done = [entry.action for entry in at.audit.entries if entry.outcome == "ok"]
        assert done == ["propose", "approve", "propose", "call", "execute"]
        keys = {
            str(entry.args_redacted.get("idempotency_key"))
            for entry in at.audit.entries
            if entry.target == "update_ticket"
        }
        assert keys == {"resolve-confirm-resolve-click-one"}
        assert at.written.tickets[TICKET].version == 2


@pytest.mark.parametrize("click", [None, "", "short", "has space in it", "x" * 57])
async def test_resolve_needs_a_key_that_names_the_click(limits: Limits, click: str | None) -> None:
    async with desk(limits, FIRST_HALF) as at:
        headers = bearer(PRIYA) | ({} if click is None else {"Idempotency-Key": click})
        response = await at.agent.post(f"/api/tickets/{TICKET}/resolve", headers=headers)

        assert response.status_code == 422
        assert at.audit.entries == []


async def test_resolve_is_refused_to_someone_who_cannot_see_the_ticket(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        for who, status in ((ROHAN, 404), (SAM, 403)):
            response = await at.agent.post(
                f"/api/tickets/{TICKET}/resolve", headers=bearer(who) | CLICK
            )
            assert response.status_code == status
        assert at.written.tickets == {}


async def test_retry_payment_is_passed_to_the_server_which_decides(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        missing = await at.agent.post("/api/refunds/424242/retry", headers=bearer(ARJUN))
        not_allowed = await at.agent.post("/api/refunds/424242/retry", headers=bearer(PRIYA))

        assert (missing.status_code, missing.json()["code"]) == (404, "NOT_FOUND")
        assert (not_allowed.status_code, not_allowed.json()["code"]) == (403, "PERMISSION_DENIED")
        assert [entry.action for entry in at.audit.entries] == ["retry", "deny"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"message": ""},
        {"message": "​‮ \n"},
        {"message": "x" * 2001},
        {"message": "ok", "ticket_id": 0},
        {"message": "ok", "ticket_id": "2207"},
        {"message": "ok", "role": "Support.L2"},
        [],
    ],
)
async def test_a_run_cannot_be_started_with_a_malformed_request(limits: Limits, body: Any) -> None:
    async with desk(limits, FIRST_HALF) as at:
        response = await at.agent.post("/api/runs", json=body, headers=bearer(PRIYA))

        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_FAILED"
        assert at.store.runs == {}


async def test_invisible_characters_are_removed_from_a_message_before_it_is_kept(
    limits: Limits,
) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, "Order​ 4490‮ is late\x07. क्‍ष")
        await at.settled(run_id, "completed")

        assert at.store.runs[run_id].message == "Order 4490 is late. क्‍ष"


async def test_a_person_lists_their_own_runs_and_can_ask_for_those_without_a_ticket(
    limits: Limits,
) -> None:
    async with desk(limits, FIRST_HALF) as at:
        on_ticket = await at.start(PRIYA, "Investigate.", FRESH_TICKET)
        asked = await at.start(PRIYA, ASK)
        others = await at.start(ARJUN, ASK)
        for run_id in (on_ticket, asked, others):
            await at.settled(run_id, "completed")

        every = (await at.agent.get("/api/runs", headers=bearer(PRIYA))).json()["items"]
        ask_only = (await at.agent.get("/api/runs?ask=true", headers=bearer(PRIYA))).json()["items"]

        assert [run["run_id"] for run in every] == [str(asked), str(on_ticket)]
        assert [run["run_id"] for run in ask_only] == [str(asked)]
        assert every[0]["result"]["root_cause"].startswith(f"Order {FRESH_ORDER}")


async def test_a_run_is_not_found_for_anyone_but_its_owner(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, ASK)
        await at.settled(run_id, "completed")

        for path in ("", "/events"):
            assert (
                await at.agent.get(f"/api/runs/{run_id}{path}", headers=bearer(ARJUN))
            ).status_code == 404
        cancelled = await at.agent.post(f"/api/runs/{run_id}/cancel", headers=bearer(SAM))
        assert cancelled.status_code == 404
        assert (
            await at.agent.get("/api/runs/not-a-uuid", headers=bearer(PRIYA))
        ).status_code == 422


async def _finished_run(at: Desk) -> Any:
    run_id = await at.start(PRIYA, "Investigate.", TICKET)
    await at.settled(run_id, "awaiting_approval")
    await at.decide(PRIYA, run_id)
    await at.settled(run_id, "completed")
    return run_id


async def test_admin_routes_are_for_the_administrator_only(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        for who in (PRIYA, ARJUN, KAVITA):
            for path in ("/api/admin/runs", f"/api/admin/runs/{uuid4()}"):
                response = await at.agent.get(path, headers=bearer(who))
                assert response.status_code == 403
                assert response.json()["code"] == "PERMISSION_DENIED"
        assert (await at.agent.get("/api/admin/runs", headers=bearer(SAM))).status_code == 200
        missing = await at.agent.get(f"/api/admin/runs/{uuid4()}", headers=bearer(SAM))
        assert missing.status_code == 404


async def test_an_administrator_sees_how_a_run_went_and_nothing_it_was_about(
    limits: Limits,
) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await _finished_run(at)

        listed = (await at.agent.get("/api/admin/runs", headers=bearer(SAM))).json()
        detail = await at.agent.get(f"/api/admin/runs/{run_id}", headers=bearer(SAM))

        (run,) = listed["items"]
        assert (run["run_id"], run["status"], run["tool_calls"]) == (str(run_id), "completed", 8)
        assert "message" not in run
        assert "result" not in run
        shown = detail.text
        for hidden in ("Ananya", "Investigate.", "postcode", "Packed for", "Copilot investigation"):
            assert hidden not in shown
        kinds = [event["type"] for event in detail.json()["events"]]
        assert kinds == at.store.kinds(run_id)
        steps = [e["payload"] for e in detail.json()["events"] if e["type"] == events.TOOL_FINISHED]
        assert steps[0]["tool"] == "get_order"
        assert "summary" not in steps[0]


async def test_one_call_of_a_run_is_shown_only_where_the_demonstration_switch_exists(
    limits: Limits,
) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, ASK)
        await at.settled(run_id, "completed")
        hidden = await at.agent.get(f"/api/runs/{run_id}/calls/call-1-1", headers=bearer(PRIYA))
        assert hidden.status_code == 404

    async with desk(limits, FIRST_HALF, demo_tools=True) as at:
        run_id = await at.start(PRIYA, ASK)
        await at.settled(run_id, "completed")

        shown = await at.agent.get(f"/api/runs/{run_id}/calls/call-1-1", headers=bearer(PRIYA))
        other = await at.agent.get(f"/api/runs/{run_id}/calls/call-1-1", headers=bearer(ARJUN))
        missing = await at.agent.get(f"/api/runs/{run_id}/calls/nope", headers=bearer(PRIYA))

        assert shown.json()["tool"] == "get_order"
        assert shown.json()["args"] == {"order_id": FRESH_ORDER}
        assert shown.json()["result"].startswith("<<tool_result")
        assert (other.status_code, missing.status_code) == (404, 404)


def _identity_answered(answer: httpx2.Response) -> Any:
    def handler(request: httpx2.Request) -> httpx2.Response | None:
        return answer if request.url.path == "/identity" else None

    return handler


async def test_the_server_s_rate_limit_is_passed_on_with_how_long_to_wait(limits: Limits) -> None:
    limited = httpx2.Response(
        429,
        json=RateLimitedError("Too many requests.", retry_after_seconds=7).body().model_dump(),
    )
    async with desk(limits, FIRST_HALF, mcp_handler=_identity_answered(limited)) as at:
        response = await at.agent.get("/api/me", headers=bearer(PRIYA))

        assert (response.status_code, response.headers["Retry-After"]) == (429, "7")
        assert response.json()["code"] == "RATE_LIMITED"


async def test_a_token_the_mcp_server_keeps_refusing_asks_the_person_to_sign_in(
    limits: Limits,
) -> None:
    refused = httpx2.Response(401, headers={"WWW-Authenticate": "Bearer"})
    async with desk(limits, FIRST_HALF, mcp_handler=_identity_answered(refused)) as at:
        response = await at.agent.get("/api/me", headers=bearer(PRIYA))

        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert at.issuer.exchanges == 2


async def test_when_the_mcp_server_is_away_a_screen_is_told_so_and_nothing_else(
    limits: Limits,
) -> None:
    def away(request: httpx2.Request) -> httpx2.Response | None:
        raise httpx2.ConnectError("connection refused by mcp.internal:8000", request=request)

    async with desk(limits, FIRST_HALF, mcp_handler=away) as at:
        response = await at.agent.get("/api/tickets", headers=bearer(PRIYA))

        assert (response.status_code, response.json()["code"]) == (503, "BACKEND_UNAVAILABLE")
        assert "mcp.internal" not in response.text


async def test_an_answer_the_agent_cannot_read_is_a_defect_reported_without_its_text(
    limits: Limits,
) -> None:
    garbled = httpx2.Response(200, text="<html>ananya.k@example.in</html>")
    async with desk(limits, FIRST_HALF, mcp_handler=_identity_answered(garbled)) as at:
        response = await at.agent.get("/api/me", headers=bearer(PRIYA))

        assert (response.status_code, response.json()["code"]) == (500, "INTERNAL")
        assert "ananya" not in response.text


class _SlowStore(InMemoryRunStore):
    async def recent_for(self, user_oid: UUID, *, ask_only: bool, limit: int) -> list[Run]:
        await asyncio.sleep(30)
        return []


async def test_a_route_that_takes_too_long_is_answered_and_not_left_hanging(
    limits: Limits,
) -> None:
    async with desk(limits, FIRST_HALF, store=_SlowStore(), route_timeout_s=1) as at:
        response = await at.agent.get("/api/runs", headers=bearer(PRIYA))

        assert (response.status_code, response.json()["code"]) == (503, "BACKEND_UNAVAILABLE")


def test_in_azure_tokens_are_checked_against_entra_s_published_keys() -> None:
    sign_in = EntraSignIn(
        tenant_id=UUID("7f1c2d3e-0000-4000-8000-0000000000bb"),
        client_id=UUID("7f1c2d3e-0000-4000-8000-0000000000cc"),
        audience=AGENT_AUDIENCE,
    )

    verifier = token_verifier(sign_in)

    assert (
        verifier.issuer
        == "https://login.microsoftonline.com/7f1c2d3e-0000-4000-8000-0000000000bb/v2.0"
    )
    assert verifier.jwks_uri == (
        "https://login.microsoftonline.com/7f1c2d3e-0000-4000-8000-0000000000bb/discovery/v2.0/keys"
    )
    assert set(verifier.audience or ()) == {"7f1c2d3e-0000-4000-8000-0000000000cc", AGENT_AUDIENCE}
    assert verifier.required_scopes == [AGENT_SCOPE]
    assert verifier.algorithm == "RS256"


async def test_a_stream_reopened_after_the_last_event_ends_at_once(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, ASK)
        await at.settled(run_id, "completed")
        last = len(at.store.events[run_id])

        async with asyncio.timeout(5):
            response = await at.agent.get(
                f"/api/runs/{run_id}/events",
                headers=bearer(PRIYA) | {"Last-Event-ID": str(last)},
            )

        assert (response.status_code, response.text) == (200, "")


async def test_a_run_is_not_recorded_when_its_steps_could_not_be_saved(limits: Limits) -> None:
    async def not_ready() -> None:
        raise BackendUnavailableError("The assistant is starting up. Try again shortly.")

    async with desk(limits, FIRST_HALF, storage_ready=not_ready) as at:
        response = await at.agent.post("/api/runs", json={"message": ASK}, headers=bearer(PRIYA))

        assert (response.status_code, response.json()["code"]) == (503, "BACKEND_UNAVAILABLE")
        assert at.store.runs == {}
        assert at.audit.entries == []


async def test_another_engineer_never_sees_contact_details_a_run_wrote_down(
    limits: Limits,
) -> None:
    wrote = says(
        "The customer ananya.k@example.in (+91 58200 11471) was not told.",
        caveats=("Call 9876543210 first.",),
    )
    model = Told([reads(("lookup_customer", {"customer_id": 3001})), wrote, says("Hello")])
    async with desk(limits, FIRST_HALF, model=model) as at:
        run_id = await at.start(ARJUN, "Investigate.", FRESH_TICKET)
        await at.settled(run_id, "completed")

        seen_by_l1 = await at.agent.get(f"/api/tickets/{FRESH_TICKET}", headers=bearer(PRIYA))
        own = await at.agent.get(f"/api/runs/{run_id}", headers=bearer(ARJUN))

        for shown in (seen_by_l1.text, own.text):
            assert "ananya.k@example.in" not in shown
            assert "58200" not in shown
            assert "9876543210" not in shown
        cause = seen_by_l1.json()["investigations"][0]["root_cause"]
        assert cause.startswith("The customer <email:")
        assert "<phone:" in own.json()["result"]["caveats"][0]
