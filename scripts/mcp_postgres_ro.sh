#!/bin/bash
# MCP-wrapper: PostgreSQL read-only access for Claude Code.
#
# Priority:
#   1) $POSTGRES_URL_RO (if set in shell env)
#   2) DATABASE_URL from .env  (fallback — full access!)
#
# To create a proper read-only role:
#   CREATE ROLE mcp_ro LOGIN PASSWORD '...' IN ROLE pg_read_all_data;
#   POSTGRES_URL_RO="postgresql://mcp_ro:...@localhost/surgil_db"
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [ -z "${POSTGRES_URL_RO:-}" ]; then
  if [ -f "$PROJECT_DIR/.env" ]; then
    POSTGRES_URL_RO=$(grep '^DATABASE_URL=' "$PROJECT_DIR/.env" | head -1 | cut -d= -f2-)
  fi
fi

if [ -z "${POSTGRES_URL_RO:-}" ]; then
  echo "ERROR: POSTGRES_URL_RO not set and DATABASE_URL not found in .env" >&2
  exit 1
fi

exec npx -y @modelcontextprotocol/server-postgres "$POSTGRES_URL_RO"
