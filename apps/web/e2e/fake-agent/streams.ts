/** Server-sent events: one run for its owner, and the latest run for an administrator. */
import type { IncomingMessage, ServerResponse } from "node:http";
import { adminView, latestRun } from "./admin";
import { frame, openStream } from "./http";
import { world, type RunRec } from "./world";

const FINAL = new Set(["run.completed", "run.stopped", "run.failed"]);
const OVER = new Set(["completed", "stopped", "failed", "cancelled"]);
const LATEST_POLL_MS = 40;

function lastSeen(request: IncomingMessage): number {
  const seen = Number(request.headers["last-event-id"] ?? "0");
  return Number.isInteger(seen) && seen > 0 ? seen : 0;
}

/** The number of frames after which this connection is cut, when a test asked for that. */
function dropAfter(run: RunRec): number | null {
  const { drop_stream_after } = world.switches;
  if (drop_stream_after === null || OVER.has(run.status)) return null;
  world.switches.drop_stream_after = null;
  return drop_stream_after;
}

/**
 * The run's events after the one the browser last saw, then each new one as it happens.
 * The stream ends after the run's last event, as the real one does.
 */
export function followRun(run: RunRec, request: IncomingMessage, response: ServerResponse): void {
  let sent = lastSeen(request);
  let frames = 0;
  const drop = dropAfter(run);
  const leave = () => run.listeners.delete(push);
  function push(): void {
    for (const event of run.events.slice(sent)) {
      response.write(frame(event.type, event.data, event.seq));
      sent = event.seq;
      frames += 1;
      if (drop !== null && frames >= drop) {
        leave();
        response.destroy();
        return;
      }
      if (FINAL.has(event.type)) break;
    }
    if (OVER.has(run.status) && sent >= run.events.length) {
      leave();
      response.end();
    }
  }
  openStream(response);
  run.listeners.add(push);
  request.on("close", leave);
  push();
}

export function followLatest(request: IncomingMessage, response: ServerResponse): void {
  let following: string | null = null;
  let sent = 0;
  openStream(response);
  const poll = setInterval(() => {
    const latest = latestRun();
    if (latest === null) return;
    if (latest.run_id !== following) {
      following = latest.run_id;
      sent = 0;
      response.write(frame("run", { run_id: latest.run_id }));
    }
    for (const event of latest.events.slice(sent)) {
      response.write(frame(event.type, adminView(event), event.seq));
      sent = event.seq;
    }
  }, LATEST_POLL_MS);
  request.on("close", () => clearInterval(poll));
}
