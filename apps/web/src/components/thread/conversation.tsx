"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Layers } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { AssistantTurn, type TurnState } from "@/components/thread/assistant-turn";
import {
  CustomerMessage,
  EscalationBanner,
  PriorInvestigation,
  SupervisorApprovalBanner,
} from "@/components/thread/banners";
import { Composer, ConnectionLine } from "@/components/thread/composer";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import type { TurnStatus } from "@/lib/conversation";
import type { Refund, RunView, WaitingAction, Workspace } from "@/lib/schemas";
import { withSupervisor } from "@/lib/refund-words";
import { useSession } from "@/lib/session";
import { myRunsKey, runFinished, useMyRuns } from "@/lib/use-my-runs";

export const INVESTIGATE = "Investigate this ticket and propose a resolution.";
const NEAR_BOTTOM_PX = 80;
const UNFINISHED: ReadonlySet<TurnStatus> = new Set(["running", "awaiting", "waiting", "paused"]);

type Live = TurnState;

/** Keeps the newest line in view while the person has not scrolled away from it. */
export function PinnedScroll({ children }: { children: ReactNode }) {
  const pane = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  useEffect(() => {
    const element = pane.current;
    if (element !== null && pinned.current) element.scrollTop = element.scrollHeight;
  });
  return (
    <div
      ref={pane}
      onScroll={(event) => {
        const el = event.currentTarget;
        pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
      }}
      className="min-h-0 flex-1 overflow-y-auto px-4 pt-3.5 pb-1.5 sm:px-5 sm:pt-[18px] sm:pb-2"
    >
      <div aria-live="polite" className="mx-auto flex max-w-[760px] flex-col gap-3.5">
        {children}
      </div>
    </div>
  );
}

function unfinished(live: Live | null): boolean {
  return live !== null && UNFINISHED.has(live.status);
}

/** The one line above the composer: a lost connection first, then what did not answer. */
export function connectionWords(live: Live | null): string | null {
  if (live === null) return null;
  if (live.connection === "lost" && unfinished(live)) {
    return "Connection lost. Reconnecting, and nothing will be missed.";
  }
  if (live.unavailable.length === 0) return null;
  const verb = live.unavailable.length === 1 ? "is" : "are";
  return `${live.unavailable.join(" and ")} ${verb} not answering right now. Copilot went on without.`;
}

type RunsProps = {
  runs: readonly RunView[];
  refund: Refund | null;
  onLive: (runId: string, live: Live) => void;
};

function OwnRuns({ runs, refund, onLive }: RunsProps) {
  return (
    <>
      {runs.map((run) => (
        <AssistantTurn
          key={run.run_id}
          runId={run.run_id}
          message={run.message}
          refund={refund}
          settled={runFinished(run.status)}
          onState={(state) => onLive(run.run_id, state)}
        />
      ))}
    </>
  );
}

function useOwnRuns(ticketId: number) {
  const runs = useMyRuns();
  const own = (runs.data?.items ?? []).filter((run) => run.ticket_id === ticketId).reverse();
  return { own, loaded: runs.isSuccess };
}

/**
 * A change a supervisor approved is carried out when its requester is next on the ticket.
 * It is asked for once a visit; if it is refused, the person is told why and it is not
 * asked for again until they come back.
 */
function useCarryOutApproved(workspace: Workspace): string | null {
  const client = useQueryClient();
  const asked = useRef(new Set<string>());
  const [refused, setRefused] = useState<string | null>(null);
  useEffect(() => {
    for (const action of workspace.approvals) {
      // A change its requester approves is carried out by the run that asked for it.
      const due =
        action.mine && action.approval_kind === "supervisor" && action.status === "approved";
      if (!due || asked.current.has(action.action_id)) continue;
      asked.current.add(action.action_id);
      void api.execute(action.action_id).then(
        () => client.invalidateQueries(),
        (error: unknown) =>
          setRefused(
            `The change a supervisor approved was not made. ${
              error instanceof ApiError ? error.message : "Open the ticket again to try again."
            }`,
          ),
      );
    }
  }, [workspace.approvals, client]);
  return refused;
}

/**
 * A refund waiting for a supervisor, shown to supervisors. Once a supervisor has approved,
 * the server no longer lists it for them, so what they approved on this visit stays on the
 * page as it was when they decided.
 */
function SupervisorBanners({ workspace }: { workspace: Workspace }) {
  const client = useQueryClient();
  const { can } = useSession();
  const [approvedHere, setApprovedHere] = useState<WaitingAction[]>([]);
  const refresh = () => client.invalidateQueries();
  const waiting = workspace.approvals.filter(
    (action) =>
      can("refunds.approve") &&
      action.approval_kind === "supervisor" &&
      action.status !== "approved" &&
      (action.may_decide || action.mine),
  );
  const listed = new Set(waiting.map((action) => action.action_id));
  const shown = [...waiting, ...approvedHere.filter((action) => !listed.has(action.action_id))];
  return (
    <>
      {shown.map((action) => (
        <SupervisorApprovalBanner
          key={action.action_id}
          action={action}
          approve={async () => {
            await api.supervisorApprove(action.action_id, action.args_hash);
            setApprovedHere((known) => [...known, { ...action, status: "approved" }]);
            await refresh();
          }}
          reject={(reason) => api.supervisorReject(action.action_id, reason).then(refresh)}
        />
      ))}
    </>
  );
}

type Props = { workspace: Workspace };

function sameLive(known: Live | undefined, next: Live): boolean {
  return (
    known?.status === next.status &&
    known.connection === next.connection &&
    known.unavailable.join() === next.unavailable.join()
  );
}

function useLive() {
  const [live, setLive] = useState<Record<string, Live>>({});
  const onLive = useCallback((runId: string, state: Live) => {
    setLive((known) => (sameLive(known[runId], state) ? known : { ...known, [runId]: state }));
  }, []);
  return { live, onLive };
}

/** A supervisor deciding a refund is not offered an investigation of their own beside it. */
function useSupervising(workspace: Workspace): boolean {
  const { can } = useSession();
  const state = workspace.ticket.refund?.state ?? null;
  const mineToCarryOut = workspace.approvals.some((action) => action.mine);
  return can("refunds.approve") && withSupervisor(state) && !mineToCarryOut;
}

/** What was there before this person began: the customer's words and what colleagues found. */
function Before({ workspace }: Props) {
  const { ticket } = workspace;
  const customerNotes = ticket.notes.filter((note) => note.author_kind === "customer").reverse();
  const others = workspace.investigations.filter((run) => !run.mine).reverse();
  return (
    <>
      {customerNotes.map((note) => (
        <CustomerMessage key={note.created_at} name={note.author_name} body={note.body} />
      ))}
      <EscalationBanner ticket={ticket} />
      {others.map((run) => (
        <PriorInvestigation key={run.run_id} run={run} />
      ))}
    </>
  );
}

function Offer({ pending, onStart }: { pending: boolean; onStart: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="primary" disabled={pending} onClick={onStart}>
        <Layers size={14} strokeWidth={1.75} aria-hidden="true" />
        Investigate with Copilot
      </Button>
      <span className="text-meta text-ink-3">
        Reads the order, ticket, runbooks and policy, then proposes a fix.
      </span>
    </div>
  );
}

function Resolved() {
  return (
    <p className="flex items-center gap-2 text-meta text-ink-3">
      <Check size={14} strokeWidth={1.75} aria-hidden="true" />
      Ticket resolved
    </p>
  );
}

/** The person's latest investigation on the ticket and how it stands, if they have one. */
function latestOf(
  own: readonly RunView[],
  live: Readonly<Record<string, Live>>,
): { runId: string | null; state: Live | null } {
  const latest = own.at(-1);
  if (latest === undefined) return { runId: null, state: null };
  return { runId: latest.run_id, state: live[latest.run_id] ?? null };
}

/** Stops the person's latest investigation, then reads the page again. */
function useStop(runId: string | null): () => void {
  const client = useQueryClient();
  return () => {
    if (runId === null) return;
    void api.cancel(runId).then(() => client.invalidateQueries());
  };
}

export function Conversation({ workspace }: Props) {
  const { ticket } = workspace;
  const client = useQueryClient();
  const { own, loaded } = useOwnRuns(ticket.ticket_id);
  const { live, onLive } = useLive();
  const supervising = useSupervising(workspace);
  const notMade = useCarryOutApproved(workspace);
  const start = useMutation({
    mutationFn: (message: string) => api.startRun(message, ticket.ticket_id),
    onSuccess: () => client.invalidateQueries({ queryKey: myRunsKey }),
  });
  const { runId, state } = latestOf(own, live);
  const resolved = ticket.status === "resolved";
  const offered = !resolved && !unfinished(state) && !supervising && loaded;
  const lost = notMade ?? connectionWords(state);
  const stop = useStop(runId);
  return (
    <>
      <PinnedScroll>
        <Before workspace={workspace} />
        <OwnRuns runs={own} refund={ticket.refund} onLive={onLive} />
        <SupervisorBanners workspace={workspace} />
        {resolved && <Resolved />}
        {offered && <Offer pending={start.isPending} onStart={() => start.mutate(INVESTIGATE)} />}
      </PinnedScroll>
      {lost !== null && <ConnectionLine text={lost} />}
      <Composer
        placeholder="Ask a follow-up about this ticket"
        running={state?.status === "running"}
        onSend={async (message) => {
          await start.mutateAsync(message);
        }}
        onStop={stop}
      />
    </>
  );
}
