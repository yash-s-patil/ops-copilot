"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useSession } from "@/lib/session";

type Props = { onReject: (reason: string) => void; onBack: () => void };

/** A rejection is recorded with its reason, so one is asked for before it is sent. */
export function RejectForm({ onReject, onBack }: Props) {
  const { limits } = useSession();
  const [reason, setReason] = useState("");
  return (
    <form
      className="flex flex-wrap items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (reason.trim() !== "") onReject(reason.trim());
      }}
    >
      <input
        // The person has just chosen to reject; the reason is the next thing they type.
        // eslint-disable-next-line jsx-a11y/no-autofocus
        autoFocus
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        maxLength={limits.reasonChars}
        aria-label="Reason for rejecting"
        placeholder="Why are you rejecting this?"
        className="h-[30px] min-w-0 flex-1 rounded-control border border-line-2 bg-surface px-2.5"
      />
      <Button type="submit" disabled={reason.trim() === ""}>
        Reject
      </Button>
      <Button variant="ghost" onClick={onBack}>
        Back
      </Button>
    </form>
  );
}
