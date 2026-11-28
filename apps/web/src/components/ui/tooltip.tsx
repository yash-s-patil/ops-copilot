"use client";

import * as Primitive from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

export const TooltipProvider = Primitive.Provider;

export function Tooltip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Primitive.Root>
      <Primitive.Trigger asChild>{children}</Primitive.Trigger>
      <Primitive.Portal>
        <Primitive.Content
          side="right"
          sideOffset={6}
          className="z-20 rounded-control border border-line bg-ink px-2 py-1 text-meta text-surface"
        >
          {label}
        </Primitive.Content>
      </Primitive.Portal>
    </Primitive.Root>
  );
}
