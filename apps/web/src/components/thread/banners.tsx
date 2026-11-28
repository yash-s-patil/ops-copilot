"use client";

import { ArrowUp, CreditCard, Layers, type LucideIcon } from "lucide-react";
import { useState, type ReactNode } from "react";
import { RejectForm } from "@/components/thread/reject-form";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { formatClock, formatMoney, formatUtc, sentenceCase } from "@/lib/format";
import type { Investigation, Ticket, WaitingAction } from "@/lib/schemas";
import { own } from "@/lib/own";

const ICON = { size: 16, strokeWidth: 1.75, "aria-hidden": true } as const;
const CURRENCY = "INR";

type BannerProps = { icon: LucideIcon; accent?: boolean; title: string; children?: ReactNode };

function Banner({ icon: Icon, accent = false, title, children }: BannerProps) {
  const tone = accent ? "border-accent bg-accent-wash" : "border-line bg-raised";
  return (
    <div className={`flex items-start gap-2.5 rounded-card border px-3 py-2.5 ${tone}`}>
      <Icon {...ICON} className="mt-px shrink-0" />
      <div className="min-w-0 flex-1">
        <b className="font-medium">{title}</b>
        {children}
      </div>
    </div>
  );
}

export function CustomerMessage({ name, body }: { name: string; body: string }) {
  return (
    <div className="border-l-2 border-line-2 py-0.5 pl-3">
      <p className="text-meta font-medium text-ink-3">{name} · customer</p>
      <p className="text-chat break-words text-ink-2">{body}</p>
    </div>
  );
}

export function EscalationBanner({ ticket }: { ticket: Ticket }) {
  if (ticket.escalated_by_name === null) return null;
  const advice = ticket.recommendation;
  return (
    <Banner icon={ArrowUp} title={`Escalated to L2 by ${ticket.escalated_by_name}`}>
      {advice !== null && (
        <p className="text-meta text-ink-3">
          Recommended refund: {formatMoney(advice.amount_minor, CURRENCY)} (
          {sentenceCase(advice.reason_code).toLowerCase()}).
        </p>
      )}
    </Banner>
  );
}

const DID: Record<string, string> = {
  issue_refund: "Refund approved and handed to payments",
  update_ticket: "Ticket updated",
};

/** Another engineer's investigation as one paragraph: who, when, the cause, what was done. */
export function PriorInvestigation({ run }: { run: Investigation }) {
  const at = new Date(run.started_at);
  const done = run.actions
    .filter((action) => action.status === "executed" && action.tool !== null)
    .map((action) => own(DID, action.tool ?? "") ?? "A change was made");
  return (
    <Banner icon={Layers} title={`Investigation by ${run.by_name} · ${formatClock(at)}`}>
      <p className="text-meta break-words text-ink-3" title={formatUtc(at)}>
        {run.root_cause ?? "Still in progress."} {done.length > 0 ? `${done.join(". ")}.` : ""}
      </p>
    </Banner>
  );
}

type SupervisorProps = {
  action: WaitingAction;
  approve: () => Promise<unknown>;
  reject: (reason: string) => Promise<unknown>;
};

function policyLine(action: WaitingAction): string {
  const reason = action.args["reason_text"];
  return typeof reason === "string" ? reason : action.summary;
}

function titleOf(action: WaitingAction, approved: boolean): string {
  const minor = action.args["amount_minor"];
  if (typeof minor !== "number") {
    return approved ? "You approved a refund" : "A refund needs your approval";
  }
  const amount = formatMoney(minor, CURRENCY);
  return approved
    ? `You approved a refund of ${amount}`
    : `Refund of ${amount} needs your approval`;
}

type ChoiceProps = Pick<SupervisorProps, "approve" | "reject">;

function SupervisorChoice({ approve, reject }: ChoiceProps) {
  const [busy, setBusy] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const run = async (decision: () => Promise<unknown>) => {
    setBusy(true);
    setProblem(null);
    try {
      await decision();
    } catch (error) {
      setProblem(error instanceof ApiError ? error.message : "That did not go through. Try again.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mt-2 flex flex-col gap-1">
      {rejecting ? (
        <RejectForm
          onBack={() => setRejecting(false)}
          onReject={(reason) => run(() => reject(reason))}
        />
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" disabled={busy} onClick={() => run(approve)}>
            Approve refund
          </Button>
          <Button disabled={busy} onClick={() => setRejecting(true)}>
            Reject
          </Button>
        </div>
      )}
      {problem !== null && (
        <p role="alert" className="text-meta text-bad">
          {problem}
        </p>
      )}
    </div>
  );
}

/** A refund waiting for a supervisor, shown where the supervisor reads the ticket. */
export function SupervisorApprovalBanner({ action, approve, reject }: SupervisorProps) {
  const approved = action.status === "approved";
  const requester = action.requester_name.split(" ")[0] ?? action.requester_name;
  const title = titleOf(action, approved);
  return (
    <Banner icon={CreditCard} accent title={title}>
      <p className="text-meta break-words text-ink-3">
        Requested by {action.requester_name} at {formatClock(new Date(action.created_at))}. Policy
        check: {policyLine(action)}
        {approved ? ` It completes when ${requester} next opens the ticket.` : ""}
      </p>
      {!approved && action.mine && (
        <p className="text-meta text-ink-3">You cannot approve your own request.</p>
      )}
      {!approved && action.may_decide && <SupervisorChoice approve={approve} reject={reject} />}
    </Banner>
  );
}
