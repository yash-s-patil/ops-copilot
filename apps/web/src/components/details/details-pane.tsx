"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Lock, X } from "lucide-react";
import type { ReactNode } from "react";
import { OrderStageBar } from "@/components/details/order-stage-bar";
import { StatusWord } from "@/components/inbox/status-word";
import { RefundProgress } from "@/components/thread/refund-progress";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { formatMoney, plural, sentenceCase } from "@/lib/format";
import { REFUND_WORDS } from "@/lib/refund-words";
import type { Customer, Note, Order, Ticket, Workspace } from "@/lib/schemas";
import { roleLabel, useSession } from "@/lib/session";

const CURRENCY = "INR";
const WITH_PAYMENTS = new Set(["approved", "sent_to_payments", "paid", "failed"]);

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2 border-b border-line px-4 py-3.5">
      <h3 className="text-label font-medium tracking-[0.04em] text-ink-3 uppercase">{label}</h3>
      {children}
    </section>
  );
}

function Pairs({ pairs }: { pairs: readonly (readonly [string, string])[] }) {
  return (
    <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3.5 gap-y-[3px] text-step">
      {pairs.map(([name, value]) => (
        <div key={name} className="contents">
          <dt className="text-ink-3">{name}</dt>
          <dd className="break-words">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function CustomerSection({ customer }: { customer: Customer | null }) {
  const { me } = useSession();
  if (customer === null) {
    return (
      <Section label="Customer">
        <p className="text-meta text-ink-3">The customer could not be loaded.</p>
      </Section>
    );
  }
  return (
    <Section label="Customer">
      <p>
        <b className="font-medium">{customer.display_name}</b>{" "}
        <span className="text-meta text-ink-3">· {customer.tier}</span>
      </p>
      <Pairs
        pairs={[
          ["Email", customer.email],
          ["Phone", customer.phone ?? "Not given"],
        ]}
      />
      {customer.contact_masked && (
        <p className="flex items-center gap-1.5 text-meta text-ink-3">
          <Lock size={13} strokeWidth={1.75} aria-hidden="true" />
          Contact details are masked for {roleLabel(me)}.
        </p>
      )}
    </Section>
  );
}

function statusLine(order: Order): string {
  if (order.status === "lost") return "Loss confirmed by carrier";
  return `${sentenceCase(order.status)} for ${plural(order.status_age_hours, "hour")}`;
}

export function OrderSection({ order, orderId }: { order: Order | null; orderId: number }) {
  if (order === null) {
    return (
      <Section label={`Order ${orderId}`}>
        <p className="text-meta text-ink-3">The order could not be loaded.</p>
      </Section>
    );
  }
  const last = order.events?.recent[0];
  return (
    <Section label={`Order ${orderId}`}>
      <OrderStageBar status={order.status} stuck={order.is_stuck} />
      <Pairs
        pairs={[
          ["Status", statusLine(order)],
          ["Amount", formatMoney(order.amount_minor, order.currency)],
          ["Last event", last === undefined ? "None recorded" : last.summary],
        ]}
      />
    </Section>
  );
}

function RetryPayment({ refundId }: { refundId: number }) {
  const client = useQueryClient();
  const retry = useMutation({
    mutationFn: () => api.retryRefund(refundId),
    onSuccess: () => client.invalidateQueries(),
  });
  return (
    <div className="flex flex-col items-start gap-1">
      <Button disabled={retry.isPending} onClick={() => retry.mutate()}>
        Retry payment
      </Button>
      {retry.isError && (
        <p role="alert" className="text-meta text-bad">
          {retry.error.message}
        </p>
      )}
    </div>
  );
}

export function RefundSection({ ticket }: { ticket: Ticket }) {
  const { can } = useSession();
  const { refund, recommendation } = ticket;
  if (refund === null) {
    const advice =
      recommendation === null || ticket.escalated_by_name === null
        ? "No refund on this order."
        : `Recommended by ${ticket.escalated_by_name}: ${formatMoney(recommendation.amount_minor, CURRENCY)}.`;
    return (
      <Section label="Refund">
        <p className="text-meta text-ink-3">{advice}</p>
      </Section>
    );
  }
  const { words, tone } = REFUND_WORDS[refund.state];
  return (
    <Section label="Refund">
      <p className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <b className="font-medium tabular-nums">
          {formatMoney(refund.amount_minor, refund.currency)}
        </b>
        <StatusWord tone={tone}>{words.replace("Refund ", "")}</StatusWord>
      </p>
      {WITH_PAYMENTS.has(refund.state) && <RefundProgress state={refund.state} />}
      {refund.state === "failed" && refund.refund_id !== null && can("refunds.request") && (
        <RetryPayment refundId={refund.refund_id} />
      )}
    </Section>
  );
}

function author(note: Note): string {
  if (note.author_kind === "agent_on_behalf") return `Copilot, for ${note.author_name}`;
  return note.author_kind === "customer" ? `${note.author_name} (customer)` : note.author_name;
}

export function TicketActivity({ notes }: { notes: readonly Note[] }) {
  return (
    <Section label="Ticket activity">
      {notes.length === 0 && <p className="text-meta text-ink-3">No notes yet.</p>}
      {notes.map((note) => (
        <div key={`${note.created_at}-${note.author_name}`} className="text-step text-ink-2">
          <b className="block text-fine font-medium text-ink-3">{author(note)}</b>
          <span className="break-words">{note.body}</span>
        </div>
      ))}
    </Section>
  );
}

type Props = { workspace: Workspace; onClose: () => void };

/** Everything here was read with the viewer's own permissions; it is not the assistant's view. */
export function DetailsPane({ workspace, onClose }: Props) {
  return (
    <>
      <header className="flex min-h-[52px] shrink-0 items-center gap-2 border-b border-line px-4 py-2">
        <h2 className="flex-1 font-semibold">Details</h2>
        <Button
          variant="ghost"
          square
          onClick={onClose}
          aria-label="Close details"
          className="lg:hidden"
        >
          <X size={16} strokeWidth={1.75} aria-hidden="true" />
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <CustomerSection customer={workspace.customer} />
        <OrderSection order={workspace.order} orderId={workspace.ticket.order_id} />
        <RefundSection ticket={workspace.ticket} />
        <TicketActivity notes={workspace.ticket.notes} />
      </div>
    </>
  );
}
