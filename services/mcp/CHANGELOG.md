# Interface changelog

Changes to what a connected agent can see or call: tools and their schemas, resources, prompts and
error bodies. Each entry goes with an update to the snapshots in `tests/contract/snapshots/`.

## Unreleased

### Added

- Tools `lookup_customer`, `get_order`, `list_tickets`, `get_ticket`, `search_runbooks` and
  `get_policy_document`. All are read-only. Every result is `{data, provenance}`.
- Resources `meridian://reference/order-lifecycle`, `meridian://policy/refund/summary` and
  `meridian://runbooks/index`. A read reports the text's version under `opscp/version`.
- Prompts `investigate_order` and `draft_customer_reply`.
- One error body for every failure: `{code, message, retryable, details}`, returned as the text
  of a result with `isError` set.
- Sign-in. The MCP endpoint answers `401` with a `WWW-Authenticate` header unless the request
  carries a bearer token issued for this server with the scope `Mcp.Access`. Where to get one is
  published at `/.well-known/oauth-protected-resource/mcp`.
- `tools/list`, `resources/list` and `prompts/list` show a caller only what their role may use.
  A call to something hidden is still answered `PERMISSION_DENIED`.
- A request that carries an `Origin` header is answered `403`: no web page is a caller.
- Rate limit. A tool call, resource read or prompt beyond 60 a minute per person (burst 20) is
  answered `RATE_LIMITED` with `details.retry_after_seconds`.
- A tool call fails with `INTERNAL` or `TIMEOUT` when its audit row cannot be written; the
  result is not returned.
- Resource `meridian://server/health`: JSON `{backends, degraded}`. `backends` gives each of
  `postgres` and `blob` as `ok`, `recovering` or `unavailable`; `degraded` lists the tools that
  rely on a backend that is not `ok`. Readable with any role, and not counted by the rate limit.
- A backend that fails repeatedly is answered for with `BACKEND_UNAVAILABLE` at once, without
  being called, until it recovers. The message of `BACKEND_UNAVAILABLE` no longer names the
  policy store, and a refusal from the store that waiting will not change is now `INTERNAL`.
- Tools `update_ticket` (needs `tickets.write`) and `issue_refund` (needs `refunds.request`).
  Called without `approval_token` they record a pending action and return `{status:
  "approval_required", action_id, args_hash, approval_kind, summary, expires_at}` inside the
  usual `{data, provenance}`. `status` is `executed`, `rejected`, `expired` or `failed` when a
  repeated call finds that its action is over.
- `POLICY_VIOLATION` carries `details.rule`, and for a refund `cap_minor` and `requested_minor`.
- `IDEMPOTENCY_CONFLICT` when an `idempotency_key` is reused with different arguments.
- Routes beside the MCP endpoint, with the same sign-in and the same error body: `GET /approvals`,
  `GET /approvals/{id}`, `POST /approvals/{id}/approve`, `POST /approvals/{id}/reject` and
  `POST /approvals/{id}/token`. Errors are answered with an HTTP status (401, 403, 404, 409,
  422, 429, 503) and the usual `{code, message, retryable, details}`.
- A write tool called again with the same arguments, the same `idempotency_key` and a valid
  `approval_token` carries the action out, once. The answer has `status: "executed"` and a new
  field `result`: `{refund_id, status, amount_minor}` for a refund, `{ticket_id, status, queue,
  version}` for a ticket. `result` is `null` in every other answer. Repeating the call returns
  the same answer and writes nothing, also when the repeat arrives while the first call is
  still running.
- A token that is forged, expired, already used, for other arguments, or for another person or
  action is `APPROVAL_INVALID`, always with the same message.
- A write the rules no longer allow when it is carried out is `POLICY_VIOLATION` with
  `details.rule` (also `ticket_changed`, `needs_supervisor`); its action becomes `failed`.
  Repeating that call is answered with `status: "failed"`; a new attempt needs a new key.
- Route `POST /refunds/{id}/retry` beside the MCP endpoint, with the same sign-in and error body.
  It needs `refunds.request`. It answers `{refund_id, status: "approved"}`, `NOT_FOUND` for a
  refund the caller cannot see, and `POLICY_VIOLATION` with `details.rule` `refund_not_failed`,
  `request_waiting`, `exceeds_remaining`, `goodwill_already_given` or `needs_supervisor`. It is
  not a tool.
- `get_ticket` and `list_tickets` give `refund.failure_reason` only while the refund's state is
  `failed`. The reason is a code: the provider's own (`provider_declined`), or
  `payment_service_unavailable`.
- `get_order` and the refund policy count a refund that failed with
  `payment_service_unavailable` in `refunded_minor`, because its payment may yet have been
  made. A refund the provider declined is still left out.
