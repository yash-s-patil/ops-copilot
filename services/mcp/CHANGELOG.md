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
