/**
 * The fake agent must answer in the shapes the application parses. Each answer is read
 * here with the application's own schemas, so the fake cannot drift from them unnoticed.
 */
import type { APIResponse } from "@playwright/test";
import type { z } from "zod";
import * as events from "@/lib/events";
import * as schemas from "@/lib/schemas";
import type { FakeAgent } from "./support/fake";
import { TICKETS } from "./support/story";
import { expect, test } from "./support/test";

const INVESTIGATE = "Investigate this ticket and propose a resolution.";

const EVENT_SCHEMAS: Record<string, z.ZodType> = {
  "tool.started": events.toolStarted,
  "tool.finished": events.toolFinished,
  "model.delta": events.modelDelta,
  "approval.required": events.approvalRequired,
  "approval.resolved": events.approvalResolved,
  "budget.updated": events.budgetUpdated,
  "run.paused": events.runPaused,
  "run.completed": events.runResult,
  "run.stopped": events.runStopped,
};

async function parsed<T extends z.ZodType>(response: APIResponse, schema: T): Promise<z.infer<T>> {
  expect(response.ok(), `${response.url()} answered ${response.status()}`).toBe(true);
  const result = schema.safeParse(await response.json());
  expect(result.error?.issues ?? [], response.url()).toEqual([]);
  return schema.parse(await response.json());
}

async function reaches(fake: FakeAgent, runId: string, status: string): Promise<void> {
  await expect
    .poll(async () => {
      const run = await parsed(await fake.get("arjun", `/api/runs/${runId}`), schemas.runView);
      return run.status;
    })
    .toBe(status);
}

type Frame = { id: number | null; event: string; data: unknown };

function frames(stream: string): Frame[] {
  return stream
    .split("\n\n")
    .filter((block) => block.trim() !== "")
    .map((block) => {
      const field = (name: string) =>
        block
          .split("\n")
          .find((line) => line.startsWith(`${name}: `))
          ?.slice(name.length + 2);
      const id = field("id");
      const data: unknown = JSON.parse(field("data") ?? "null");
      return { id: id === undefined ? null : Number(id), event: field("event") ?? "", data };
    });
}

test.skip(({ viewport }) => viewport?.width !== 1440, "No browser is used; once is enough.");

test("who is signed in, for each of the five people", async ({ fake }) => {
  for (const user of ["priya", "rohan", "arjun", "kavita", "sam"] as const) {
    await parsed(await fake.get(user, "/api/me"), schemas.me);
  }
});

test("every inbox view and the ticket workspace", async ({ fake }) => {
  await fake.reset("escalated", "waiting_for_supervisor");
  for (const user of ["priya", "arjun", "kavita"] as const) {
    for (const view of schemas.VIEWS) {
      await parsed(await fake.get(user, `/api/tickets?view=${view}`), schemas.ticketPage);
    }
    for (const ticket of [TICKETS.stuck, TICKETS.lost]) {
      const workspace = await parsed(
        await fake.get(user, `/api/tickets/${ticket}`),
        schemas.workspace,
      );
      expect(workspace.investigations.length).toBeGreaterThan(0);
      await parsed(
        await fake.get(user, `/api/tickets/${ticket}?part=status`),
        schemas.ticketStatus,
      );
    }
  }
  const waiting = await parsed(
    await fake.get("kavita", `/api/tickets/${TICKETS.lost}`),
    schemas.workspace,
  );
  expect(waiting.approvals).toHaveLength(1);
});

test("a run from start to end, its events, and the admin projection", async ({ fake }) => {
  const start = await fake.post("arjun", "/api/runs", {
    data: { message: INVESTIGATE, ticket_id: TICKETS.stuck },
  });
  const { run_id } = await parsed(start, schemas.started);
  await reaches(fake, run_id, "awaiting_approval");
  const detail = await parsed(
    await fake.get("sam", `/api/admin/runs/${run_id}`),
    schemas.adminRunDetail,
  );
  const asked = detail.events.filter((event) => event.type === "approval.required");
  for (const { payload } of asked) {
    const path = `/api/runs/${run_id}/actions/${String(payload["action_id"])}`;
    const decided = await fake.post("arjun", `${path}/approve`, {
      data: { args_hash: payload["args_hash"] },
    });
    await parsed(decided, schemas.decided);
  }
  await reaches(fake, run_id, "completed");
  const anAction = String(asked[0]?.payload["action_id"]);
  await parsed(await fake.post("arjun", `/api/approvals/${anAction}/execute`), schemas.executed);

  const stream = frames(await (await fake.get("arjun", `/api/runs/${run_id}/events`)).text());
  expect(stream.map((frame) => frame.id)).toEqual(stream.map((_, index) => index + 1));
  expect(stream.at(-1)?.event).toBe("run.completed");
  for (const frame of stream) {
    const issues = EVENT_SCHEMAS[frame.event]?.safeParse(frame.data).error?.issues ?? [];
    expect(issues, `event ${frame.id} ${frame.event}`).toEqual([]);
  }
  const resumed = await fake.get("arjun", `/api/runs/${run_id}/events`, { "Last-Event-ID": "5" });
  expect(frames(await resumed.text())[0]?.id).toBe(6);

  const list = await parsed(await fake.get("arjun", "/api/runs"), schemas.runList);
  expect(list.items.map((run) => run.run_id)).toContain(run_id);
  await parsed(await fake.get("arjun", "/api/runs?ask=true"), schemas.runList);
  await parsed(await fake.get("arjun", `/api/runs/${run_id}/calls/call_1`), schemas.callDetail);
  await parsed(await fake.get("sam", "/api/admin/runs"), schemas.adminRuns);
  await parsed(await fake.get("arjun", `/api/tickets/${TICKETS.stuck}`), schemas.workspace);
});

/** The name the web gives one click on Confirm resolve. */
const CLICK = "confirm-resolve-click-one";

test("resolving a ticket, and the one error body", async ({ fake }) => {
  const resolved = await fake.post("priya", `/api/tickets/${TICKETS.exception}/resolve`, {
    headers: { "Idempotency-Key": CLICK },
  });
  await parsed(resolved, schemas.resolved);
  for (const refused of [
    await fake.get("rohan", `/api/tickets/${TICKETS.stuck}`),
    await fake.get("sam", "/api/tickets?view=my_queue"),
    await fake.get("priya", "/api/admin/runs"),
  ]) {
    expect(refused.ok()).toBe(false);
    expect(schemas.errorBody.safeParse(await refused.json()).success).toBe(true);
  }
});
