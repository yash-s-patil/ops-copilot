import { describe, expect, it } from "vitest";
import type { ToolPhase } from "@/lib/events";
import { guardrailLine, reliesOn, stepLabel, WITHHELD_LINE } from "@/lib/step-labels";
import { ORDER_ID, plainSpaces, REFUND_MINOR, REFUND_WORDS } from "@/test/fixtures";

const INTERNAL = /\b(tools?|tokens?|mcp|pending actions?)\b|_/i;
const KNOWN_TOOLS = [
  "get_order",
  "lookup_customer",
  "list_tickets",
  "get_ticket",
  "search_runbooks",
  "get_policy_document",
  "update_ticket",
  "issue_refund",
];
const PHASES: readonly ToolPhase[] = ["read", "propose", "execute"];

function label(tool: string, phase: ToolPhase, args: Record<string, unknown> = {}) {
  const { running, done } = stepLabel(tool, phase, args);
  return { running: plainSpaces(running), done: plainSpaces(done) };
}

describe("stepLabel", () => {
  it("names the order a read is about", () => {
    expect(label("get_order", "read", { order_id: ORDER_ID })).toEqual({
      running: "Checking order 4471",
      done: "Checked order 4471",
    });
  });

  it("leaves no trailing space when the order is not named", () => {
    expect(label("get_order", "read")).toEqual({
      running: "Checking order",
      done: "Checked order",
    });
  });

  it.each([
    ["lookup_customer", "Looking up the customer", "Looked up the customer"],
    ["list_tickets", "Finding the ticket", "Found the ticket"],
    ["get_ticket", "Reading the ticket notes", "Read the ticket notes"],
    ["search_runbooks", "Searching the runbooks", "Searched the runbooks"],
  ])("describes %s in plain words", (tool, running, done) => {
    expect(label(tool, "read")).toEqual({ running, done });
  });

  it("names a policy document by its words, not its file name", () => {
    expect(label("get_policy_document", "read", { name: "refund-policy_v2" }).done).toBe(
      "Read the refund policy v2",
    );
  });

  it("says policy when the document is not named or is not text", () => {
    expect(label("get_policy_document", "read").running).toBe("Reading the policy");
    expect(label("get_policy_document", "read", { name: 7 }).done).toBe("Read the policy");
  });
});

describe("stepLabel for a change", () => {
  it("says a refund is being prepared, with its amount, before approval", () => {
    expect(label("issue_refund", "propose", { amount_minor: REFUND_MINOR })).toEqual({
      running: `Preparing a refund of ${REFUND_WORDS}`,
      done: `Prepared a refund of ${REFUND_WORDS}`,
    });
  });

  it("says a refund is being recorded once it is approved", () => {
    expect(label("issue_refund", "execute", { amount_minor: REFUND_MINOR })).toEqual({
      running: `Recording the refund of ${REFUND_WORDS}`,
      done: `Recorded the refund of ${REFUND_WORDS}`,
    });
  });

  it.each([{}, { amount_minor: "620" }, { amount_minor: null }])(
    "says an amount when the refund's amount is missing or not a number: %j",
    (args) => {
      expect(label("issue_refund", "propose", args).done).toBe("Prepared a refund of an amount");
    },
  );

  it.each([
    ["propose", {}, "Preparing a ticket note", "Prepared a ticket note"],
    ["execute", {}, "Saving the ticket note", "Saved the ticket note"],
    [
      "propose",
      { escalate_to: "L2" },
      "Preparing a note and an escalation to L2",
      "Prepared a note and an escalation to L2",
    ],
    [
      "execute",
      { escalate_to: "L2" },
      "Saving the note and escalating to L2",
      "Saved the note and escalated to L2",
    ],
  ] as const)("describes a ticket change in phase %s with %j", (phase, args, running, done) => {
    expect(label("update_ticket", phase, args)).toEqual({ running, done });
  });

  it("does not call a note an escalation when escalate_to is null", () => {
    expect(label("update_ticket", "propose", { escalate_to: null }).done).toBe(
      "Prepared a ticket note",
    );
  });
});

describe("stepLabel for a step it has no words for", () => {
  it("describes a step it has no words for without naming it", () => {
    expect(label("purge_everything", "read", { id: 1 })).toEqual({
      running: "Working on a step",
      done: "Finished a step",
    });
  });

  it("falls back for a step named like something every object has", () => {
    expect(label("toString", "read")).toEqual({
      running: "Working on a step",
      done: "Finished a step",
    });
  });

  it("never shows an engineer a name from the inside, whatever the step", () => {
    const lines = [...KNOWN_TOOLS, "something_new"].flatMap((tool) =>
      PHASES.flatMap((phase) => Object.values(label(tool, phase, { name: "refund-policy" }))),
    );
    expect(lines.filter((line) => INTERNAL.test(line))).toEqual([]);
  });
});

describe("guardrailLine", () => {
  it.each([
    ["no_evidence", "A proposed change was dropped because nothing supported it"],
    ["amount_over_cap", "Refund amount refused by policy"],
    ["budget", "Stopped to stay within the limits of one investigation"],
  ])("says what the %s rule did", (rule, line) => {
    expect(guardrailLine(rule)).toBe(line);
  });

  it("has words for a rule it does not know, and for no rule at all", () => {
    expect(guardrailLine("brand_new_rule")).toBe("A safety check stepped in");
    expect(guardrailLine("")).toBe("A safety check stepped in");
  });

  it("falls back for a rule named like something every object has", () => {
    expect(guardrailLine("constructor")).toBe("A safety check stepped in");
  });

  it("says a withheld result plainly", () => {
    expect(WITHHELD_LINE).not.toMatch(INTERNAL);
  });
});

describe("reliesOn", () => {
  it.each([
    ["get_order", "Orders"],
    ["get_ticket", "Tickets"],
    ["update_ticket", "Tickets"],
    ["issue_refund", "Refunds"],
    ["search_runbooks", "Runbook search"],
  ])("says %s relies on %s", (tool, system) => {
    expect(reliesOn(tool)).toBe(system);
  });

  it("does not name a system it does not know", () => {
    expect(reliesOn("something_new")).toBe("One of Meridian's systems");
  });
});
