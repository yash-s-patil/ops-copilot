"use client";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { own } from "@/lib/own";

const NEEDS: Record<string, string> = {
  PERMISSION_DENIED: "Your role does not include this. Ask a supervisor if you need it.",
  NOT_FOUND: "Nothing here is visible to you.",
  RATE_LIMITED: "Too many requests just now. Wait a moment and try again.",
  BACKEND_UNAVAILABLE: "Meridian's systems are not answering right now.",
};

function words(error: unknown): string {
  if (!(error instanceof ApiError)) return "Something went wrong.";
  return own(NEEDS, error.code) ?? error.message;
}

/** What failed, in plain words, and a way to try again when trying again can help. */
export function PaneError({ error, retry }: { error: unknown; retry?: () => void }) {
  const final =
    error instanceof ApiError && ["PERMISSION_DENIED", "NOT_FOUND"].includes(error.code);
  return (
    <div role="alert" className="flex flex-col items-start gap-2 px-5 py-7 text-ink-2">
      <p>{words(error)}</p>
      {retry !== undefined && !final && <Button onClick={retry}>Try again</Button>}
    </div>
  );
}
