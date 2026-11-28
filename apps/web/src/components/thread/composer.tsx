"use client";

import { ArrowUp, Square } from "lucide-react";
import { useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useSession } from "@/lib/session";

const ICON = { size: 16, strokeWidth: 1.75, "aria-hidden": true } as const;
const HINT =
  "Copilot reads orders, tickets, runbooks and policies with your permissions. It asks before it changes anything.";
const EMPTY = "Type a message first.";
const MAX_HEIGHT_PX = 120;

type Props = {
  placeholder: string;
  running: boolean;
  onSend: (message: string) => Promise<void>;
  onStop: () => void;
};

function grow(element: HTMLTextAreaElement): void {
  element.style.height = "auto";
  element.style.height = `${Math.min(element.scrollHeight, MAX_HEIGHT_PX)}px`;
}

function useDraft(onSend: Props["onSend"]) {
  const [draft, setDraft] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  const sending = useRef(false);
  const send = async () => {
    // A second Enter before the first is answered must not start a second investigation.
    if (sending.current) return;
    if (draft.trim() === "") {
      setProblem(EMPTY);
      return;
    }
    setProblem(null);
    sending.current = true;
    try {
      await onSend(draft);
      setDraft("");
      if (area.current) area.current.style.height = "auto";
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "That did not send. Try again.");
    } finally {
      sending.current = false;
    }
  };
  return { draft, setDraft, problem, area, send };
}

/** One text area that grows to five lines. Enter sends; Shift and Enter makes a new line. */
export function Composer({ placeholder, running, onSend, onStop }: Props) {
  const { limits } = useSession();
  const { draft, setDraft, problem, area, send } = useDraft(onSend);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void (running ? onStop() : send());
  };
  const onKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (!running) void send();
  };

  return (
    <div className="shrink-0 px-4 pt-1.5 pb-3 sm:px-5 sm:pt-2 sm:pb-3.5">
      <form
        onSubmit={submit}
        className="mx-auto flex max-w-[760px] items-end gap-2 rounded-composer border border-line-2 bg-surface py-1.5 pr-1.5 pl-3 focus-within:border-accent"
      >
        <textarea
          ref={area}
          rows={1}
          value={draft}
          maxLength={limits.messageChars}
          aria-label="Message"
          placeholder={placeholder}
          onChange={(event) => {
            setDraft(event.target.value);
            grow(event.target);
          }}
          onKeyDown={onKey}
          className="max-h-[120px] min-w-0 flex-1 resize-none bg-transparent py-[5px] text-chat leading-[1.45] outline-none placeholder:text-ink-3"
        />
        <button
          type="submit"
          aria-label={running ? "Stop" : "Send"}
          className="grid size-[30px] shrink-0 place-items-center rounded-control border border-accent bg-accent text-on-accent hover:border-accent-ink hover:bg-accent-ink pointer-coarse:size-11"
        >
          {running ? <Square {...ICON} /> : <ArrowUp {...ICON} />}
        </button>
      </form>
      <p
        role={problem === null ? undefined : "alert"}
        className={`mx-auto mt-1.5 max-w-[760px] text-fine ${problem === null ? "text-ink-3" : "text-bad"}`}
      >
        {problem ?? HINT}
      </p>
    </div>
  );
}

export function ConnectionLine({ text }: { text: string }) {
  return (
    <p role="status" className="mx-auto w-full max-w-[760px] px-5 pb-1 text-meta text-warn">
      {text}
    </p>
  );
}
