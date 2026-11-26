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

`make dev` serves the MCP server at `http://127.0.0.1:8000/mcp`, with `/healthz` and `/readyz` beside it. It publishes six read tools, three resources and two prompts. Sign-in is not built yet, so the running server recognises nobody and refuses every call; the tests exercise the tools as each kind of engineer.

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
