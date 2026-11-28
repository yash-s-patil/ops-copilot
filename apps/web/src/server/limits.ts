import "server-only";
import { readFileSync } from "node:fs";
import { parse } from "yaml";
import { z } from "zod";
import type { UiLimits } from "@/lib/session";
import { settings } from "@/server/settings";

const MS_PER_S = 1000;

const file = z.object({
  ui: z.object({
    inbox_refresh_s: z.number().positive(),
    refund_poll_s: z.number().positive(),
    refund_poll_max_duration_s: z.number().positive(),
    retries: z.number().int().positive(),
    retry_wait_ms: z.number().positive(),
    busy_retries: z.number().int().positive(),
    busy_wait_s: z.number().positive(),
    reconnect_max_s: z.number().positive(),
  }),
  runs: z.object({ body_bytes_max: z.number().positive() }),
  user: z.object({ message_chars: z.number().positive() }),
  approval: z.object({ decision_reason_chars: z.number().positive() }),
  budget: z.object({ tokens: z.number().positive(), tool_calls: z.number().positive() }),
});

let read: z.infer<typeof file> | null = null;

/** The same limits file every service reads. No interval or size is written into the code. */
function limits(): z.infer<typeof file> {
  read ??= file.parse(parse(readFileSync(settings().LIMITS_FILE, "utf8")));
  return read;
}

export function uiLimits(): UiLimits {
  const { ui, user, approval, budget } = limits();
  return {
    inboxRefreshMs: ui.inbox_refresh_s * MS_PER_S,
    refundPollMs: ui.refund_poll_s * MS_PER_S,
    refundPollMaxMs: ui.refund_poll_max_duration_s * MS_PER_S,
    messageChars: user.message_chars,
    reasonChars: approval.decision_reason_chars,
    runTokens: budget.tokens,
    runToolCalls: budget.tool_calls,
    retries: ui.retries,
    retryWaitMs: ui.retry_wait_ms,
    busyRetries: ui.busy_retries,
    busyWaitMs: ui.busy_wait_s * MS_PER_S,
    reconnectMaxMs: ui.reconnect_max_s * MS_PER_S,
  };
}

export function bodyBytesMax(): number {
  return limits().runs.body_bytes_max;
}
