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
