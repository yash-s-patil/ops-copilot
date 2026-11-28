import { own } from "@/lib/own";

const STAGES = ["Placed", "Paid", "Packed", "Shipped", "Delivered"] as const;
const AT: Record<string, number> = {
  placed: 0,
  paid: 1,
  packed: 2,
  shipped: 3,
  delivered: 4,
  lost: 3,
};

type Props = { status: string; stuck: boolean };

function segment(index: number, at: number, status: string, stuck: boolean): string {
  if (index < at) return "border-t-ink-3 text-ink-3";
  if (index > at) return "border-t-line text-ink-3";
  if (status === "lost") return "border-t-bad font-medium text-bad";
  return stuck ? "border-t-warn font-medium text-ink" : "border-t-ink-3 font-medium text-ink";
}

/** Five stages. The current one is marked when the order is stuck, and says Lost when it is lost. */
export function OrderStageBar({ status, stuck }: Props) {
  const at = own(AT, status) ?? -1;
  if (at < 0) return <p className="text-meta text-ink-3">This order was {status}.</p>;
  return (
    <ol className="grid grid-cols-5 gap-1">
      {STAGES.map((stage, index) => (
        <li
          key={stage}
          aria-current={index === at ? "step" : undefined}
          className={`truncate border-t-[3px] pt-1.5 text-label ${segment(index, at, status, stuck)}`}
        >
          {index === at && status === "lost" ? "Lost" : stage}
        </li>
      ))}
    </ol>
  );
}
