"use client";

import { useEffect, useReducer } from "react";
import { api, ApiError, eventsUrl } from "@/lib/api";
import { FINAL_EVENTS, STREAMED_EVENTS, type RunEvent } from "@/lib/events";

export type Connection = "connecting" | "open" | "lost" | "ended" | "signed_out";

type State = { events: RunEvent[]; connection: Connection };

type Change = { kind: "event"; event: RunEvent } | { kind: "connection"; connection: Connection };

function reduce(state: State, change: Change): State {
  if (change.kind === "connection") return { ...state, connection: change.connection };
  const last = state.events.at(-1)?.seq ?? 0;
  // A reconnect replays from the last number the browser acknowledged; anything at or
  // below what is already held has been seen.
  if (change.event.seq <= last) return state;
  return { events: [...state.events, change.event], connection: "open" };
}

function parsed(message: MessageEvent<string>): RunEvent | null {
  const seq = Number(message.lastEventId);
  if (!Number.isInteger(seq)) return null;
  try {
    const data: unknown = JSON.parse(message.data);
    return { seq, type: message.type, data };
  } catch {
    return null;
  }
}

const FIRST_WAIT_MS = 1000;

/**
 * Follows one run. The browser's event source reconnects by itself after a dropped
 * connection and names the last event it saw. An answer that is not a stream, such as
 * "too many requests", closes it for good, so it is opened again here after a pause that
 * doubles up to `reconnectMaxMs`; events already held are skipped when they come again.
 * When the run cannot be read either, a lost connection is told from a session that ended.
 */
export function useRunEvents(runId: string | null, reconnectMaxMs: number): State {
  const [state, dispatch] = useReducer(reduce, { events: [], connection: "connecting" });

  useEffect(() => {
    if (runId === null) return;
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let waitMs = FIRST_WAIT_MS;
    let done = false;
    const finish = (connection: Connection) => {
      done = true;
      source?.close();
      dispatch({ kind: "connection", connection });
    };
    const onEvent = (message: MessageEvent<string>) => {
      const event = parsed(message);
      if (event === null) return;
      dispatch({ kind: "event", event });
      if (FINAL_EVENTS.has(event.type)) finish("ended");
    };
    const open = () => {
      source = new EventSource(eventsUrl(runId));
      for (const type of STREAMED_EVENTS) source.addEventListener(type, onEvent);
      source.onopen = () => {
        waitMs = FIRST_WAIT_MS;
        dispatch({ kind: "connection", connection: "open" });
      };
      source.onerror = () => {
        dispatch({ kind: "connection", connection: "lost" });
        api.run(runId).catch((error: unknown) => {
          if (error instanceof ApiError && error.signedOut) finish("signed_out");
        });
        if (source?.readyState !== EventSource.CLOSED) return;
        timer = setTimeout(() => {
          if (!done) open();
        }, waitMs);
        waitMs = Math.min(waitMs * 2, reconnectMaxMs);
      };
    };
    open();
    return () => {
      done = true;
      if (timer !== null) clearTimeout(timer);
      source?.close();
    };
  }, [runId, reconnectMaxMs]);

  return state;
}
