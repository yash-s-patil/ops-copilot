import type { RefundState } from "@/lib/schemas";

type Segment = { label: string; state: "done" | "current" | "failed" | "ahead" };

const BAR: Record<Segment["state"], string> = {
  done: "border-t-ok text-ink",
  current: "border-t-accent text-ink",
  failed: "border-t-bad text-bad",
  ahead: "border-t-line text-ink-3",
};

function segments(state: RefundState): Segment[] {
  const sent = state !== "approved";
  const last: Segment =
    state === "failed"
      ? { label: "Payment failed", state: "failed" }
      : { label: "Paid", state: state === "paid" ? "done" : sent ? "current" : "ahead" };
  return [
    { label: "Approved", state: "done" },
    { label: "Sent to payments", state: sent ? "done" : "current" },
    last,
  ];
}

/** Approved, sent to payments, paid: where a refund is, as three segments with words. */
export function RefundProgress({ state }: { state: RefundState }) {
  return (
    <ol className="grid grid-cols-3 gap-1.5">
      {segments(state).map((segment) => (
        <li
          key={segment.label}
          aria-current={segment.state === "current" ? "step" : undefined}
          className={`border-t-[3px] pt-[7px] text-meta ${BAR[segment.state]}`}
        >
          {segment.label}
        </li>
      ))}
    </ol>
  );
}
