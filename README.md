# Ops Copilot

A reference deployment of an MCP server and an agent on private Azure.

A support engineer opens a ticket; an agent investigates it across orders, customers,
policies and runbooks and proposes a fix. The agent holds no rights over business data:
it acts with the signed-in engineer's delegated identity, and every write needs a human
approval bound to the exact arguments.

**Status:** under construction. The repository currently holds the project foundation;
services arrive phase by phase.

## Quick start

Requires Docker, [uv](https://docs.astral.sh/uv/), Node 24 with pnpm, and GNU Make.

```
make install  # install the locked toolchain into .venv
make dev      # start local PostgreSQL and Azurite
make lint     # formatting, lint rules, secret scan on commit, strict type checking
make test     # Python unit tests with coverage
make test-web # web unit tests
```

Run `make help` for every target.

## Layout

| Path | Contents |
|---|---|
| `config/` | `limits.yaml` (every cap, timeout and threshold) and `pricing.yaml` |
| `packages/opscp_common/` | Code shared by the services |
| `apps/web/` | The web application (Next.js) |
| `.github/workflows/` | Continuous integration |

## Licence

Apache-2.0. See [LICENSE](LICENSE).
