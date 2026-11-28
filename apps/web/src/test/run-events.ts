/** Run events as the agent streams them, for tests of what the conversation makes of them. */
import type { RunEvent, ToolPhase } from "@/lib/events";
import { ACTION_ID, ARGS_HASH, ORDER_ID, REFUND_MINOR, TICKET_ID } from "@/test/fixtures";

export type Draft = readonly [type: string, data: unknown];

/** Numbers the events from one, as the agent does. */
export function numbered(drafts: readonly Draft[]): RunEvent[] {
  return drafts.map(([type, data], index) => ({ seq: index + 1, type, data }));
}

export const REFUND_ARGS = {
  order_id: ORDER_ID,
  amount_minor: REFUND_MINOR,
  reason_code: "lost_in_transit",
  reason_text: "The parcel was lost by the carrier.",
};
export const NOTE_ARGS = { ticket_id: TICKET_ID, note: "Carrier confirmed the loss." };

type Call = { id: string; tool: string; phase?: ToolPhase; args?: Record<string, unknown> };

export function started({ id, tool, phase = "read", args = {} }: Call): Draft {
  return ["tool.started", { call_id: id, tool, phase, args }];
}

export function finished(call: Call, over: Record<string, unknown> = {}): Draft {
  return [
    "tool.finished",
    {
      call_id: call.id,
      tool: call.tool,
      phase: call.phase ?? "read",
      status: "ok",
      duration_ms: 120,
      bytes: 512,
      ...over,
    },
  ];
}

/** A call from start to a good finish. */
export function ran(call: Call, over: Record<string, unknown> = {}): Draft[] {
  return [started(call), finished(call, over)];
}

export function said(text: string, turn = 1, attempt = 1): Draft {
  return ["model.delta", { turn, attempt, text }];
}

type Ask = {
  actionId?: string;
  tool?: string;
  args?: Record<string, unknown>;
  kind?: "self" | "supervisor";
};

export function asked({
  actionId = ACTION_ID,
  tool = "issue_refund",
  args = REFUND_ARGS,
  kind = "self",
}: Ask = {}): Draft {
  return [
    "approval.required",
    {
      action_id: actionId,
      tool,
      args,
      args_hash: ARGS_HASH,
      approval_kind: kind,
      expires_at: "2026-11-28T10:02:00Z",
    },
  ];
}

export function decided(
  decision: string,
  approver: string | null,
  actionId: string = ACTION_ID,
): Draft {
  return ["approval.resolved", { action_id: actionId, decision, approver }];
}

export const READ_ORDER = { id: "c1", tool: "get_order", args: { order_id: ORDER_ID } } as const;
export const READ_TICKET = { id: "c2", tool: "get_ticket" } as const;
export const PROPOSE_REFUND = {
  id: "c3",
  tool: "issue_refund",
  phase: "propose",
  args: REFUND_ARGS,
} as const;
export const PROPOSE_NOTE = {
  id: "c4",
  tool: "update_ticket",
  phase: "propose",
  args: NOTE_ARGS,
} as const;
export const EXECUTE_REFUND = { ...PROPOSE_REFUND, phase: "execute" } as const;
export const EXECUTE_NOTE = { ...PROPOSE_NOTE, phase: "execute" } as const;
