"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Check,
  ChevronRight,
  Clock,
  Lock,
  ShieldAlert,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";
import { Spinner } from "@/components/ui/spinner";
import { api } from "@/lib/api";
import { stepCounts, type CallStep, type NoticeStep, type Step } from "@/lib/conversation";
import { formatBytes, formatSeconds, plural } from "@/lib/format";
import { useSession } from "@/lib/session";
import { stepLabel } from "@/lib/step-labels";

const ICON = { size: 16, strokeWidth: 1.75, "aria-hidden": true } as const;
const SMALL = { size: 14, strokeWidth: 1.75, "aria-hidden": true } as const;

const STATE_ICON: Record<Exclude<CallStep["state"], "running">, [LucideIcon, string, string]> = {
  ok: [Check, "text-ok", "Done"],
  prepared: [Clock, "text-accent", "Needs approval"],
  denied: [Lock, "text-bad", "Refused for your role"],
  error: [TriangleAlert, "text-warn", "Problem"],
};

function StateIcon({ state }: { state: CallStep["state"] }) {
  if (state === "running") return <Spinner />;
  const [Icon, colour, label] = STATE_ICON[state];
  return (
    <span className={colour}>
      <Icon {...ICON} />
      <span className="sr-only">{label}: </span>
    </span>
  );
}

export function WithheldNotice({ text }: { text: string }) {
  return (
    <div className="mt-2 flex flex-col gap-1 rounded-card border border-bad bg-bad-wash px-[11px] py-[9px]">
      <b className="font-medium">Withheld from the assistant. Shown to you only.</b>
      <p className="break-words">{text}</p>
    </div>
  );
}

function CallJson({ runId, callId }: { runId: string; callId: string }) {
  const detail = useQuery({
    queryKey: ["call", runId, callId],
    queryFn: () => api.callDetail(runId, callId),
    staleTime: Infinity,
  });
  if (!detail.data) return null;
  return (
    <pre className="mt-1.5 max-h-[260px] overflow-auto rounded-control border border-line bg-raised px-2.5 py-2 font-mono text-meta break-words whitespace-pre-wrap">
      {JSON.stringify({ arguments: detail.data.args, result: detail.data.result }, null, 1)}
    </pre>
  );
}

export function StepLine({ step, runId }: { step: CallStep; runId: string }) {
  const { technical } = useSession();
  const [open, setOpen] = useState(false);
  const label = stepLabel(step.tool, step.phase, step.args);
  return (
    <li>
      <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary className="flex items-center gap-2 px-2.5 py-[5px]">
          <StateIcon state={step.state} />
          <span className="min-w-0 flex-1">
            {step.state === "running" ? label.running : label.done}
          </span>
          {technical && step.state !== "running" && (
            <span className="hidden truncate font-mono text-fine text-ink-3 sm:inline">
              {step.tool} · {step.durationMs} ms · {formatBytes(step.bytes)}
            </span>
          )}
          <ChevronRight
            {...SMALL}
            className={`shrink-0 text-ink-3 transition-transform duration-[120ms] ${open ? "rotate-90" : ""}`}
          />
        </summary>
        <div className="pr-2.5 pb-2 pl-[34px] text-step text-ink-2">
          {step.summary && <p className="break-words">{step.summary}</p>}
          {step.withheld.map((text, index) => (
            <WithheldNotice key={index} text={text} />
          ))}
          {technical && open && <CallJson runId={runId} callId={step.callId} />}
        </div>
      </details>
    </li>
  );
}

function NoticeLine({ step }: { step: NoticeStep }) {
  const { technical } = useSession();
  const Icon = step.icon === "shield" ? ShieldAlert : Lock;
  return (
    <li
      className={`flex items-center gap-2 px-2.5 py-[5px] text-step ${step.tone === "bad" ? "text-bad" : "text-ink-3"}`}
    >
      <Icon {...SMALL} className="shrink-0" />
      <span>
        {step.text}
        {technical && step.technical !== null && (
          <span className="font-mono text-meta"> {step.technical}</span>
        )}
      </span>
    </li>
  );
}

function summaryLine(steps: readonly Step[]): string {
  const { calls, durationMs, issues } = stepCounts(steps);
  const head = calls === 0 ? "Details" : `${plural(calls, "step")} · ${formatSeconds(durationMs)}`;
  return issues === 0 ? head : `${head} · ${plural(issues, "issue")}`;
}

type Props = { steps: readonly Step[]; live: boolean; runId: string };

/** While the run works, every step as it happens. Afterwards one line that opens. */
export function StepGroup({ steps, live, runId }: Props) {
  const [opened, setOpened] = useState(false);
  const open = live || opened;
  return (
    <details open={open} className="rounded-card border border-line">
      {/* The browser opens a group by itself while it is live, so only a click is remembered. */}
      <summary
        onClick={(event) => {
          event.preventDefault();
          if (!live) setOpened((was) => !was);
        }}
        className={`flex items-center gap-2 px-2.5 py-[7px] text-step text-ink-2 ${open ? "border-b border-line" : ""}`}
      >
        {live ? (
          <Spinner />
        ) : (
          <ChevronRight
            {...SMALL}
            className={`text-ink-3 transition-transform duration-[120ms] ${open ? "rotate-90" : ""}`}
          />
        )}
        <span>{live ? "Working" : summaryLine(steps)}</span>
      </summary>
      <ul className="py-1">
        {steps.map((step) =>
          step.kind === "call" ? (
            <StepLine key={step.key} step={step} runId={runId} />
          ) : (
            <NoticeLine key={step.key} step={step} />
          ),
        )}
      </ul>
    </details>
  );
}
