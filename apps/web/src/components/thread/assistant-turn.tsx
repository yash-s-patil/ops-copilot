"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Layers } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApprovalCard } from "@/components/thread/approval-card";
import {
  DecisionLine,
  RefundCard,
  ReplyDraft,
  StoppedCard,
  SummaryCard,
} from "@/components/thread/cards";
import { StepGroup } from "@/components/thread/step-group";
import { Spinner } from "@/components/ui/spinner";
import { api, ApiError } from "@/lib/api";
import { TurnFollower, type ActionRow, type Block, type Turn } from "@/lib/conversation";
import { formatCount, formatSeconds } from "@/lib/format";
import type { Refund } from "@/lib/schemas";
import { useSession } from "@/lib/session";
import { myRunsKey } from "@/lib/use-my-runs";
import { useRunEvents, type Connection } from "@/lib/use-run-events";

export type TurnState = {
  status: Turn["status"];
  connection: Connection;
  unavailable: readonly string[];
};

type Props = {
  runId: string;
  message: string;
  refund: Refund | null;
  /** The run had ended before this page was read, so the page already shows what it did. */
  settled: boolean;
  onState?: (state: TurnState) => void;
};

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <p className="max-w-[92%] rounded-bubble rounded-br-[3px] border border-line bg-raised px-3 py-2 text-chat break-words whitespace-pre-wrap sm:max-w-[82%]">
        {text}
      </p>
    </div>
  );
}

function TechnicalFooter({ turn, runId }: { turn: Turn; runId: string }) {
  const { technical } = useSession();
  if (!technical || turn.budget === null) return null;
  const { budget } = turn;
  const cost =
    budget.estimated_cost_inr === null ? "not priced" : `INR ${budget.estimated_cost_inr}`;
  return (
    <p className="font-mono text-meta break-words text-ink-3">
      {budget.tool_calls} tool calls · {formatCount(budget.tokens_in + budget.tokens_out)} tokens ·{" "}
      {formatSeconds(budget.elapsed_ms)} · est. {cost} · run {runId.slice(0, 8)}
    </p>
  );
}

type BlockProps = { block: Block; runId: string; refund: Refund | null };

function BlockView({ block, runId, refund }: BlockProps) {
  // The run goes on once its changes are decided, and the page is read again when it ends.
  const decide = {
    approve: (action: ActionRow) => api.approve(runId, action.actionId, action.argsHash),
    reject: (action: ActionRow, reason: string) => api.reject(runId, action.actionId, reason),
  };
  switch (block.kind) {
    case "text":
      return (
        <p className="text-chat leading-[1.55] break-words whitespace-pre-wrap">{block.text}</p>
      );
    case "steps":
      return <StepGroup steps={block.steps} live={block.live} runId={runId} />;
    case "approval":
      return <ApprovalCard actions={block.actions} open={block.open} decide={decide} />;
    case "decision":
      return <DecisionLine approved={block.decision === "approved"} approver={block.approver} />;
    case "refund":
      return <RefundCard refund={refund} />;
    case "summary":
      return <SummaryCard cause={block.cause} done={block.done} notes={block.notes} />;
    case "reply":
      return <ReplyDraft draft={block.draft} />;
    case "stopped":
      return <StoppedCard title="Investigation stopped" reason={block.reason} />;
    case "failed":
      return <StoppedCard title="Investigation failed" reason={block.message} />;
  }
}

/** Reads the page again when a run that is being watched changes what the page shows. */
function useRefreshOnChange(status: Turn["status"], settled: boolean): void {
  const client = useQueryClient();
  const refreshedAt = useRef(status);
  useEffect(() => {
    if (settled || refreshedAt.current === status) return;
    refreshedAt.current = status;
    if (status === "running") return;
    void client.invalidateQueries({ queryKey: myRunsKey });
    void client.invalidateQueries({ queryKey: ["tickets"] });
    // A change that only waits for this person has altered nothing but the list's marker.
    if (status !== "awaiting") void client.invalidateQueries({ queryKey: ["workspace"] });
  }, [status, settled, client]);
}

/** One question and what the assistant did about it, in the order it happened. */
export function AssistantTurn({ runId, message, refund, settled, onState }: Props) {
  const { limits, signInEnded } = useSession();
  const { events, connection } = useRunEvents(runId, limits.reconnectMaxMs);
  const [follower] = useState(() => new TurnFollower());
  const turn = useMemo(() => follower.feed(events), [follower, events]);
  const { status, unavailable } = turn;
  useEffect(
    () => onState?.({ status, connection, unavailable }),
    [status, connection, unavailable, onState],
  );
  useEffect(() => {
    if (connection === "signed_out") signInEnded();
  }, [connection, signInEnded]);
  const { needsSignIn } = turn;
  useEffect(() => {
    if (!needsSignIn || settled) return;
    // The pause may be one this person has since signed in after; the server knows.
    api.run(runId).catch((error: unknown) => {
      if (error instanceof ApiError && error.signedOut) signInEnded();
    });
  }, [needsSignIn, settled, runId, signInEnded]);
  useRefreshOnChange(status, settled);
  const working = status === "running" && turn.blocks.at(-1)?.kind !== "steps";
  return (
    <>
      <UserBubble text={message} />
      <div className="flex gap-2.5">
        <span
          aria-hidden="true"
          className="mt-px grid size-6 shrink-0 place-items-center rounded-control bg-ink text-surface"
        >
          <Layers size={14} strokeWidth={1.75} />
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-2.5">
          {turn.blocks.map((block) => (
            <BlockView key={block.key} block={block} runId={runId} refund={refund} />
          ))}
          {working && (
            <p className="flex items-center gap-2 text-meta text-ink-3">
              <Spinner />
              Working
            </p>
          )}
          <TechnicalFooter turn={turn} runId={runId} />
        </div>
      </div>
    </>
  );
}
