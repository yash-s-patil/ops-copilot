#!/usr/bin/env bash
# Local stand-in for the Azure bootstrap job.
set -euo pipefail

psql --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --set password="$POSTGRES_PASSWORD" <<'SQL'
CREATE ROLE migrator LOGIN PASSWORD :'password';
CREATE ROLE mcp_app LOGIN PASSWORD :'password';
CREATE ROLE payments_worker LOGIN PASSWORD :'password';
CREATE ROLE agent_app LOGIN PASSWORD :'password';

CREATE DATABASE meridian OWNER migrator;
CREATE DATABASE meridian_test OWNER migrator;

REVOKE CONNECT ON DATABASE meridian, meridian_test FROM PUBLIC;
GRANT CONNECT ON DATABASE meridian, meridian_test TO mcp_app, payments_worker;
SQL
