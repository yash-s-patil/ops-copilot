"use client";

import Link from "next/link";
import { PriorityGlyph } from "@/components/inbox/priority-glyph";
import { StatusWord, type Tone } from "@/components/inbox/status-word";
import { formatAge } from "@/lib/format";
import { REFUND_WORDS, withSupervisor } from "@/lib/refund-words";
import type { TicketSummary, View } from "@/lib/schemas";

type Props = {
  ticket: TicketSummary;
  view: View;
  selected: boolean;
  /** An investigation of this person's has changes waiting for their own decision. */
  awaitingMe: boolean;
  now: Date;
};

function firstName(name: string): string {
  return name.split(" ")[0] ?? name;
}

type Word = { text: string; tone?: Tone };

function wordsFor(ticket: TicketSummary, awaitingMe: boolean): Word[] {
  const words: Word[] = [];
  const refund = ticket.refund_state === null ? null : REFUND_WORDS[ticket.refund_state];
  if (refund !== null) words.push({ text: refund.words, tone: refund.tone });
  else if (ticket.has_recommendation) words.push({ text: "Refund recommended", tone: "accent" });
  if (ticket.escalated_by_name !== null) {
    words.push({ text: `Escalated by ${firstName(ticket.escalated_by_name)}` });
  }
  // A refund that waits for a supervisor waits for someone other than its requester.
  const mine = awaitingMe && !withSupervisor(ticket.refund_state);
  if (ticket.needs_my_approval || mine) {
    words.push({ text: "Needs your approval", tone: "accent" });
  }
  return words;
}

function StatusWords({ words }: { words: readonly Word[] }) {
  if (words.length === 0) return null;
  return (
    <span className="col-span-2 col-start-2 mt-[3px] flex flex-wrap gap-x-3 gap-y-[3px]">
      {words.map((word) => (
        <StatusWord key={word.text} tone={word.tone}>
          {word.text}
        </StatusWord>
      ))}
    </span>
  );
}

/** Three lines: what and how old; which ticket, order and customer; then any state words. */
export function TicketRow({ ticket, view, selected, awaitingMe, now }: Props) {
  const state = selected
    ? "border-l-accent bg-accent-wash"
    : "border-l-transparent hover:bg-raised";
  return (
    <Link
      href={`/inbox/${ticket.ticket_id}?view=${view}`}
      aria-current={selected ? "true" : undefined}
      data-ticket-row
      className={`grid grid-cols-[auto_minmax(0,1fr)_auto] gap-x-[9px] gap-y-0.5 border-b border-l-2 border-b-line py-[11px] pr-4 pl-3.5 ${state}`}
    >
      <PriorityGlyph priority={ticket.priority} />
      <span className="truncate font-medium">{ticket.summary}</span>
      <span className="text-meta text-ink-3 tabular-nums">
        {formatAge(new Date(ticket.updated_at), now)}
      </span>
      <span className="col-span-2 col-start-2 truncate text-meta text-ink-3">
        #{ticket.ticket_id} · Order {ticket.order_id} · {ticket.customer_name} ·{" "}
        {ticket.customer_tier}
      </span>
      <StatusWords words={wordsFor(ticket, awaitingMe)} />
    </Link>
  );
}
