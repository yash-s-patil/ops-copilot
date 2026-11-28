"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export const myRunsKey = ["runs"] as const;

const AWAITING = "awaiting_approval";
const FINISHED: ReadonlySet<string> = new Set(["completed", "stopped", "failed", "cancelled"]);

/** A run that has ended changes nothing more, so a page that shows it need not watch it. */
export function runFinished(status: string): boolean {
  return FINISHED.has(status);
}

/** The person's own investigations, newest first. Read from the assistant's own records. */
export function useMyRuns() {
  return useQuery({ queryKey: myRunsKey, queryFn: () => api.runs(false) });
}

/** The tickets on which an investigation of this person's waits for their decision. */
export function useTicketsAwaitingMe(): ReadonlySet<number> {
  const runs = useMyRuns();
  const waiting = new Set<number>();
  for (const run of runs.data?.items ?? []) {
    if (run.status === AWAITING && run.ticket_id !== null) waiting.add(run.ticket_id);
  }
  return waiting;
}
