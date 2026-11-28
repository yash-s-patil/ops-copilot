"use client";

import { useQuery } from "@tanstack/react-query";
import { useRef, type KeyboardEvent } from "react";
import { TicketFilters } from "@/components/inbox/ticket-filters";
import { TicketRow } from "@/components/inbox/ticket-row";
import { PaneError } from "@/components/shell/pane-error";
import { SkeletonLines } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { View } from "@/lib/schemas";
import { useTicketsAwaitingMe } from "@/lib/use-my-runs";
import { regionLabel, roleLabel, useSession } from "@/lib/session";

export const ticketsKey = (view: View) => ["tickets", view] as const;

function emptyLine(view: View, supervisor: boolean): string {
  if (view === "my_queue") {
    return supervisor ? "No refunds are waiting for your approval." : "Your queue is clear.";
  }
  if (view === "all_open") return "No open tickets in your region.";
  return view === "waiting" ? "Nothing is waiting on someone else." : "No resolved tickets yet.";
}

function moveFocus(event: KeyboardEvent<HTMLDivElement>, rows: HTMLElement | null): void {
  if (rows === null || (event.key !== "ArrowDown" && event.key !== "ArrowUp")) return;
  const links = [...rows.querySelectorAll<HTMLElement>("[data-ticket-row]")];
  const at = links.findIndex((link) => link === document.activeElement);
  const next = links[event.key === "ArrowDown" ? at + 1 : at - 1];
  if (next === undefined) return;
  event.preventDefault();
  next.focus();
}

function ListHeader() {
  const { me } = useSession();
  const region = regionLabel(me);
  const where = region === null ? "" : `${region.charAt(0).toUpperCase()}${region.slice(1)} · `;
  return (
    <header className="flex min-h-[52px] shrink-0 flex-col justify-center border-b border-line px-4 py-2">
      <h1 className="text-title font-semibold tracking-[-0.01em]">Inbox</h1>
      <p className="truncate text-meta text-ink-3">
        {where}
        {roleLabel(me)}
      </p>
    </header>
  );
}

type Props = { view: View; ticketId: number | null };

export function TicketList({ view, ticketId }: Props) {
  const { me, limits } = useSession();
  const rows = useRef<HTMLDivElement>(null);
  const tickets = useQuery({
    queryKey: ticketsKey(view),
    queryFn: () => api.tickets(view),
    refetchInterval: limits.inboxRefreshMs,
  });
  const awaitingMe = useTicketsAwaitingMe();
  // Ages are counted from when the list was read, so they do not shift between renders.
  const now = new Date(tickets.dataUpdatedAt);
  return (
    <section aria-label="Tickets" className="flex h-full min-h-0 flex-col">
      <ListHeader />
      <TicketFilters view={view} counts={tickets.data?.counts ?? null} ticketId={ticketId} />
      {/* The arrow keys move between rows; each row is itself a link. */}
      {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions */}
      <div
        ref={rows}
        onKeyDown={(event) => moveFocus(event, rows.current)}
        className="min-h-0 flex-1 overflow-y-auto"
      >
        {tickets.isPending && (
          <div className="p-4">
            <SkeletonLines widths={["w-3/4", "w-1/2", "w-2/3", "w-1/2", "w-3/5", "w-2/5"]} />
          </div>
        )}
        {tickets.isError && tickets.data === undefined && (
          <PaneError error={tickets.error} retry={() => tickets.refetch()} />
        )}
        {tickets.data?.items.map((ticket) => (
          <TicketRow
            key={ticket.ticket_id}
            ticket={ticket}
            view={view}
            selected={ticket.ticket_id === ticketId}
            awaitingMe={awaitingMe.has(ticket.ticket_id)}
            now={now}
          />
        ))}
        {tickets.data?.items.length === 0 && (
          <p className="px-5 py-7 text-center text-ink-3">
            {emptyLine(view, me.roles.includes("Support.Supervisor"))}
          </p>
        )}
      </div>
    </section>
  );
}
