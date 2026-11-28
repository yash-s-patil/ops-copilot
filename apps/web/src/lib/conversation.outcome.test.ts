import { describe, expect, it } from "vitest";
import { turnOf } from "@/lib/conversation";
import type { RunEvent } from "@/lib/events";
import { blockOf, blocksOf, kinds } from "@/test/blocks";
import {
  ACTION_ID,
  ARJUN,
  OTHER_ACTION_ID,
  plainSpaces,
  REFUND_MINOR,
  REFUND_WORDS,
} from "@/test/fixtures";
import {
  asked,
  decided,
  EXECUTE_REFUND,
  finished,
  NOTE_ARGS,
  numbered,
  PROPOSE_NOTE,
  PROPOSE_REFUND,
  ran,
  READ_ORDER,
  READ_TICKET,
  said,
  started,
  type Draft,
} from "@/test/run-events";

const ONE_CHANGE: Draft[] = [...ran(READ_ORDER), ...ran(PROPOSE_REFUND), asked()];
const REFUNDED: Draft[] = [...ONE_CHANGE, decided("approved", ARJUN.name), ...ran(EXECUTE_REFUND)];

const refundOutcome = (status: string, over: Record<string, unknown> = {}) => ({
  call_id: PROPOSE_REFUND.id,
  tool: "issue_refund",
  status,
  action_id: ACTION_ID,
  ...over,
});

const noteOutcome = (status: string, over: Record<string, unknown> = {}) => ({
  call_id: PROPOSE_NOTE.id,
  tool: "update_ticket",
  status,
  action_id: OTHER_ACTION_ID,
  ...over,
});

const completed = (result: Record<string, unknown> = {}): Draft => ["run.completed", result];

describe("a completed run", () => {
  it("ends with a summary and no reply when the assistant only looked", () => {
    const turn = turnOf(
      numbered([...ran(READ_ORDER), completed({ root_cause: "The parcel is in transit." })]),
    );
    expect(kinds(turn)).toEqual(["steps", "summary"]);
    expect(turn.status).toBe("completed");
    expect(blockOf(turn, "steps").live).toBe(false);
    expect(blockOf(turn, "summary")).toMatchObject({
      cause: "The parcel is in transit.",
      done: [],
      notes: [],
    });
  });

  it("shows the refund block, then the summary, then the reply draft when a refund was made", () => {
    const result = {
      root_cause: "The carrier lost the parcel.",
      proposed_actions: [refundOutcome("executed", { result: { amount_minor: REFUND_MINOR } })],
      customer_reply_draft: "We have refunded your order.",
    };
    const turn = turnOf(numbered([...REFUNDED, completed(result)]));
    expect(kinds(turn).slice(-3)).toEqual(["refund", "summary", "reply"]);
    expect(blockOf(turn, "summary").done.map(plainSpaces)).toEqual([
      `Refund of ${REFUND_WORDS} approved and handed to payments`,
    ]);
    expect(blockOf(turn, "reply").draft).toBe("We have refunded your order.");
  });

  it("marks the row of a change that was made as done", () => {
    const result = { proposed_actions: [refundOutcome("executed")] };
    const turn = turnOf(numbered([...REFUNDED, completed(result)]));
    expect(blockOf(turn, "approval").actions[0]?.status).toBe("done");
  });

  it("has no refund block when the refund could not be recorded", () => {
    const events = [
      ...ONE_CHANGE,
      decided("approved", ARJUN.name),
      started(EXECUTE_REFUND),
      finished(EXECUTE_REFUND, { status: "error", error_code: "BACKEND_UNAVAILABLE" }),
      completed({ proposed_actions: [refundOutcome("failed", { detail: "Refunds were down." })] }),
    ];
    const turn = turnOf(numbered(events));
    expect(blocksOf(turn, "refund")).toEqual([]);
    expect(blockOf(turn, "approval").actions[0]?.status).toBe("failed");
    expect(blockOf(turn, "summary")).toMatchObject({ done: [], notes: ["Refunds were down."] });
  });
});

describe("the summary of a completed run", () => {
  it("has no refund block when the only change made was a note", () => {
    const result = { proposed_actions: [noteOutcome("executed")] };
    const turn = turnOf(numbered([...ran(READ_ORDER), completed(result)]));
    expect(blocksOf(turn, "refund")).toEqual([]);
    expect(blockOf(turn, "summary").done).toEqual(["Note added to the ticket"]);
  });

  it("says a note escalated the ticket when it did", () => {
    const result = { proposed_actions: [noteOutcome("executed", { escalates: true })] };
    const turn = turnOf(numbered([completed(result)]));
    expect(blockOf(turn, "summary").done).toEqual(["Note added and ticket escalated to L2"]);
  });

  it("says a refund was made without an amount when the result names none", () => {
    const result = { proposed_actions: [refundOutcome("executed")] };
    const turn = turnOf(numbered([completed(result)]));
    expect(blockOf(turn, "summary").done).toEqual(["Refund approved and handed to payments"]);
  });

  it("puts caveats first in the notes, then why a change was not made, but not a rejection", () => {
    const result = {
      caveats: ["The carrier has not confirmed in writing."],
      proposed_actions: [
        refundOutcome("rejected", { detail: "Rejected by the engineer." }),
        noteOutcome("expired", { detail: "The note was not approved in time." }),
      ],
    };
    const turn = turnOf(numbered([completed(result)]));
    expect(blockOf(turn, "summary").notes).toEqual([
      "The carrier has not confirmed in writing.",
      "The note was not approved in time.",
    ]);
  });

  it("closes every card and leaves a row alone when its outcome is one it does not know", () => {
    const result = { proposed_actions: [refundOutcome("awaiting_supervisor")] };
    const turn = turnOf(
      numbered([...ran(PROPOSE_REFUND), asked({ kind: "supervisor" }), completed(result)]),
    );
    expect(blockOf(turn, "approval")).toMatchObject({ open: false });
    expect(blockOf(turn, "approval").actions[0]?.status).toBe("waiting");
    expect(turn.status).toBe("completed");
  });

  it("copes with a result that says nothing at all", () => {
    const turn = turnOf(numbered([completed()]));
    expect(kinds(turn)).toEqual(["summary"]);
    expect(blockOf(turn, "summary")).toMatchObject({ cause: "", done: [], notes: [] });
  });

  it("shows no reply block for an empty draft", () => {
    const turn = turnOf(numbered([completed({ customer_reply_draft: "" })]));
    expect(blocksOf(turn, "reply")).toEqual([]);
  });
});

describe("a run that did not complete", () => {
  it("says the engineer stopped it when they did, whatever reason came with it", () => {
    const turn = turnOf(
      numbered([
        ...ONE_CHANGE,
        ["run.stopped", { code: "cancelled", reason: "cancelled by owner" }],
      ]),
    );
    expect(turn.status).toBe("stopped");
    expect(blockOf(turn, "stopped").reason).toBe("You stopped this investigation.");
    expect(blockOf(turn, "approval").open).toBe(false);
  });

  it("gives the reason it was given when the run was stopped for another cause", () => {
    const stopped = { code: "budget_exceeded", reason: "This investigation reached its limit." };
    const turn = turnOf(numbered([...ran(READ_ORDER), ["run.stopped", stopped]]));
    expect(blockOf(turn, "stopped").reason).toBe("This investigation reached its limit.");
    expect(blockOf(turn, "steps").live).toBe(false);
  });

  it("shows a stopped block with no words when no reason was given", () => {
    const turn = turnOf(numbered([["run.stopped", {}]]));
    expect(blockOf(turn, "stopped").reason).toBe("");
  });

  it("shows the message of a failed run", () => {
    const failed = { code: "INTERNAL", message: "The investigation could not go on." };
    const turn = turnOf(numbered([started(READ_ORDER), ["run.failed", failed]]));
    expect(turn.status).toBe("failed");
    expect(kinds(turn)).toEqual(["steps", "failed"]);
    expect(blockOf(turn, "failed").message).toBe("The investigation could not go on.");
  });
});

describe("a paused run", () => {
  const paused = (code: string): Draft => ["run.paused", { code }];

  it("is paused, asks for sign-in when that is why, and is not live", () => {
    const turn = turnOf(numbered([...ran(READ_ORDER), paused("reauth_required")]));
    expect(turn).toMatchObject({ status: "paused", needsSignIn: true });
    expect(blockOf(turn, "steps").live).toBe(false);
  });

  it("does not ask for sign-in when it paused for another reason or gave none", () => {
    expect(turnOf(numbered([paused("rate_limited")])).needsSignIn).toBe(false);
    expect(turnOf(numbered([["run.paused", null]]))).toMatchObject({
      status: "paused",
      needsSignIn: false,
    });
  });

  it.each([
    ["a step starts", started(READ_TICKET)],
    ["the assistant speaks", said("Back again.")],
    ["a decision arrives", decided("approved", ARJUN.name)],
  ])("is running again once %s", (_what, next) => {
    const turn = turnOf(numbered([...ONE_CHANGE, paused("reauth_required"), next]));
    expect(turn.needsSignIn).toBe(false);
    expect(turn.status).not.toBe("paused");
  });

  it("stays paused through events that do not mean work has resumed", () => {
    const budget = { tokens_in: 1, tokens_out: 1, tool_calls: 1, model_turns: 1, elapsed_ms: 1 };
    const turn = turnOf(numbered([paused("reauth_required"), ["budget.updated", budget]]));
    expect(turn.status).toBe("paused");
  });

  it("stays paused rather than waiting while a change is still open", () => {
    const turn = turnOf(numbered([...ONE_CHANGE, paused("reauth_required")]));
    expect(turn.status).toBe("paused");
    expect(blockOf(turn, "approval").open).toBe(true);
  });
});

describe("systems that did not answer", () => {
  const down = { status: "error", error_code: "BACKEND_UNAVAILABLE" };

  it("names nothing when every step answered", () => {
    expect(turnOf(numbered(ran(READ_ORDER))).unavailable).toEqual([]);
  });

  it("names each system once, in an engineer's words", () => {
    const turn = turnOf(
      numbered([
        ...ran(READ_ORDER, down),
        ...ran(READ_TICKET, down),
        ...ran({ id: "c7", tool: "list_tickets" }, down),
        ...ran({ id: "c8", tool: "something_new" }, down),
      ]),
    );
    expect(turn.unavailable).toEqual(["Orders", "Tickets", "One of Meridian's systems"]);
  });

  it("does not name a system for any other kind of error", () => {
    const turn = turnOf(numbered(ran(READ_ORDER, { status: "error", error_code: "TIMEOUT" })));
    expect(turn.unavailable).toEqual([]);
  });
});

describe("replayed and repeated events", () => {
  const WHOLE: RunEvent[] = numbered([
    said("Let me look."),
    ...REFUNDED,
    said("Done.", 2),
    completed({
      proposed_actions: [refundOutcome("executed")],
      customer_reply_draft: "We have refunded your order.",
    }),
  ]);

  it("draws the same conversation when every event arrives twice", () => {
    expect(turnOf([...WHOLE, ...WHOLE])).toEqual(turnOf(WHOLE));
    expect(turnOf(WHOLE.flatMap((event) => [event, event]))).toEqual(turnOf(WHOLE));
  });

  it.each([1, 4, 7, WHOLE.length - 1])(
    "draws the same conversation when the first %d events are followed by the whole run",
    (seen) => {
      expect(turnOf([...WHOLE.slice(0, seen), ...WHOLE])).toEqual(turnOf(WHOLE));
    },
  );

  it("draws nothing twice at any point of a run that is replayed from its start", () => {
    for (let seen = 1; seen <= WHOLE.length; seen += 1) {
      const live = WHOLE.slice(0, seen);
      expect(turnOf([...live, ...live])).toEqual(turnOf(live));
    }
  });

  it("drops an event whose number is below one already applied", () => {
    const [first, second, third] = numbered([said("One. "), said("Two. "), said("Three.")]);
    if (first === undefined || second === undefined || third === undefined) throw new Error();
    const turn = turnOf([first, third, second]);
    expect(blockOf(turn, "text").text).toBe("One. Three.");
    expect(turn.lastSeq).toBe(3);
  });

  it("reports the highest number it applied", () => {
    expect(turnOf(WHOLE).lastSeq).toBe(WHOLE.length);
  });

  it("does not ask twice for a change that is announced again with a new number", () => {
    const turn = turnOf(numbered([...ONE_CHANGE, asked(), asked({ actionId: ACTION_ID })]));
    expect(blocksOf(turn, "approval")).toHaveLength(1);
    expect(blockOf(turn, "approval").actions).toHaveLength(1);
  });
});

describe("a payload that does not fit its event", () => {
  const SAID = { seq: 2, type: "model.delta", data: { turn: 1, attempt: 1, text: "Still here." } };

  it.each([
    ["tool.started", { tool: "get_order" }],
    ["tool.finished", { call_id: "c1", tool: "get_order", phase: "read", status: "maybe" }],
    ["model.delta", { text: "no turn" }],
    ["approval.required", { action_id: ACTION_ID, tool: "update_ticket", args: NOTE_ARGS }],
    ["budget.updated", { tokens_in: "many" }],
    ["run.failed", { code: "INTERNAL" }],
    ["run.completed", { proposed_actions: "none" }],
    ["tool.started", null],
  ])("leaves out a malformed %s and keeps what follows", (type, data) => {
    const turn = turnOf([{ seq: 1, type, data }, SAID]);

    expect(turn.status).toBe("running");
    expect(turn.blocks).toEqual([{ kind: "text", key: "text-1", text: "Still here." }]);
  });
});
