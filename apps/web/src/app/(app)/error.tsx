"use client";

import { Button } from "@/components/ui/button";

type Props = { error: Error & { digest?: string }; reset: () => void };

/** What failed, a way to try again, and the reference to quote when asking for help. */
export default function AppError({ error, reset }: Props) {
  return (
    <div role="alert" className="flex flex-1 flex-col items-start gap-2 p-5">
      <h1 className="text-title font-semibold">This page could not be shown</h1>
      <p className="text-ink-2">
        Something went wrong while showing this page. Nothing was changed by it.
      </p>
      {error.digest !== undefined && (
        <p className="font-mono text-meta text-ink-3">Reference {error.digest}</p>
      )}
      <Button onClick={reset}>Try again</Button>
    </div>
  );
}
