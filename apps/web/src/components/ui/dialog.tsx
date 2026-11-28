"use client";

import * as Primitive from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

type Props = {
  open: boolean;
  title: string;
  description: string;
  children: ReactNode;
};

/** A dialog the person must answer: it has no close button and does not close on a click outside. */
export function Dialog({ open, title, description, children }: Props) {
  return (
    <Primitive.Root open={open}>
      <Primitive.Portal>
        <Primitive.Overlay className="fixed inset-0 z-30 bg-ink/40" />
        <Primitive.Content
          onInteractOutside={(event) => event.preventDefault()}
          onEscapeKeyDown={(event) => event.preventDefault()}
          className="fixed top-1/2 left-1/2 z-40 flex w-[min(380px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 flex-col gap-3 rounded-card border border-line bg-surface p-4"
        >
          <Primitive.Title className="text-title font-semibold">{title}</Primitive.Title>
          <Primitive.Description className="text-ink-2">{description}</Primitive.Description>
          <div className="flex gap-2">{children}</div>
        </Primitive.Content>
      </Primitive.Portal>
    </Primitive.Root>
  );
}
