#!/bin/sh
# Starts the web application on this machine against the services `make dev` runs.
# Usage, from the repository root: sh apps/web/dev-env.sh [next arguments]
set -eu
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
set -a
. "$ROOT/.env"
set +a
export ENVIRONMENT=local AUTH_MODE=dev AUTH_TRUST_HOST=true
export AUTH_SECRET="$WEB_AUTH_SECRET"
export AGENT_URL="${AGENT_URL:-http://127.0.0.1:8001}"
export AGENT_SCOPE="$AGENT_API_AUDIENCE/Agent.Access"
export DEV_TOKEN_URL="${DEV_TOKEN_URL:-http://127.0.0.1:8090/token}"
export LIMITS_FILE="$ROOT/config/limits.yaml"
exec pnpm --dir "$ROOT/apps/web" exec next "${@:-dev}"
