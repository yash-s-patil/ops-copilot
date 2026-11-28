import { describe, expect, it } from "vitest";
import { turnOf } from "@/lib/conversation";
import { blockOf, blocksOf, callLines, kinds } from "@/test/blocks";
import { ACTION_ID, ARJUN, KAVITA, OTHER_ACTION_ID, PRIYA } from "@/test/fixtures";
import {
  asked,
  decided,
  EXECUTE_NOTE,
  EXECUTE_REFUND,
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

const NOTE_ASK = { actionId: OTHER_ACTION_ID, tool: "update_ticket", args: NOTE_ARGS };

/** A note and a refund prepared in one turn, each followed by its request for approval. */
const TWO_CHANGES: Draft[] = [
  ...ran(READ_ORDER),
  ...ran(PROPOSE_NOTE),
  asked(NOTE_ASK),
  ...ran(PROPOSE_REFUND),
  asked(),
];

const ONE_CHANGE: Draft[] = [...ran(READ_ORDER), ...ran(PROPOSE_REFUND), asked()];

describe("asking for approval", () => {
  it("shows one open card for a change the engineer may approve, and waits for them", () => {
    const turn = turnOf(numbered(ONE_CHANGE));
    expect(kinds(turn)).toEqual(["steps", "approval"]);
    expect(turn.status).toBe("awaiting");
    const card = blockOf(turn, "approval");
    expect(card.open).toBe(true);
    expect(card.actions).toEqual([
      expect.objectContaining({
        actionId: ACTION_ID,
        tool: "issue_refund",
        approvalKind: "self",
        status: "open",
        approver: null,
      }),
    ]);
  });

  it("marks the prepared change as prepared, and the steps above the card as no longer live", () => {
    const steps = blockOf(turnOf(numbered(ONE_CHANGE)), "steps");
    expect(steps.live).toBe(false);
    expect(callLines(steps.steps)).toEqual(["get_order:read:ok", "issue_refund:propose:prepared"]);
  });

  it("asks once for several changes prepared in the same turn", () => {
    const turn = turnOf(numbered(TWO_CHANGES));
    expect(kinds(turn)).toEqual(["steps", "approval"]);
    expect(blockOf(turn, "approval").actions.map((row) => row.tool)).toEqual([
      "update_ticket",
      "issue_refund",
    ]);
  });

  it("keeps every prepared step in the steps block above the card", () => {
    const steps = blockOf(turnOf(numbered(TWO_CHANGES)), "steps");
    expect(callLines(steps.steps)).toEqual([
      "get_order:read:ok",
      "update_ticket:propose:prepared",
      "issue_refund:propose:prepared",
    ]);
  });
});

describe("asking more than once", () => {
  it("starts a second card when the assistant reads something between two requests", () => {
    const turn = turnOf(
      numbered([...ran(PROPOSE_NOTE), asked(NOTE_ASK), ...ran(READ_TICKET), asked()]),
    );
    expect(kinds(turn)).toEqual(["steps", "approval", "steps", "approval"]);
  });

  it("does not add a row twice when the same request is announced again", () => {
    const turn = turnOf(numbered([...ONE_CHANGE, asked()]));
    expect(blockOf(turn, "approval").actions).toHaveLength(1);
  });

  it("waits for someone else, not for the engineer, when a change needs a supervisor", () => {
    const turn = turnOf(numbered([...ran(PROPOSE_REFUND), asked({ kind: "supervisor" })]));
    const card = blockOf(turn, "approval");
    expect(card.actions[0]).toMatchObject({ approvalKind: "supervisor", status: "waiting" });
    expect(turn.status).toBe("waiting");
  });

  it("goes on once the supervisor has decided", () => {
    const turn = turnOf(
      numbered([
        ...ran(PROPOSE_REFUND),
        asked({ kind: "supervisor" }),
        decided("approved", "Kavita Rao"),
      ]),
    );
    expect(turn.status).toBe("running");
  });
});

describe("a decision", () => {
  it("closes the card and says who approved once the only change is approved", () => {
    const turn = turnOf(numbered([...ONE_CHANGE, decided("approved", ARJUN.name)]));
    expect(kinds(turn)).toEqual(["steps", "approval", "decision"]);
    expect(blockOf(turn, "approval")).toMatchObject({ open: false });
    expect(blockOf(turn, "approval").actions[0]).toMatchObject({
      status: "approved",
      approver: ARJUN.name,
    });
    expect(blockOf(turn, "decision")).toMatchObject({
      decision: "approved",
      approver: ARJUN.name,
    });
    expect(turn.status).toBe("running");
  });

  it("keeps waiting, with no decision line, while another change on the card is open", () => {
    const turn = turnOf(
      numbered([...TWO_CHANGES, decided("approved", PRIYA.name, OTHER_ACTION_ID)]),
    );
    expect(kinds(turn)).toEqual(["steps", "approval"]);
    expect(blockOf(turn, "approval").open).toBe(true);
    expect(turn.status).toBe("awaiting");
  });

  it("says who approved once, however many changes they approved", () => {
    const turn = turnOf(
      numbered([
        ...TWO_CHANGES,
        decided("approved", PRIYA.name, OTHER_ACTION_ID),
        decided("approved", PRIYA.name),
      ]),
    );
    expect(
      blocksOf(turn, "decision").map((line) => `${line.decision} by ${line.approver}`),
    ).toEqual([`approved by ${PRIYA.name}`]);
  });

  it("gives one line each to an approval and a rejection on the same card", () => {
    const turn = turnOf(
      numbered([
        ...TWO_CHANGES,
        decided("approved", ARJUN.name, OTHER_ACTION_ID),
        decided("rejected", ARJUN.name),
      ]),
    );
    expect(blocksOf(turn, "decision").map((line) => line.decision)).toEqual([
      "approved",
      "rejected",
    ]);
  });
});

describe("a rejection, an expiry and a supervisor", () => {
  it("records a rejection and stops waiting", () => {
    const turn = turnOf(numbered([...ONE_CHANGE, decided("rejected", ARJUN.name)]));
    expect(blockOf(turn, "approval").actions[0]).toMatchObject({ status: "rejected" });
    expect(blockOf(turn, "decision")).toMatchObject({ decision: "rejected" });
    expect(turn.status).toBe("running");
  });

  it("closes the card with no decision line when the approval ran out of time", () => {
    const turn = turnOf(numbered([...ONE_CHANGE, decided("expired", null)]));
    expect(kinds(turn)).toEqual(["steps", "approval"]);
    expect(blockOf(turn, "approval")).toMatchObject({ open: false });
    expect(blockOf(turn, "approval").actions[0]).toMatchObject({ status: "expired" });
    expect(turn.status).toBe("running");
  });

  it("records a supervisor's approval of a waiting change", () => {
    const turn = turnOf(
      numbered([
        ...ran(PROPOSE_REFUND),
        asked({ kind: "supervisor" }),
        decided("approved", KAVITA.name),
      ]),
    );
    expect(blockOf(turn, "approval").actions[0]).toMatchObject({
      status: "approved",
      approver: KAVITA.name,
    });
    expect(blockOf(turn, "decision").approver).toBe(KAVITA.name);
  });
});

describe("a decision that must be ignored", () => {
  it("keeps the first decision when a second one arrives for the same change", () => {
    const turn = turnOf(
      numbered([...ONE_CHANGE, decided("approved", ARJUN.name), decided("rejected", KAVITA.name)]),
    );
    expect(blockOf(turn, "approval").actions[0]).toMatchObject({
      status: "approved",
      approver: ARJUN.name,
    });
    expect(blocksOf(turn, "decision")).toHaveLength(1);
  });

  it("ignores a decision it does not know the word for", () => {
    const turn = turnOf(numbered([...ONE_CHANGE, decided("postponed", ARJUN.name)]));
    expect(blockOf(turn, "approval")).toMatchObject({ open: true });
    expect(turn.status).toBe("awaiting");
  });

  it("ignores a decision about a change that was never asked for", () => {
    const turn = turnOf(numbered([...ran(READ_ORDER), decided("approved", ARJUN.name)]));
    expect(kinds(turn)).toEqual(["steps"]);
  });
});

describe("after approval", () => {
  const APPROVED: Draft[] = [...ONE_CHANGE, decided("approved", ARJUN.name)];

  it("shows the change being made in a new steps block below the decision", () => {
    const turn = turnOf(numbered([...APPROVED, started(EXECUTE_REFUND)]));
    expect(kinds(turn)).toEqual(["steps", "approval", "decision", "steps"]);
    const [above, below] = blocksOf(turn, "steps");
    expect(callLines(above?.steps ?? [])).toContain("issue_refund:propose:prepared");
    expect(callLines(below?.steps ?? [])).toEqual(["issue_refund:execute:running"]);
    expect(below?.live).toBe(true);
  });

  it("keeps the prepared step and the executed step apart though they share a call", () => {
    const turn = turnOf(numbered([...APPROVED, ...ran(EXECUTE_REFUND)]));
    const lines = blocksOf(turn, "steps").flatMap((block) => callLines(block.steps));
    expect(lines).toEqual([
      "get_order:read:ok",
      "issue_refund:propose:prepared",
      "issue_refund:execute:ok",
    ]);
  });

  it("goes on to further turns after the change is made", () => {
    const turn = turnOf(
      numbered([...APPROVED, ...ran(EXECUTE_REFUND), said("The refund is recorded.", 2)]),
    );
    expect(kinds(turn).slice(-2)).toEqual(["steps", "text"]);
    expect(turn.status).toBe("running");
  });

  it("asks again on a new card for a change prepared after the first was made", () => {
    const turn = turnOf(
      numbered([...APPROVED, ...ran(EXECUTE_REFUND), ...ran(PROPOSE_NOTE), asked(NOTE_ASK)]),
    );
    expect(kinds(turn)).toEqual(["steps", "approval", "decision", "steps", "approval"]);
    expect(blocksOf(turn, "approval").map((card) => card.open)).toEqual([false, true]);
    expect(turn.status).toBe("awaiting");
  });

  it("shows both changes being made after both were approved", () => {
    const turn = turnOf(
      numbered([
        ...TWO_CHANGES,
        decided("approved", ARJUN.name, OTHER_ACTION_ID),
        decided("approved", ARJUN.name),
        ...ran(EXECUTE_NOTE),
        ...ran(EXECUTE_REFUND),
      ]),
    );
    expect(callLines(blocksOf(turn, "steps").at(-1)?.steps ?? [])).toEqual([
      "update_ticket:execute:ok",
      "issue_refund:execute:ok",
    ]);
  });
});
