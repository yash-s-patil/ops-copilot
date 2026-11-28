import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import * as s from "@/lib/schemas";
import { RUN_ID, ticketSummary, workspace } from "@/test/fixtures";

const node = z.record(z.string(), z.unknown());
type Node = z.infer<typeof node>;
type Fill = "values" | "nulls";

const SNAPSHOT = new URL(
  "../../../../services/agent/tests/contract/snapshots/openapi.json",
  import.meta.url,
);
const published = z
  .object({ components: z.object({ schemas: z.record(z.string(), node) }) })
  .parse(JSON.parse(readFileSync(fileURLToPath(SNAPSHOT), "utf8"))).components.schemas;

function documented(name: string): Node {
  const found = published[name];
  if (found === undefined) throw new Error(`the agent's API document has no ${name}`);
  return found;
}

function text(schema: Node): string {
  if (schema["format"] === "uuid") return RUN_ID;
  return schema["format"] === "date-time" ? "2026-11-28T09:00:00Z" : "text";
}

function object(schema: Node, fill: Fill): Record<string, unknown> {
  const properties = z.record(z.string(), node).safeParse(schema["properties"]);
  if (!properties.success) return {};
  return Object.fromEntries(
    Object.entries(properties.data).map(([name, property]) => [name, example(property, fill)]),
  );
}

function oneOf(options: readonly Node[], fill: Fill): unknown {
  const nullable = options.some((option) => option["type"] === "null");
  if (nullable && fill === "nulls") return null;
  return example(options.find((option) => option["type"] !== "null") ?? {}, fill);
}

/** A value of the shape the agent documents, with every optional part filled or null. */
function example(schema: Node, fill: Fill): unknown {
  const ref = schema["$ref"];
  if (typeof ref === "string") return example(documented(ref.split("/").at(-1) ?? ""), fill);
  const options = z.array(node).safeParse(schema["anyOf"]);
  if (options.success) return oneOf(options.data, fill);
  if (schema["type"] === "string") return text(schema);
  if (schema["type"] === "integer") return 7;
  if (schema["type"] === "boolean") return true;
  if (schema["type"] === "array") {
    return fill === "nulls" ? [] : [example(node.parse(schema["items"]), fill)];
  }
  return object(schema, fill);
}

const CONTRACT = [
  ["Me", s.me],
  ["RunView", s.runView],
  ["RunList", s.runList],
  ["Started", s.started],
  ["Decided", s.decided],
  ["Executed", s.executed],
  ["Resolved", s.resolved],
  ["CallDetail", s.callDetail],
  ["AdminRun", s.adminRun],
  ["AdminRuns", s.adminRuns],
  ["Stopped", s.anything],
] as const;

describe("what the agent documents", () => {
  it.each(CONTRACT)("is accepted for %s with every field filled", (name, schema) => {
    expect(schema.safeParse(example(documented(name), "values")).success).toBe(true);
  });

  it.each(CONTRACT)("is accepted for %s with every optional field null", (name, schema) => {
    expect(schema.safeParse(example(documented(name), "nulls")).success).toBe(true);
  });

  it("is accepted for a run's trace once its events have a number, a type, a time and a payload", () => {
    const run = example(documented("AdminRun"), "values");
    const event = { seq: 1, type: "run.started", at: "2026-11-28T09:00:00Z", payload: {} };
    expect(s.adminRunDetail.safeParse({ run, events: [event] }).success).toBe(true);
    expect(s.adminRunDetail.safeParse({ run, events: [{}] }).success).toBe(false);
  });
});

describe("tickets", () => {
  const counts = { my_queue: 1, all_open: 4, waiting: 0, done: 12 };

  it("accepts an empty page and a page of one", () => {
    expect(s.ticketPage.parse({ items: [], next_cursor: null, counts }).items).toEqual([]);
    const page = { items: [ticketSummary()], next_cursor: "next", counts };
    expect(s.ticketPage.parse(page).items).toHaveLength(1);
  });

  it("refuses counts that leave out a view", () => {
    const partial = { my_queue: 1, all_open: 4 };
    expect(s.ticketPage.safeParse({ items: [], next_cursor: null, counts: partial }).success).toBe(
      false,
    );
  });

  it.each([
    ["a priority that does not exist", { priority: "P9" }],
    ["a refund state that does not exist", { refund_state: "lost" }],
    ["a ticket number written as text", { ticket_id: "2207" }],
    ["a missing customer name", { customer_name: undefined }],
  ])("refuses a ticket with %s", (_what, wrong) => {
    expect(s.ticketSummary.safeParse({ ...ticketSummary(), ...wrong }).success).toBe(false);
  });

  it("accepts a whole workspace, and one whose order and customer could not be read", () => {
    expect(s.workspace.safeParse(workspace()).success).toBe(true);
    expect(s.workspace.safeParse(workspace({ order: null, customer: null })).success).toBe(true);
  });

  it("refuses a workspace with no ticket or with notes by an unknown kind of author", () => {
    expect(s.workspace.safeParse({ ...workspace(), ticket: null }).success).toBe(false);
    const note = { author_kind: "robot", author_name: "x", body: "y", created_at: "z" };
    const odd = { ...workspace(), ticket: { ...workspace().ticket, notes: [note] } };
    expect(s.workspace.safeParse(odd).success).toBe(false);
  });

  it("accepts the short status of a ticket and refuses one without its version", () => {
    const status = { ticket_id: 2207, status: "open", queue: "L1", refund_state: "paid" };
    expect(s.ticketStatus.safeParse({ ...status, version: 4 }).success).toBe(true);
    expect(s.ticketStatus.safeParse(status).success).toBe(false);
  });
});

describe("answers that are not what they should be", () => {
  it.each([null, undefined, "text", 7, [], {}])("refuses %j as who is signed in", (body) => {
    expect(s.me.safeParse(body).success).toBe(false);
  });

  it("refuses a run whose result is not a result", () => {
    const run = example(documented("RunView"), "values");
    expect(s.runView.safeParse({ ...node.parse(run), result: "done" }).success).toBe(false);
  });

  it("drops fields it was not told about rather than passing them on", () => {
    const body = { run_id: RUN_ID, internal_note: "not for the page" };
    expect(s.started.parse(body)).toEqual({ run_id: RUN_ID });
  });
});

describe("an error body", () => {
  it("is read with empty details when it gives none", () => {
    expect(s.errorBody.parse({ code: "NOT_FOUND", message: "Nothing here." })).toEqual({
      code: "NOT_FOUND",
      message: "Nothing here.",
      details: {},
    });
  });

  it.each([{ message: "no code" }, { code: 404, message: "x" }, { code: "X" }, "Bad Gateway"])(
    "is refused when it is %j",
    (body) => {
      expect(s.errorBody.safeParse(body).success).toBe(false);
    },
  );
});
