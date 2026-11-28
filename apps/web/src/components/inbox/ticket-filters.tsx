"use client";

import Link from "next/link";
import { VIEWS, type TicketPage, type View } from "@/lib/schemas";

const LABELS: Record<View, string> = {
  my_queue: "My queue",
  all_open: "All open",
  waiting: "Waiting",
  done: "Done",
};

type Props = { view: View; counts: TicketPage["counts"] | null; ticketId: number | null };

/** The four views, each with its count from the same call that listed the tickets. */
export function TicketFilters({ view, counts, ticketId }: Props) {
  const base = ticketId === null ? "/inbox" : `/inbox/${ticketId}`;
  return (
    <nav
      aria-label="Filter"
      className="flex shrink-0 gap-0.5 overflow-x-auto border-b border-line px-3 py-2"
    >
      {VIEWS.map((option) => {
        const pressed = option === view;
        return (
          <Link
            key={option}
            href={`${base}?view=${option}`}
            aria-current={pressed ? "true" : undefined}
            className={`flex h-[26px] items-center gap-1 rounded-[5px] px-[9px] whitespace-nowrap ${
              pressed ? "bg-raised font-medium text-ink ring-1 ring-line" : "text-ink-3"
            }`}
          >
            {LABELS[option]}
            <span className="text-meta font-normal text-ink-3 tabular-nums">
              {counts?.[option] ?? ""}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
