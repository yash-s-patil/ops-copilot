import type { Priority } from "@/lib/schemas";

const FILLED: Record<Priority, number> = { P1: 4, P2: 3, P3: 2, P4: 1 };
const BARS = [0, 1, 2, 3] as const;
const BAR_STEP_X = 3.2;
const BAR_STEP_Y = 2.6;

/** Four ascending bars, one to four filled for P4 to P1. P1 is filled in the problem colour. */
export function PriorityGlyph({ priority }: { priority: Priority }) {
  const on = priority === "P1" ? "fill-bad" : "fill-ink-2";
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      role="img"
      aria-label={`Priority ${priority}`}
      className="mt-[3px] shrink-0"
    >
      {BARS.map((bar) => (
        <rect
          key={bar}
          x={1 + bar * BAR_STEP_X}
          y={10 - bar * BAR_STEP_Y}
          width="2.2"
          height={3 + bar * BAR_STEP_Y}
          rx="0.6"
          className={bar < FILLED[priority] ? on : "fill-line-2"}
        />
      ))}
    </svg>
  );
}
