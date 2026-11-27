#!/usr/bin/env bash
# Local stand-in for the Azure bootstrap job.
set -euo pipefail

psql --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --set password="$POSTGRES_PASSWORD" \
    --set agent_password="${AGENT_DATABASE_PASSWORD:?Run make env to create .env first}" <<'SQL'
CREATE ROLE migrator LOGIN PASSWORD :'password';
CREATE ROLE mcp_app LOGIN PASSWORD :'password';
CREATE ROLE payments_worker LOGIN PASSWORD :'password';
-- The agent's password opens no role but its own, and its role opens only its own database.
CREATE ROLE agent_app LOGIN PASSWORD :'agent_password';

CREATE DATABASE meridian OWNER migrator;
CREATE DATABASE meridian_test OWNER migrator;
CREATE DATABASE copilot OWNER migrator;
CREATE DATABASE copilot_test OWNER migrator;

REVOKE CONNECT ON DATABASE meridian, meridian_test, copilot, copilot_test FROM PUBLIC;
GRANT CONNECT ON DATABASE meridian, meridian_test TO mcp_app, payments_worker;
-- The agent has its own database and no way into the business one.
GRANT CONNECT ON DATABASE copilot, copilot_test TO agent_app;
SQL
