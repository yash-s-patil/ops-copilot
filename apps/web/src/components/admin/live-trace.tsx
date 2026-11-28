"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { z } from "zod";
import { AdminPage, FigureStrip } from "@/components/admin/admin-page";
import { runStatus } from "@/components/admin/runs-table";
import { StatusWord } from "@/components/inbox/status-word";
import { PaneError } from "@/components/shell/pane-error";
import { ADMIN_LIVE_URL, api } from "@/lib/api";
import { STREAMED_EVENTS } from "@/lib/events";
import { formatCount, formatSeconds } from "@/lib/format";
import { adminEvent, type AdminEvent, type AdminRun } from "@/lib/schemas";
import { useSession } from "@/lib/session";
import { budgetOf, rawOf, spansOf, timelineOf } from "@/lib/trace";

const PERCENT = 100;
const MIN_WIDTH_PERCENT = 0.6;

export function BudgetStrip({ events }: { events: readonly AdminEvent[] }) {
  const { limits } = useSession();
  const budget = budgetOf(events);
  return (
    <FigureStrip
      figures={[
        {
          value: formatCount(budget?.tokens ?? 0),
          label: `tokens of ${formatCount(limits.runTokens)}`,
        },
        { value: `${budget?.toolCalls ?? 0} / ${limits.runToolCalls}`, label: "tool calls" },
        { value: formatSeconds(budget?.elapsedMs ?? 0), label: "elapsed, excluding human wait" },
        {
          value: budget?.cost == null ? "not priced" : `INR ${budget.cost}`,
          label: "est. cost",
        },
      ]}
    />
  );
}

export function SpanWaterfall({ events }: { events: readonly AdminEvent[] }) {
  const { spans, totalMs } = spansOf(events);
  if (spans.length === 0) return null;
  return (
    <figure className="rounded-card border border-line px-3.5 py-3">
      <figcaption className="sr-only">
        When each tool call ran and how long it took, in the order they were made.
      </figcaption>
      <ol className="grid grid-cols-[104px_minmax(0,1fr)_52px] items-center gap-x-2.5 gap-y-[5px] text-meta sm:grid-cols-[160px_minmax(0,1fr)_62px]">
        {spans.map((span) => (
          <li key={span.key} className="contents">
            <span className="truncate font-mono">{span.name}</span>
            <span className="relative h-2 rounded-[2px] bg-raised">
              <span
                className="absolute inset-y-0 min-w-[3px] rounded-[2px] bg-accent"
                style={{
                  left: `${((span.startMs / totalMs) * PERCENT).toFixed(2)}%`,
                  width: `${Math.max(MIN_WIDTH_PERCENT, (span.durationMs / totalMs) * PERCENT).toFixed(2)}%`,
                }}
              />
            </span>
            <span className="text-ink-3 tabular-nums">{span.durationMs} ms</span>
          </li>
        ))}
      </ol>
    </figure>
  );
}

export function TechnicalTimeline({ events }: { events: readonly AdminEvent[] }) {
  const rows = timelineOf(events);
  return (
    <ol className="flex flex-col rounded-card border border-line">
      {rows.map((row) => (
        <li
          key={row.key}
          className="flex flex-wrap items-center gap-x-2.5 gap-y-1 border-b border-line px-3 py-[7px] text-step text-ink-2 last:border-b-0"
        >
          {row.mono ? (
            <>
              <span className="font-mono font-medium text-ink">{row.label}</span>
              <StatusWord tone={row.tone}>{row.state}</StatusWord>
            </>
          ) : (
            <StatusWord tone={row.tone}>{row.label}</StatusWord>
          )}
          {row.note !== "" && <span>{row.note}</span>}
          {row.aside !== "" && (
            <span className="ml-auto text-meta text-ink-3 tabular-nums">{row.aside}</span>
          )}
        </li>
      ))}
      {rows.length === 0 && <li className="px-3 py-[7px] text-meta text-ink-3">Nothing yet.</li>}
    </ol>
  );
}

export function RawEventStream({ events }: { events: readonly AdminEvent[] }) {
  return (
    <pre className="overflow-auto rounded-control border border-line bg-raised px-2.5 py-2 font-mono text-meta break-words whitespace-pre-wrap">
      {rawOf(events)}
    </pre>
  );
}

export function Trace({ run, events }: { run: AdminRun | null; events: readonly AdminEvent[] }) {
  const [raw, setRaw] = useState(false);
  const [words, tone] = run === null ? ["", "neutral" as const] : runStatus(run.status);
  return (
    <>
      {run !== null && (
        <p className="flex flex-wrap items-center gap-x-1.5 text-meta text-ink-3">
          Run <span className="font-mono">{run.run_id.slice(0, 8)}</span> · {run.user_name} ·
          <StatusWord tone={tone}>{words}</StatusWord>
        </p>
      )}
      <BudgetStrip events={events} />
      <SpanWaterfall events={events} />
      <label className="flex items-center gap-1.5 text-meta text-ink-3">
        <input type="checkbox" checked={raw} onChange={(event) => setRaw(event.target.checked)} />
        Show the raw event stream (server-sent events)
      </label>
      {raw ? <RawEventStream events={events} /> : <TechnicalTimeline events={events} />}
    </>
  );
}

type Followed = { runId: string | null; events: AdminEvent[] };

const movedTo = z.object({ run_id: z.string() });

/** A frame that is not JSON is read as nothing, and then refused by its schema. */
function jsonOf(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/** Follows whichever run started most recently, moving on when a newer one starts. */
function useLatestRun(): Followed {
  const [followed, setFollowed] = useState<Followed>({ runId: null, events: [] });
  useEffect(() => {
    const source = new EventSource(ADMIN_LIVE_URL);
    source.addEventListener("run", (message: MessageEvent<string>) => {
      const next = movedTo.safeParse(jsonOf(message.data));
      setFollowed({ runId: next.success ? next.data.run_id : null, events: [] });
    });
    const onEvent = (message: MessageEvent<string>) => {
      const parsed = adminEvent.safeParse(jsonOf(message.data));
      if (!parsed.success) return;
      setFollowed((known) =>
        known.events.some((event) => event.seq === parsed.data.seq)
          ? known
          : { ...known, events: [...known.events, parsed.data] },
      );
    };
    for (const type of STREAMED_EVENTS) source.addEventListener(type, onEvent);
    return () => source.close();
  }, []);
  return followed;
}

export function LiveTrace() {
  const { limits } = useSession();
  const { runId, events } = useLatestRun();
  const runs = useQuery({
    queryKey: ["admin", "runs"],
    queryFn: api.adminRuns,
    refetchInterval: limits.inboxRefreshMs,
  });
  const run = runs.data?.items.find((item) => item.run_id === runId) ?? null;
  // A run that started since the list was read is not in it yet: read it again at once.
  const unlisted = runId !== null && runs.isSuccess && run === null;
  const { refetch } = runs;
  useEffect(() => {
    if (unlisted) void refetch();
  }, [unlisted, runId, refetch]);
  return (
    <AdminPage
      title="Live trace"
      about="Every tool call with its timing, size and tokens, as it happens. Engineers see plain steps; this is the full trace."
    >
      {runs.isError && <PaneError error={runs.error} retry={() => runs.refetch()} />}
      {runId === null ? (
        <p className="py-7 text-center text-ink-3">No runs yet.</p>
      ) : (
        <Trace run={run} events={events} />
      )}
    </AdminPage>
  );
}
