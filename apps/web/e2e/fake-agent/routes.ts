/** The agent's routes, as the web application calls them. */
import type { IncomingMessage, ServerResponse } from "node:http";
import { adminRun, adminRuns, adminView, onlyAdmin } from "./admin";
import { invalid, notFound, type Refusal } from "./http";
import type { Person } from "./people";
import {
  callDetail,
  cancel,
  decideAsSupervisor,
  decideOwn,
  execute,
  nudge,
  ownRun,
  runsOf,
  startRun,
  viewOf,
} from "./runs";
import { followLatest, followRun } from "./streams";
import { inbox, isView, resolve, retryRefund, statusOf, workspace } from "./tickets";
import { world, type Json } from "./world";

export const STREAMED = Symbol("streamed");

export type Call = {
  who: Person;
  ids: string[];
  query: URLSearchParams;
  body: Json;
  request: IncomingMessage;
  response: ServerResponse;
};

type Route = {
  method: "GET" | "POST";
  path: RegExp;
  status?: number;
  run: (call: Call) => unknown;
};

const ID = "([0-9]{1,16})";
const UUID = "([0-9a-f-]{36})";

function text(body: Json, name: string): string | null {
  const value = body[name];
  return typeof value === "string" ? value : null;
}

function id(call: Call, at: number): string {
  return call.ids[at] ?? "";
}

function started({ who, body }: Call) {
  const message = text(body, "message");
  const ticketId = body["ticket_id"];
  if (message === null || (ticketId !== undefined && typeof ticketId !== "number")) {
    throw invalid("The request does not match what this route accepts.");
  }
  return { run_id: startRun(who, message, ticketId ?? null).run_id };
}

function decision(call: Call, decided: "approved" | "rejected") {
  return {
    decision: decided,
    argsHash: text(call.body, "args_hash"),
    reason: text(call.body, "reason"),
  };
}

function adminDetail({ who, ids }: Call) {
  onlyAdmin(who);
  const run = world.runs.get(ids[0] ?? "");
  if (run === undefined) throw notFound("No run with that id.");
  return { run: adminRun(run), events: run.events.map(adminView) };
}

function ticketOrStatus(call: Call) {
  const ticketId = Number(id(call, 0));
  return call.query.get("part") === "status"
    ? statusOf(call.who, ticketId)
    : workspace(call.who, ticketId);
}

function tickets({ who, query }: Call) {
  const view = query.get("view") ?? "my_queue";
  if (!isView(view)) throw invalid("No view by that name.");
  return inbox(who, view);
}

const get = (path: string, run: Route["run"]): Route => ({
  method: "GET",
  path: new RegExp(`^/api/${path}$`),
  run,
});
const post = (path: string, run: Route["run"], status?: number): Route => ({
  method: "POST",
  path: new RegExp(`^/api/${path}$`),
  status,
  run,
});

const ROUTES: readonly Route[] = [
  get("me", ({ who }) => {
    const { key, ...me } = who;
    void key;
    return me;
  }),
  get("tickets", tickets),
  get(`tickets/${ID}`, ticketOrStatus),
  post(`tickets/${ID}/resolve`, (call) => {
    const click = call.request.headers["idempotency-key"];
    return resolve(call.who, Number(id(call, 0)), typeof click === "string" ? click : null);
  }),
  post(`refunds/${ID}/retry`, (call) => retryRefund(call.who, Number(id(call, 0)))),
  post("runs", started, 202),
  get("runs", ({ who, query }) => ({ items: runsOf(who, query.get("ask") === "true") })),
  // Reading a run lets it go on, as it does on the real agent.
  get(`runs/${UUID}`, (call) => {
    const run = ownRun(call.who, id(call, 0));
    nudge(run);
    return viewOf(run);
  }),
  get(`runs/${UUID}/events`, (call) => {
    const run = ownRun(call.who, id(call, 0));
    nudge(run);
    followRun(run, call.request, call.response);
    return STREAMED;
  }),
  get(`runs/${UUID}/calls/([A-Za-z0-9_-]{1,64})`, (call) =>
    callDetail(call.who, id(call, 0), id(call, 1)),
  ),
  post(`runs/${UUID}/actions/${UUID}/approve`, (call) =>
    decideOwn(call.who, id(call, 0), id(call, 1), decision(call, "approved")),
  ),
  post(`runs/${UUID}/actions/${UUID}/reject`, (call) =>
    decideOwn(call.who, id(call, 0), id(call, 1), decision(call, "rejected")),
  ),
  post(`runs/${UUID}/cancel`, (call) => cancel(call.who, id(call, 0))),
  post(`approvals/${UUID}/approve`, (call) =>
    decideAsSupervisor(call.who, id(call, 0), decision(call, "approved")),
  ),
  post(`approvals/${UUID}/reject`, (call) =>
    decideAsSupervisor(call.who, id(call, 0), decision(call, "rejected")),
  ),
  post(`approvals/${UUID}/execute`, (call) => execute(call.who, id(call, 0))),
  get("admin/runs", ({ who }) => adminRuns(who)),
  get("admin/runs/latest/events", (call) => {
    onlyAdmin(call.who);
    followLatest(call.request, call.response);
    return STREAMED;
  }),
  get(`admin/runs/${UUID}`, adminDetail),
];

export type Found = { route: Route; ids: string[] };

export function routeFor(method: string, path: string): Found | null {
  for (const route of ROUTES) {
    const match = route.method === method ? route.path.exec(path) : null;
    if (match !== null) return { route, ids: match.slice(1) };
  }
  return null;
}

export type { Refusal };
