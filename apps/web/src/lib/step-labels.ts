import { formatMoney } from "@/lib/format";
import type { ToolPhase } from "@/lib/events";
import { own } from "@/lib/own";

/** What an engineer reads on a step line. A tool's own name is never shown to them. */
export type StepLabel = { running: string; done: string };

type Args = Record<string, unknown>;

const CURRENCY = "INR";

function amount(args: Args): string {
  const minor = args["amount_minor"];
  return typeof minor === "number" ? formatMoney(minor, CURRENCY) : "an amount";
}

function words(value: unknown): string {
  return typeof value === "string" ? value.replaceAll("-", " ").replaceAll("_", " ") : "policy";
}

function refund(phase: ToolPhase, args: Args): StepLabel {
  if (phase === "execute") {
    return {
      running: `Recording the refund of ${amount(args)}`,
      done: `Recorded the refund of ${amount(args)}`,
    };
  }
  return {
    running: `Preparing a refund of ${amount(args)}`,
    done: `Prepared a refund of ${amount(args)}`,
  };
}

function ticketChange(phase: ToolPhase, args: Args): StepLabel {
  const escalates = args["escalate_to"] != null;
  if (phase === "execute") {
    return escalates
      ? {
          running: "Saving the note and escalating to L2",
          done: "Saved the note and escalated to L2",
        }
      : { running: "Saving the ticket note", done: "Saved the ticket note" };
  }
  return escalates
    ? {
        running: "Preparing a note and an escalation to L2",
        done: "Prepared a note and an escalation to L2",
      }
    : { running: "Preparing a ticket note", done: "Prepared a ticket note" };
}

const READS: Record<string, (args: Args) => StepLabel> = {
  get_order: (args) => ({
    running: `Checking order ${String(args["order_id"] ?? "")}`.trim(),
    done: `Checked order ${String(args["order_id"] ?? "")}`.trim(),
  }),
  lookup_customer: () => ({ running: "Looking up the customer", done: "Looked up the customer" }),
  list_tickets: () => ({ running: "Finding the ticket", done: "Found the ticket" }),
  get_ticket: () => ({ running: "Reading the ticket notes", done: "Read the ticket notes" }),
  search_runbooks: () => ({ running: "Searching the runbooks", done: "Searched the runbooks" }),
  get_policy_document: (args) => ({
    running: `Reading the ${words(args["name"])}`,
    done: `Read the ${words(args["name"])}`,
  }),
};

const UNKNOWN: StepLabel = { running: "Working on a step", done: "Finished a step" };

export function stepLabel(tool: string, phase: ToolPhase, args: Args): StepLabel {
  if (tool === "issue_refund") return refund(phase, args);
  if (tool === "update_ticket") return ticketChange(phase, args);
  return own(READS, tool)?.(args) ?? UNKNOWN;
}

/** What a guardrail event says to an engineer, in plain words. */
const GUARDRAILS: Record<string, string> = {
  no_evidence: "A proposed change was dropped because nothing supported it",
  amount_not_derivable: "A proposed refund was dropped because it did not follow from the order",
  amount_over_cap: "Refund amount refused by policy",
  reply_contact_details: "The reply draft was withheld because it held contact details",
  canary: "The assistant's answer broke a safety rule and was not used",
  budget: "Stopped to stay within the limits of one investigation",
};

export function guardrailLine(rule: string): string {
  return own(GUARDRAILS, rule) ?? "A safety check stepped in";
}

export const WITHHELD_LINE = "A search result was withheld as suspicious";

/** What an engineer would call the thing a step relies on, for when it does not answer. */
const RELIES_ON: Record<string, string> = {
  get_order: "Orders",
  lookup_customer: "Customer records",
  list_tickets: "Tickets",
  get_ticket: "Tickets",
  update_ticket: "Tickets",
  search_runbooks: "Runbook search",
  get_policy_document: "Policy documents",
  issue_refund: "Refunds",
};

export function reliesOn(tool: string): string {
  return own(RELIES_ON, tool) ?? "One of Meridian's systems";
}
