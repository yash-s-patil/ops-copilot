"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { DetailsPane } from "@/components/details/details-pane";
import { useTicketId, useView } from "@/components/inbox/inbox-frame";
import { DetailsDrawer } from "@/components/shell/details-drawer";
import { PaneError } from "@/components/shell/pane-error";
import { Conversation } from "@/components/thread/conversation";
import { TicketHeader } from "@/components/thread/ticket-header";
import { SkeletonLines } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { paymentUnderWay } from "@/lib/refund-words";
import type { RefundState } from "@/lib/schemas";
import { useSession } from "@/lib/session";

/**
 * While a payment is under way the ticket is asked how it stands every few seconds, with
 * one small read, and the whole page is read again when that changes. It stops when the
 * refund is paid or failed, or after the longest time a payment is watched for.
 */
function useRefundProgress(ticketId: number, state: RefundState | null): void {
  const { limits } = useSession();
  const client = useQueryClient();
  const watching = paymentUnderWay(state);
  const since = useRef<number | null>(null);
  useEffect(() => {
    since.current = watching ? Date.now() : null;
  }, [watching]);
  const status = useQuery({
    queryKey: ["workspace", ticketId, "status"],
    queryFn: () => api.ticketStatus(ticketId),
    enabled: watching,
    refetchInterval: () =>
      since.current !== null && Date.now() - since.current < limits.refundPollMaxMs
        ? limits.refundPollMs
        : false,
  });
  const seen = status.data?.refund_state;
  useEffect(() => {
    if (seen !== undefined && seen !== state) void client.invalidateQueries();
  }, [seen, state, client]);
}

function Loaded({ ticketId }: { ticketId: number }) {
  const view = useView();
  const [details, setDetails] = useState(false);
  const workspace = useQuery({
    queryKey: ["workspace", ticketId],
    queryFn: () => api.workspace(ticketId),
  });
  useRefundProgress(ticketId, workspace.data?.ticket.refund?.state ?? null);
  if (workspace.isPending) {
    return (
      <div className="flex-1 p-5">
        <SkeletonLines widths={["w-1/3", "w-1/4", "w-2/3", "w-1/2", "w-3/5"]} />
      </div>
    );
  }
  // A refresh that fails leaves what is already on screen where it is.
  if (workspace.data === undefined) {
    return (
      <div className="flex-1">
        <PaneError error={workspace.error} retry={() => workspace.refetch()} />
      </div>
    );
  }
  return (
    <>
      <section aria-label="Ticket" className="flex min-h-0 min-w-0 flex-1 flex-col">
        <TicketHeader
          ticket={workspace.data.ticket}
          view={view}
          onDetails={() => setDetails((open) => !open)}
        />
        <Conversation key={ticketId} workspace={workspace.data} />
      </section>
      <DetailsDrawer open={details} onClose={() => setDetails(false)}>
        <DetailsPane workspace={workspace.data} onClose={() => setDetails(false)} />
      </DetailsDrawer>
    </>
  );
}

export function TicketWorkspace() {
  const ticketId = useTicketId();
  if (ticketId === null) {
    return (
      <p className="flex-1 px-5 py-7 text-center text-ink-3">Select a ticket from the list.</p>
    );
  }
  return <Loaded key={ticketId} ticketId={ticketId} />;
}
