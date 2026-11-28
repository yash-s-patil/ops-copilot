"use client";

import { Check, Copy, TriangleAlert, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { RefundProgress } from "@/components/thread/refund-progress";
import { Button } from "@/components/ui/button";
import { formatMoney } from "@/lib/format";
import type { Refund } from "@/lib/schemas";

const ICON = { size: 16, strokeWidth: 1.75, "aria-hidden": true } as const;
const SMALL = { size: 14, strokeWidth: 1.75, "aria-hidden": true } as const;
const COPIED_MS = 2000;
const WITH_PAYMENTS = new Set(["approved", "sent_to_payments", "paid", "failed"]);

const CARD_TONE = {
  plain: "border-line",
  needed: "border-accent",
  stopped: "border-bad bg-bad-wash",
} as const;

type CardProps = { children: ReactNode; tone?: keyof typeof CARD_TONE };

export function Card({ children, tone = "plain" }: CardProps) {
  return (
    <div className={`flex flex-col gap-2.5 rounded-card border px-3.5 py-3 ${CARD_TONE[tone]}`}>
      {children}
    </div>
  );
}

export function CardTitle({ children }: { children: ReactNode }) {
  return <h2 className="flex flex-wrap items-center gap-2 font-semibold">{children}</h2>;
}

export function DecisionLine({ approved, approver }: { approved: boolean; approver: string }) {
  const Icon = approved ? Check : X;
  return (
    <p className="flex items-start gap-2 text-meta text-ink-3">
      <Icon {...SMALL} className="mt-px shrink-0" />
      <span>
        {approved ? "Approved" : "Rejected"} by {approver}
      </span>
    </p>
  );
}

/** Appears once a refund is recorded and follows it to paid or failed by itself. */
export function RefundCard({ refund }: { refund: Refund | null }) {
  if (refund === null || !WITH_PAYMENTS.has(refund.state)) return null;
  return (
    <Card>
      <CardTitle>Refund of {formatMoney(refund.amount_minor, refund.currency)}</CardTitle>
      <RefundProgress state={refund.state} />
      <p className="text-meta text-ink-3">
        Payment is handled by the payments service after approval. This status updates by itself.
      </p>
    </Card>
  );
}

type SummaryProps = { cause: string; done: readonly string[]; notes: readonly string[] };

export function SummaryCard({ cause, done, notes }: SummaryProps) {
  return (
    <Card>
      <CardTitle>Summary</CardTitle>
      <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3.5 gap-y-[3px] text-step">
        <dt className="text-ink-3">Cause</dt>
        <dd className="break-words">{cause}</dd>
        <dt className="text-ink-3">Done</dt>
        <dd className="break-words">
          {done.length === 0 ? "Nothing was changed." : `${done.join(". ")}.`}
        </dd>
        {notes.length > 0 && (
          <>
            <dt className="text-ink-3">Note</dt>
            <dd className="break-words">{notes.join(" ")}</dd>
          </>
        )}
      </dl>
    </Card>
  );
}

export function ReplyDraft({ draft }: { draft: string }) {
  const [said, setSaid] = useState<"" | "Copied" | "Could not copy">("");
  const copy = async () => {
    // A browser may refuse the clipboard, for example on a page that is not in front.
    const done = await navigator.clipboard.writeText(draft).then(
      () => true,
      () => false,
    );
    setSaid(done ? "Copied" : "Could not copy");
    setTimeout(() => setSaid(""), COPIED_MS);
  };
  return (
    <Card>
      <CardTitle>Reply to the customer</CardTitle>
      <p className="text-meta text-ink-3">A draft. You send it yourself.</p>
      <p className="rounded-control border border-line bg-raised px-2.5 py-2 text-[13.5px] break-words whitespace-pre-wrap">
        {draft}
      </p>
      <div>
        <Button onClick={copy}>
          <Copy {...SMALL} />
          {said === "" ? "Copy draft" : said}
        </Button>
        <span role="status" className="sr-only">
          {said === "Copied"
            ? "Draft copied"
            : said === ""
              ? ""
              : "The draft could not be copied. Select the text instead."}
        </span>
      </div>
    </Card>
  );
}

export function StoppedCard({ title, reason }: { title: string; reason: string }) {
  return (
    <Card tone="stopped">
      <CardTitle>
        <TriangleAlert {...ICON} />
        {title}
      </CardTitle>
      <p className="break-words">{reason}</p>
    </Card>
  );
}
