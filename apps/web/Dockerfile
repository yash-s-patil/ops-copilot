# Build from the repository root: docker build --file apps/web/Dockerfile .

FROM node:24-bookworm-slim@sha256:873f586f3794f52281bd039bf02c048a0be08fde619338f9941bad1eb6b34530 AS build

ENV CI=true \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    NEXT_TELEMETRY_DISABLED=1

WORKDIR /app

COPY apps/web/package.json apps/web/pnpm-lock.yaml apps/web/pnpm-workspace.yaml ./

# pnpm at the version package.json names.
RUN corepack enable \
    && pnpm install --frozen-lockfile

COPY apps/web/next.config.ts apps/web/tsconfig.json ./
COPY apps/web/src src

RUN pnpm run build


FROM node:24-bookworm-slim@sha256:873f586f3794f52281bd039bf02c048a0be08fde619338f9941bad1eb6b34530

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static .next/static
COPY config/limits.yaml config/limits.yaml

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000 \
    LIMITS_FILE=/app/config/limits.yaml

USER 10001:10001

EXPOSE 3000

HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=6 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/healthz', { signal: AbortSignal.timeout(2000) }).then((r) => process.exit(r.ok ? 0 : 1), () => process.exit(1))"]

CMD ["node", "server.js"]
