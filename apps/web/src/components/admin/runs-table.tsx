import Link from "next/link";
import { StatusWord, type Tone } from "@/components/inbox/status-word";
import { formatCount, formatSeconds } from "@/lib/format";
import type { AdminRun } from "@/lib/schemas";

const STATUS: Record<string, [string, Tone]> = {
  completed: ["completed", "ok"],
  stopped: ["stopped", "bad"],
  failed: ["failed", "bad"],
  cancelled: ["stopped by its owner", "neutral"],
  awaiting_approval: ["waiting for approval", "warn"],
  reauth_required: ["waiting for sign-in", "warn"],
};

export function runStatus(status: string): [string, Tone] {
  return STATUS[status] ?? [status.replaceAll("_", " "), "warn"];
}

const ROLE: Record<string, string> = {
  "Support.L1": "L1",
  "Support.L2": "L2",
  "Support.Supervisor": "Supervisor",
};

const TH =
  "border-b border-line bg-raised px-3 py-2 text-left text-meta font-medium whitespace-nowrap text-ink-3";
const TD = "border-b border-line px-3 py-2 align-top";

function Row({ run }: { run: AdminRun }) {
  const [words, tone] = runStatus(run.status);
  return (
    <tr className="last:[&>td]:border-b-0">
      <td className={TD}>
        <Link
          href={`/admin/runs/${run.run_id}`}
          className="font-mono text-meta text-accent underline underline-offset-2"
        >
          {run.run_id.slice(0, 8)}
        </Link>
      </td>
      <td className={TD}>
        {run.user_name}{" "}
        <span className="text-meta text-ink-3">
          {run.roles.map((role) => ROLE[role] ?? "").join(" ")}
        </span>
      </td>
      <td className={TD}>
        <StatusWord tone={tone}>{words}</StatusWord>
      </td>
      <td className={TD}>{run.tool_calls}</td>
      <td className={TD}>{formatCount(run.tokens_in + run.tokens_out)}</td>
      <td className={TD}>
        {run.estimated_cost_inr === null ? "not priced" : `INR ${run.estimated_cost_inr}`}
      </td>
      <td className={TD}>{formatSeconds(run.elapsed_ms)}</td>
    </tr>
  );
}

/** All runs: who, how it stands, and what it used. Nothing about what it was about. */
export function RunsTable({ runs }: { runs: readonly AdminRun[] }) {
  return (
    <div className="overflow-x-auto rounded-card border border-line">
      <table className="w-full border-collapse tabular-nums">
        <thead>
          <tr>
            {["Run", "User", "Status", "Tool calls", "Tokens", "Est. cost", "Time"].map((name) => (
              <th key={name} scope="col" className={TH}>
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <Row key={run.run_id} run={run} />
          ))}
          {runs.length === 0 && (
            <tr>
              <td colSpan={7} className="px-3 py-2 text-meta text-ink-3">
                No runs yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
