# Build from the repository root: docker build --file infra/local/issuer/Dockerfile .
# For docker compose on a developer's machine only. No workflow pushes this image.

FROM python:3.13-slim-bookworm@sha256:f0fa218d5b06f9b9235024fa061cde2cb141f726de52134471053d2d9b5324a3 AS build

# uv 0.10.4
COPY --from=ghcr.io/astral-sh/uv@sha256:4cac394b6b72846f8a85a7a0e577c6d61d4e17fe2ccee65d9451a8b3c9efb4ac /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY jobs/seed/pyproject.toml jobs/seed/pyproject.toml
COPY services/mcp/pyproject.toml services/mcp/pyproject.toml
COPY services/payments/pyproject.toml services/payments/pyproject.toml
COPY services/agent/pyproject.toml services/agent/pyproject.toml
COPY packages/opscp_common packages/opscp_common
COPY infra/local/issuer/pyproject.toml infra/local/issuer/pyproject.toml
COPY infra/local/issuer/src infra/local/issuer/src

RUN uv sync --locked --no-dev --no-editable --package opscp-dev-issuer


FROM python:3.13-slim-bookworm@sha256:f0fa218d5b06f9b9235024fa061cde2cb141f726de52134471053d2d9b5324a3

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=build /app/.venv /app/.venv

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 10001:10001

EXPOSE 8090

HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=6 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=2)"]

CMD ["python", "-m", "opscp_dev_issuer"]
