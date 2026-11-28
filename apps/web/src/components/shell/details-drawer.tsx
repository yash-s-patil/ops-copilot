"use client";

import { useEffect, type ReactNode } from "react";

type Props = { open: boolean; onClose: () => void; children: ReactNode };

/**
 * A fourth pane at desktop widths. Below 1280 it lies over the right edge when opened,
 * which is the only place in the product where a shadow is used.
 */
export function DetailsDrawer({ open, onClose, children }: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  return (
    <aside
      aria-label="Ticket details"
      className={`min-h-0 flex-col border-l border-line bg-surface lg:flex lg:w-[312px] lg:shrink-0 max-lg:absolute max-lg:inset-y-0 max-lg:right-0 max-lg:z-10 max-lg:w-[min(340px,92%)] max-lg:shadow-drawer max-sm:w-full ${open ? "flex" : "hidden"}`}
    >
      {children}
    </aside>
  );
}
