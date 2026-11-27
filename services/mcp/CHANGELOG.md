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
  repeated call finds that its action is over. Any `approval_token` is `APPROVAL_INVALID` until
  approvals are built.
- `POLICY_VIOLATION` carries `details.rule`, and for a refund `cap_minor` and `requested_minor`.
- `IDEMPOTENCY_CONFLICT` when an `idempotency_key` is reused with different arguments.
- Routes beside the MCP endpoint, with the same sign-in and the same error body: `GET /approvals`,
  `GET /approvals/{id}`, `POST /approvals/{id}/approve`, `POST /approvals/{id}/reject` and
  `POST /approvals/{id}/token`. Errors are answered with an HTTP status (401, 403, 404, 409,
  422, 429, 503) and the usual `{code, message, retryable, details}`.
