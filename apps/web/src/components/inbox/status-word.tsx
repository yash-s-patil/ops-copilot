import type { ReactNode } from "react";

export type Tone = "neutral" | "ok" | "warn" | "bad" | "accent";

const TEXT: Record<Tone, string> = {
  neutral: "text-ink-2",
  ok: "text-ok",
  warn: "text-warn",
  bad: "text-bad",
  accent: "text-accent",
};

const DOT: Record<Tone, string> = {
  neutral: "bg-ink-3",
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
  accent: "bg-accent",
};

/** A state as a word with a dot. The word carries the meaning; the colour only repeats it. */
export function StatusWord({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1.5 text-meta whitespace-nowrap ${TEXT[tone]}`}>
      <span aria-hidden="true" className={`size-[7px] shrink-0 rounded-full ${DOT[tone]}`} />
      {children}
    </span>
  );
}
