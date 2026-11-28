"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AdminPage } from "@/components/admin/admin-page";
import { Trace } from "@/components/admin/live-trace";
import { RunsTable } from "@/components/admin/runs-table";
import { PaneError } from "@/components/shell/pane-error";
import { SkeletonLines } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { isRunId } from "@/lib/ids";

const ABOUT = "Who ran what, how it stands and what it used. Nothing a run read or wrote is shown.";

export function AdminRuns() {
  const runs = useQuery({ queryKey: ["admin", "runs"], queryFn: api.adminRuns });
  return (
    <AdminPage title="Runs" about={ABOUT}>
      {runs.isPending && <SkeletonLines widths={["w-full", "w-full", "w-2/3"]} />}
      {runs.isError && <PaneError error={runs.error} retry={() => runs.refetch()} />}
      {runs.data && <RunsTable runs={runs.data.items} />}
    </AdminPage>
  );
}

export function AdminRunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const known = isRunId(runId);
  const run = useQuery({
    queryKey: ["admin", "runs", runId],
    queryFn: () => api.adminRun(runId),
    enabled: known,
  });
  return (
    <AdminPage title="Runs" about={ABOUT}>
      <div>
        <Link
          href="/admin/runs"
          className="inline-flex h-[30px] items-center gap-1.5 rounded-control border border-line-2 bg-surface px-[11px] font-medium hover:bg-raised"
        >
          <ChevronLeft size={14} strokeWidth={1.75} aria-hidden="true" />
          All runs
        </Link>
      </div>
      {!known && <p className="text-ink-2">No run has that id.</p>}
      {known && run.isPending && <SkeletonLines widths={["w-1/3", "w-full", "w-full"]} />}
      {run.isError && <PaneError error={run.error} retry={() => run.refetch()} />}
      {run.data && <Trace run={run.data.run} events={run.data.events} />}
    </AdminPage>
  );
}
