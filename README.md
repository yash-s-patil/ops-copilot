# Ops Copilot

A reference deployment of an MCP server and an agent on private Azure.

A support engineer opens a ticket; an agent investigates it across orders, customers,
policies and runbooks and proposes a fix. The agent holds no rights over business data:
it acts with the signed-in engineer's delegated identity, and every write needs a human
approval bound to the exact arguments.

**Status:** under construction. The repository currently holds the project foundation;
services arrive phase by phase.

## Quick start

Requires Docker, [uv](https://docs.astral.sh/uv/), Node 24 with pnpm, Terraform 1.17, [tflint](https://github.com/terraform-linters/tflint) and GNU Make.

```
make install  # install the locked toolchain into .venv
make dev      # build and start local PostgreSQL, Azurite and the MCP server
make lint     # formatting, lint rules, secret scan on commit, strict type checking
make migrate  # apply database migrations locally
make seed     # load the deterministic demo data and upload the policy documents (replaces what is there)
make test     # Python unit tests with coverage
make test-integration  # tests against local PostgreSQL and Azurite
make test-web # web unit tests
```

Run `make help` for every target. After pulling a change to the local database or storage setup, run `make dev-reset`.

`make dev` serves the MCP server at `http://127.0.0.1:8000/mcp`, with `/healthz` and `/readyz` beside it. It publishes six read tools, two write tools, four resources and two prompts. Every request needs a bearer token issued for this server; without one the answer is `401` and a pointer to `/.well-known/oauth-protected-resource/mcp`. Locally the sign-in service is replaced by a signing key that `make env` generates into `.env`: `make token WHO=priya` prints a token for one of the five demo users (`priya`, `rohan`, `arjun`, `kavita`, `sam`), and `make smoke` checks that each is shown only their own tools. A server started with that local sign-in refuses to run outside `ENVIRONMENT=local`.

Every tool call that reaches a tool, whether it succeeds, fails or is refused for lack of permission, appends one row to `audit.audit_log` and writes one log line with the same fields. Arguments are recorded with email addresses and phone numbers replaced by a hash and other text cut to 200 characters. Each row carries the hash of the row before it, so a changed or removed row is detectable: `make verify-audit` checks the chain. If the row cannot be written, the caller gets an error and not the result. Each person may make 60 requests a minute with a burst of 20; the next one is answered `RATE_LIMITED` with the seconds to wait, and is logged but not added to the audit log.

The two write tools, `update_ticket` and `issue_refund`, change nothing when called. A call is checked (input, permission, and for a refund the refund policy in `services/refund_policy.py`), recorded as a pending action, and answered with `approval_required`, the action's id and the hash of its exact arguments. A refund up to INR 5,000 waits 15 minutes for its requester's confirmation; a larger one waits 24 hours for a supervisor. Repeating a call with the same `idempotency_key` returns the same action; the same key with different arguments is `IDEMPOTENCY_CONFLICT`. A person decides through routes beside the MCP endpoint, which no tool can reach: `GET /approvals` and `GET /approvals/{id}` show what waits, `POST /approvals/{id}/approve` takes the `args_hash` the person was shown, `POST /approvals/{id}/reject` takes a reason, and `POST /approvals/{id}/token` gives the requester a fresh token for an approved action. They need the same bearer token as `/mcp`. A refund at or under the limit is confirmed by its requester; a larger one by a supervisor who is not the requester, and that approval hands nobody a token until the requester asks for one. An approval token is a signed ES256 token naming the requester, the approver, the action and the hash of its arguments; it lasts 120 seconds. Locally it is signed with a key that `make env` generates; in Azure the key stays in Key Vault. To carry an approved action out, the agent calls the same tool again with the same arguments, the same `idempotency_key` and the token. In one database transaction the server takes the action (so a second call cannot), records the token's id as used, checks the rules again as they stand at that moment, writes the change and writes the audit row. A refund is written together with a row in the outbox, the queue from which a separate payments worker will pay it; this server never calls a payment service. If the rules now refuse the write, nothing is changed and the action is marked failed. Repeating a call that was carried out returns the stored result and writes nothing.

Every call to PostgreSQL or Blob storage goes through a guard for that backend. It allows at most 20 calls in flight, gives each attempt a time limit, and retries a read up to twice, with a random and growing pause, when the failure is a lost connection, a `429` or a `5xx`. A write is never retried. After 5 failures in 30 seconds the guard's circuit breaker opens: calls are refused at once with `BACKEND_UNAVAILABLE` for 20 seconds, then one probe is let through, and its success closes the breaker. The breaker is kept per process, so each replica learns for itself. The resource `meridian://server/health`, readable by anyone with a role, reports each backend as `ok`, `recovering` or `unavailable` and lists the tools that cannot be relied on.

## Layout

| Path | Contents |
|---|---|
| `config/` | `limits.yaml` (every cap, timeout and threshold) and `pricing.yaml` |
| `packages/opscp_common/` | Code shared by the services |
| `services/mcp/` | The MCP server: migrations, the read tools, resources and prompts, and its image |
| `jobs/seed/` | Loads the demo data and uploads the policy documents |
| `data/` | The runbooks and policy documents the assistant reads |
| `apps/web/` | The web application (Next.js) |
| `infra/terraform/state-bootstrap/` | One-off storage account for Terraform state |
| `.github/workflows/` | Continuous integration |

## Licence

Apache-2.0. See [LICENSE](LICENSE).
