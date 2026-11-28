"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, PanelRight } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import { StatusWord } from "@/components/inbox/status-word";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import { refundInFlight } from "@/lib/refund-words";
import type { Ticket, View } from "@/lib/schemas";
import { useSession } from "@/lib/session";

const ICON = { size: 16, strokeWidth: 1.75, "aria-hidden": true } as const;
const OPEN = new Set(["open", "pending_customer"]);

function clickId(): string {
  return crypto.randomUUID().replaceAll("-", "");
}

function ResolveButton({ ticket }: { ticket: Ticket }) {
  const client = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  // One key for one confirmation, kept if the request must be made again.
  const click = useRef(clickId());
  const resolve = useMutation({
    mutationFn: () => api.resolve(ticket.ticket_id, click.current),
    onSuccess: () => {
      click.current = clickId();
      setConfirming(false);
      return client.invalidateQueries();
    },
  });
  if (!confirming) return <Button onClick={() => setConfirming(true)}>Resolve</Button>;
  return (
    <span className="flex items-center gap-2">
      {resolve.isError && (
        <span role="alert" className="hidden text-meta text-bad md:inline">
          {resolve.error instanceof ApiError ? resolve.error.message : "That did not go through."}
        </span>
      )}
      <Button disabled={resolve.isPending} onClick={() => resolve.mutate()}>
        Confirm resolve
      </Button>
    </span>
  );
}

type Props = { ticket: Ticket; view: View; onDetails: () => void };

export function TicketHeader({ ticket, view, onDetails }: Props) {
  const { can } = useSession();
  const mayResolve =
    OPEN.has(ticket.status) &&
    can("tickets.write") &&
    !refundInFlight(ticket.refund?.state ?? null);
  return (
    <header className="flex min-h-[52px] shrink-0 items-center gap-2 border-b border-line px-4 py-2">
      <Link
        href={`/inbox?view=${view}`}
        aria-label="Back to list"
        className="grid size-[30px] shrink-0 place-items-center rounded-control text-ink-2 hover:bg-raised sm:hidden pointer-coarse:size-11"
      >
        <ChevronLeft {...ICON} />
      </Link>
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-title font-semibold tracking-[-0.01em]">{ticket.summary}</h1>
        <p className="truncate text-meta text-ink-3">
          #{ticket.ticket_id} · Order {ticket.order_id} · {ticket.priority} · {ticket.queue} queue
        </p>
      </div>
      {mayResolve && <ResolveButton ticket={ticket} />}
      {ticket.status === "resolved" && <StatusWord tone="ok">Resolved</StatusWord>}
      <Button square onClick={onDetails} aria-label="Ticket details" className="lg:hidden">
        <PanelRight {...ICON} />
      </Button>
    </header>
  );
}
