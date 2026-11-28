"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useView } from "@/components/inbox/inbox-frame";
import { ticketsKey } from "@/components/inbox/ticket-list";
import { api } from "@/lib/api";

const ONE_PANE = "(max-width: 719px)";

/** At widths that show the list beside a ticket, the first ticket of the view is opened. */
export function FirstTicket() {
  const view = useView();
  const router = useRouter();
  const tickets = useQuery({ queryKey: ticketsKey(view), queryFn: () => api.tickets(view) });
  const first = tickets.data?.items[0]?.ticket_id;
  useEffect(() => {
    if (first !== undefined && !window.matchMedia(ONE_PANE).matches) {
      router.replace(`/inbox/${first}?view=${view}`);
    }
  }, [first, view, router]);
  return <p className="flex-1 px-5 py-7 text-center text-ink-3">Select a ticket from the list.</p>;
}
