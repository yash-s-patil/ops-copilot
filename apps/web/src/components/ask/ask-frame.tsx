"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import type { ReactNode } from "react";
import { PaneError } from "@/components/shell/pane-error";
import { AssistantTurn } from "@/components/thread/assistant-turn";
import { Composer } from "@/components/thread/composer";
import { PinnedScroll } from "@/components/thread/conversation";
import { SkeletonLines } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { formatClock } from "@/lib/format";
import type { RunView } from "@/lib/schemas";
import { runFinished } from "@/lib/use-my-runs";
import { isRunId } from "@/lib/ids";

const askKey = ["runs", "ask"] as const;

function useRunId(): string | null {
  const raw = useParams<{ runId?: string }>().runId;
  return isRunId(raw) ? raw : null;
}

function outcome(run: RunView): string {
  if (run.status === "awaiting_approval") return "Waiting for your approval";
  if (run.status === "running" || run.status === "queued") return "In progress";
  return run.status === "completed" ? "Answered" : run.status.replaceAll("_", " ");
}

function ChatRow({ run, selected }: { run: RunView; selected: boolean }) {
  const state = selected
    ? "border-l-accent bg-accent-wash"
    : "border-l-transparent hover:bg-raised";
  return (
    <Link
      href={`/ask/${run.run_id}`}
      aria-current={selected ? "true" : undefined}
      className={`grid grid-cols-[minmax(0,1fr)_auto] gap-x-[9px] gap-y-0.5 border-b border-l-2 border-b-line py-[11px] pr-4 pl-3.5 ${state}`}
    >
      <span className="truncate font-medium">{run.message}</span>
      <span className="text-meta text-ink-3 tabular-nums">
        {formatClock(new Date(run.started_at))}
      </span>
      <span className="col-span-2 truncate text-meta text-ink-3">{outcome(run)}</span>
    </Link>
  );
}

function ChatList({ runId }: { runId: string | null }) {
  const chats = useQuery({ queryKey: askKey, queryFn: () => api.runs(true) });
  return (
    <section aria-label="Chats" className="flex h-full min-h-0 flex-col">
      <header className="flex min-h-[52px] shrink-0 items-center gap-2 border-b border-line px-4 py-2">
        <h1 className="flex-1 text-title font-semibold tracking-[-0.01em]">Ask Copilot</h1>
        <Link
          href="/ask"
          className="flex h-[30px] items-center rounded-control border border-line-2 bg-surface px-[11px] font-medium hover:bg-raised"
        >
          New chat
        </Link>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {chats.isPending && (
          <div className="p-4">
            <SkeletonLines widths={["w-3/4", "w-1/2", "w-2/3"]} />
          </div>
        )}
        {chats.isError && <PaneError error={chats.error} retry={() => chats.refetch()} />}
        {chats.data?.items.map((run) => (
          <ChatRow key={run.run_id} run={run} selected={run.run_id === runId} />
        ))}
        {chats.data?.items.length === 0 && (
          <p className="px-5 py-7 text-center text-ink-3">
            Questions that are not tied to a ticket appear here.
          </p>
        )}
      </div>
    </section>
  );
}

/** Below 720 px one pane is shown: the chat, or the list of chats when it was asked for. */
export function AskFrame({ children }: { children: ReactNode }) {
  const runId = useRunId();
  const listing = useSearchParams().get("chats") !== null && runId === null;
  return (
    <>
      <div
        className={`min-h-0 shrink-0 border-r border-line sm:w-[280px] md:w-[320px] lg:w-[340px] max-sm:w-full max-sm:border-r-0 ${listing ? "" : "max-sm:hidden"}`}
      >
        <ChatList runId={runId} />
      </div>
      <section
        aria-label="Chat"
        className={`flex min-h-0 min-w-0 flex-1 flex-col ${listing ? "max-sm:hidden" : ""}`}
      >
        {children}
      </section>
    </>
  );
}

function ChatBody({ runId }: { runId: string }) {
  const run = useQuery({ queryKey: ["runs", runId], queryFn: () => api.run(runId) });
  if (run.isPending) {
    return <SkeletonLines widths={["w-1/2", "w-2/3", "w-1/3"]} />;
  }
  if (run.isError) return <PaneError error={run.error} retry={() => run.refetch()} />;
  return (
    <AssistantTurn
      runId={runId}
      message={run.data.message}
      refund={null}
      settled={runFinished(run.data.status)}
    />
  );
}

/** The same conversation as on a ticket, with no ticket and no details beside it. */
export function AskWorkspace() {
  const runId = useRunId();
  const router = useRouter();
  const client = useQueryClient();
  const start = useMutation({
    mutationFn: (message: string) => api.startRun(message, null),
    onSuccess: async (started) => {
      await client.invalidateQueries({ queryKey: askKey });
      router.push(`/ask/${started.run_id}`);
    },
  });
  return (
    <>
      <header className="flex min-h-[52px] shrink-0 items-center gap-2 border-b border-line px-4 py-2">
        <Link
          href="/ask?chats"
          aria-label="Your chats"
          className="grid size-[30px] shrink-0 place-items-center rounded-control text-ink-2 hover:bg-raised sm:hidden pointer-coarse:size-11"
        >
          <ChevronLeft size={16} strokeWidth={1.75} aria-hidden="true" />
        </Link>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-title font-semibold tracking-[-0.01em]">Ask Copilot</h1>
          <p className="truncate text-meta text-ink-3">Not tied to a ticket</p>
        </div>
      </header>
      <PinnedScroll>
        {runId === null ? (
          <p className="px-5 py-7 text-center text-ink-3">
            Ask about any order by number. The assistant only sees what you are allowed to see.
          </p>
        ) : (
          <ChatBody key={runId} runId={runId} />
        )}
      </PinnedScroll>
      <Composer
        placeholder="Ask about an order, for example: What is wrong with order 4471?"
        running={false}
        onSend={async (message) => {
          await start.mutateAsync(message);
        }}
        onStop={() => undefined}
      />
    </>
  );
}
