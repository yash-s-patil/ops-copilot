"use client";

import { ArrowUp, Clock, CreditCard, FileText, type LucideIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { StatusWord, type Tone } from "@/components/inbox/status-word";
import { Card, CardTitle } from "@/components/thread/cards";
import { RejectForm } from "@/components/thread/reject-form";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import type { ActionRow, ActionStatus } from "@/lib/conversation";
import { formatMoney } from "@/lib/format";
import { useSession } from "@/lib/session";

const ICON = { size: 16, strokeWidth: 1.75, "aria-hidden": true } as const;
const CURRENCY = "INR";

const STATUS: Partial<Record<ActionStatus, [string, Tone]>> = {
  done: ["Done", "ok"],
  rejected: ["Rejected", "bad"],
  waiting: ["Waiting for supervisor", "warn"],
  expired: ["Not approved in time", "bad"],
  failed: ["Not done", "bad"],
  unknown: ["Not confirmed", "warn"],
};

type Described = { icon: LucideIcon; title: string; detail: string };

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function describe(action: ActionRow): Described {
  const { args } = action;
  if (action.tool === "issue_refund") {
    const minor = args["amount_minor"];
    const amount = typeof minor === "number" ? formatMoney(minor, CURRENCY) : "";
    const who =
      action.approvalKind === "self"
        ? "You can approve refunds up to INR 5,000 yourself."
        : "Above INR 5,000, so a supervisor must approve.";
    return {
      icon: CreditCard,
      title: `Refund ${amount} on order ${String(args["order_id"] ?? "")}`,
      detail: `${text(args["reason_text"])} ${who}`.trim(),
    };
  }
  const ticket = `#${String(args["ticket_id"] ?? "")}`;
  const note = text(args["note"]);
  const quoted = note === "" ? "" : `"${note}"`;
  if (args["escalate_to"] != null) {
    return {
      icon: ArrowUp,
      title: `Add a note and escalate ticket ${ticket} to L2`,
      detail: quoted,
    };
  }
  if (args["status"] != null) {
    return {
      icon: FileText,
      title: `Mark ticket ${ticket} as ${text(args["status"])}`,
      detail: quoted,
    };
  }
  return { icon: FileText, title: `Add a note to ticket ${ticket}`, detail: quoted };
}

function ActionLine({ action }: { action: ActionRow }) {
  const { technical } = useSession();
  const { icon: Icon, title, detail } = describe(action);
  const status = STATUS[action.status];
  return (
    <li className="flex items-start gap-2.5 border-t border-line pt-2.5 first:border-t-0 first:pt-0">
      <Icon {...ICON} className="mt-px shrink-0 text-ink-3" />
      <div className="min-w-0 flex-1">
        <b className="font-medium">{title}</b>
        {detail !== "" && <p className="text-meta break-words text-ink-3">{detail}</p>}
        {technical && (
          <p className="truncate font-mono text-meta text-ink-3">
            {action.tool} · args hash {action.argsHash.slice(0, 12)} · {action.actionId}
          </p>
        )}
      </div>
      {status !== undefined && <StatusWord tone={status[1]}>{status[0]}</StatusWord>}
    </li>
  );
}

type Decide = {
  approve: (action: ActionRow) => Promise<unknown>;
  reject: (action: ActionRow, reason: string) => Promise<unknown>;
};

function problem(error: unknown): string {
  return error instanceof ApiError ? error.message : "That did not go through. Try again.";
}

function useDecision(actions: readonly ActionRow[]) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mine = actions.filter((action) => action.status === "open");
  const each = async (one: (action: ActionRow) => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      // One after another: the run goes on when the last of them is decided.
      for (const action of mine) await one(action);
    } catch (failure) {
      setError(problem(failure));
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, each };
}

type Props = { actions: readonly ActionRow[]; open: boolean; decide: Decide };

function Changes({ actions }: { actions: readonly ActionRow[] }) {
  return (
    <ul className="flex flex-col gap-2.5">
      {actions.map((action) => (
        <ActionLine key={action.actionId} action={action} />
      ))}
    </ul>
  );
}

function Decision({ actions, decide }: { actions: readonly ActionRow[]; decide: Decide }) {
  const [rejecting, setRejecting] = useState(false);
  const { busy, error, each } = useDecision(actions);
  return (
    <>
      {rejecting ? (
        <RejectForm
          onBack={() => setRejecting(false)}
          onReject={(reason) => each((action) => decide.reject(action, reason))}
        />
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" disabled={busy} onClick={() => each(decide.approve)}>
            Approve
          </Button>
          <Button disabled={busy} onClick={() => setRejecting(true)}>
            Reject
          </Button>
          <span className="text-meta text-ink-3">Nothing has been changed yet.</span>
        </div>
      )}
      {error !== null && (
        <p role="alert" className="text-meta text-bad">
          {error}
        </p>
      )}
    </>
  );
}

/**
 * Says in words exactly what will change, before it does. It takes the focus when it
 * appears, so that a person using a keyboard or a screen reader lands on the decision.
 */
export function ApprovalCard({ actions, open, decide }: Props) {
  const card = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open) card.current?.focus();
  }, [open]);
  if (!open) {
    return (
      <Card>
        <CardTitle>Proposed changes</CardTitle>
        <Changes actions={actions} />
      </Card>
    );
  }
  return (
    <div
      ref={card}
      tabIndex={-1}
      role="group"
      aria-label="Approve these changes"
      className="rounded-card outline-none"
    >
      <Card tone="needed">
        <CardTitle>
          <Clock {...ICON} />
          Approve these changes
        </CardTitle>
        <Changes actions={actions} />
        <Decision actions={actions} decide={decide} />
      </Card>
    </div>
  );
}
