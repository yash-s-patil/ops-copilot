"""Both halves of the reference run, end to end against the real MCP application.

Only the model is a stand-in. The agent reaches the MCP application over its own client,
with exchanged tokens, and every rule the server has applies.
"""

import json
from typing import Any

from joserfc import jwt

from opscp_agent.events import types as events
from opscp_agent_testing.desk import AGENT_AUDIENCE, CANARY, bearer, desk, token_for
from opscp_agent_testing.reference import (
    FIRST_HALF,
    FRESH_TICKET,
    GOODWILL,
    ORDER,
    SECOND_HALF,
    TICKET,
)
from opscp_common.limits import Limits
from opscp_mcp_testing.identities import ARJUN, PRIYA, ROHAN, SAM
from opscp_mcp_testing.signin import AUDIENCE as MCP_AUDIENCE
from opscp_mcp_testing.signin import signing_key

INVESTIGATE = "Investigate this ticket."
SIX_READS = [
    "get_order",
    "lookup_customer",
    "list_tickets",
    "get_ticket",
    "search_runbooks",
    "get_policy_document",
]


def _in_run(at: Any, run_id: Any) -> list[tuple[str, str]]:
    return [
        (entry.action, entry.target) for entry in at.audit.entries if entry.run_id == str(run_id)
    ]


async def test_an_l1_engineer_investigates_and_escalates_with_a_recommendation(
    limits: Limits,
) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, INVESTIGATE, TICKET)
        await at.settled(run_id, "awaiting_approval")

        loaded = at.store.payloads(run_id, events.CONTEXT_LOADED)[0]
        assert "issue_refund" not in loaded["tools"]
        assert len(loaded["tools"]) == 7
        started = at.store.payloads(run_id, events.TOOL_STARTED)
        assert [step["tool"] for step in started] == [*SIX_READS, "update_ticket"]
        (card,) = at.store.payloads(run_id, events.APPROVAL_REQUIRED)
        assert card["tool"] == "update_ticket"
        assert card["approval_kind"] == "self"
        assert card["args"]["escalate_to"] == "L2"
        assert card["args"]["recommendation"]["amount_minor"] == GOODWILL
        assert {cited["tool"] for cited in card["evidence"]} >= {"get_order", "lookup_customer"}
        assert at.written.tickets == {}

        await at.decide(PRIYA, run_id)
        assert await at.settled(run_id, "completed") == "completed"

        result = at.store.runs[run_id].result
        assert result is not None
        assert result["escalated"] is True
        assert result["customer_reply_draft"] is None
        assert [action["status"] for action in result["proposed_actions"]] == ["executed"]
        assert at.written.tickets[TICKET].queue == "L2"
        assert at.written.escalations[TICKET]["by_name"] == PRIYA.name
        assert _in_run(at, run_id)[-4:] == [
            ("propose", "update_ticket"),
            ("approve", "POST /approvals/{id}/approve"),
            ("call", "POST /approvals/{id}/token"),
            ("execute", "update_ticket"),
        ]
        assert at.store.kinds(run_id)[-1] == events.RUN_COMPLETED


async def test_an_l2_engineer_refunds_and_notes_the_ticket_with_one_decision(
    limits: Limits,
) -> None:
    async with desk(limits, SECOND_HALF) as at:
        run_id = await at.start(ARJUN, INVESTIGATE, TICKET)
        await at.settled(run_id, "awaiting_approval")

        cards = at.store.payloads(run_id, events.APPROVAL_REQUIRED)
        assert [card["tool"] for card in cards] == ["issue_refund", "update_ticket"]
        assert cards[0]["args"]["amount_minor"] == GOODWILL
        assert at.written.refunds == []

        await at.decide(ARJUN, run_id)
        await at.settled(run_id, "completed")

        (refund,) = at.written.refunds
        assert (refund.order_id, refund.amount_minor, refund.status) == (
            ORDER,
            GOODWILL,
            "approved",
        )
        assert len(at.written.outbox) == 1
        assert len(at.written.notes) == 1
        result = at.store.runs[run_id].result
        assert result is not None
        assert result["escalated"] is False
        assert f"order {ORDER}" in result["customer_reply_draft"]
        assert [action["status"] for action in result["proposed_actions"]] == ["executed"] * 2
        executions = [step for step in _in_run(at, run_id) if step[0] == "execute"]
        assert executions == [("execute", "issue_refund"), ("execute", "update_ticket")]


async def test_the_counts_of_the_first_half(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, INVESTIGATE, TICKET)
        await at.settled(run_id, "awaiting_approval")
        await at.decide(PRIYA, run_id)
        await at.settled(run_id, "completed")

        phases = [step["phase"] for step in at.store.payloads(run_id, events.TOOL_FINISHED)]
        assert (phases.count("read"), phases.count("propose"), phases.count("execute")) == (6, 1, 1)
        assert len(at.store.payloads(run_id, events.APPROVAL_RESOLVED)) == 1
        run = at.store.runs[run_id]
        assert (run.tool_calls, run.model_turns) == (8, 5)
        assert run.tokens_in > 0


async def test_an_order_in_another_region_cannot_be_found(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(ROHAN, f"What is wrong with order {ORDER}?")
        await at.settled(run_id, "completed")

        result = at.store.runs[run_id].result
        assert result is not None
        assert result["root_cause"] == f"I cannot find order {ORDER}."
        assert result["proposed_actions"] == []
        assert result["customer_reply_draft"] is None
        (step,) = at.store.payloads(run_id, events.TOOL_FINISHED)
        assert (step["status"], step["error_code"]) == ("error", "NOT_FOUND")


async def test_a_run_cannot_be_started_on_a_ticket_the_person_cannot_see(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        response = await at.agent.post(
            "/api/runs", json={"message": INVESTIGATE, "ticket_id": TICKET}, headers=bearer(ROHAN)
        )

        assert response.status_code == 404
        assert response.json()["code"] == "NOT_FOUND"
        assert at.store.runs == {}


async def test_an_order_that_is_not_delayed_gets_no_proposal(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, INVESTIGATE, FRESH_TICKET)
        await at.settled(run_id, "completed")

        result = at.store.runs[run_id].result
        assert result is not None
        assert result["proposed_actions"] == []
        assert at.store.payloads(run_id, events.APPROVAL_REQUIRED) == []


async def test_a_question_that_names_no_order_is_answered_without_any_call(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, "Hello, can you help?")
        await at.settled(run_id, "completed")

        assert at.store.payloads(run_id, events.TOOL_STARTED) == []
        result = at.store.runs[run_id].result
        assert result is not None
        assert "which order" in result["root_cause"]


async def test_a_person_with_no_data_role_is_shown_no_tools(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(SAM, f"What is wrong with order {ORDER}?")
        await at.settled(run_id, "completed")

        assert at.store.payloads(run_id, events.CONTEXT_LOADED)[0]["tools"] == []
        assert at.store.payloads(run_id, events.TOOL_STARTED) == []


async def test_the_person_s_own_token_never_reaches_the_mcp_server(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, INVESTIGATE, TICKET)
        await at.settled(run_id, "awaiting_approval")

        assert at.mcp_requests
        for request in at.mcp_requests:
            sent = request.headers["authorization"].removeprefix("Bearer ")
            claims = jwt.decode(sent, signing_key()).claims
            assert claims["aud"] == MCP_AUDIENCE != AGENT_AUDIENCE
            assert claims["oid"] == str(PRIYA.oid)
        assert at.issuer.exchanges == 1


async def test_no_token_and_no_canary_is_saved_with_a_run(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, INVESTIGATE, TICKET)
        await at.settled(run_id, "awaiting_approval")
        await at.decide(PRIYA, run_id)
        await at.settled(run_id, "completed")

        saved = json.dumps(await at.manager.saved_state(run_id), default=str)
        stored = json.dumps([event.payload for _key, event in at.store.events[run_id]], default=str)
        person_token = token_for(PRIYA)
        for kept in (saved, stored):
            assert CANARY not in kept
            assert "eyJ" not in kept
            assert person_token.split(".")[0] not in kept


async def test_the_second_half_asks_the_server_no_more_often_than_it_must(limits: Limits) -> None:
    async with desk(limits, SECOND_HALF) as at:
        run_id = await at.start(ARJUN, INVESTIGATE, TICKET)
        await at.settled(run_id, "awaiting_approval")
        await at.decide(ARJUN, run_id)
        await at.settled(run_id, "completed")
        for _ in range(5):
            await at.agent.get(f"/api/runs/{run_id}", headers=bearer(ARJUN))

        # One read to start, six reads, two requests, and for each change one approval,
        # one token and one execution. Every one counts against the person's rate limit.
        counted = [entry.target for entry in at.audit.entries]
        assert len(counted) == 1 + 6 + 2 + 3 * 2
        assert len(counted) < limits.rate.burst
