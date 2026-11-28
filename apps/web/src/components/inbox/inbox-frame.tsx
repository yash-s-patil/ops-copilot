"use client";

import { useParams, useSearchParams } from "next/navigation";
import type { ReactNode } from "react";
import { TicketList } from "@/components/inbox/ticket-list";
import { view as viewSchema, type View } from "@/lib/schemas";

export function useView(): View {
  const parsed = viewSchema.safeParse(useSearchParams().get("view"));
  return parsed.success ? parsed.data : "my_queue";
}

export function useTicketId(): number | null {
  const raw = useParams<{ ticketId?: string }>().ticketId;
  const id = Number(raw);
  return raw !== undefined && Number.isInteger(id) && id > 0 ? id : null;
}

/** The list beside the open ticket. Below 720 px one of the two is shown at a time. */
export function InboxFrame({ children }: { children: ReactNode }) {
  const view = useView();
  const ticketId = useTicketId();
  return (
    <>
      <div
        className={`min-h-0 shrink-0 border-r border-line sm:w-[280px] md:w-[320px] lg:w-[340px] max-sm:w-full max-sm:border-r-0 ${ticketId === null ? "" : "max-sm:hidden"}`}
      >
        <TicketList view={view} ticketId={ticketId} />
      </div>
      <div
        className={`relative flex min-h-0 min-w-0 flex-1 ${ticketId === null ? "max-sm:hidden" : ""}`}
      >
        {children}
      </div>
    </>
  );
}
