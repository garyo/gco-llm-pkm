#!/bin/bash
set -e

# Run database migrations as pkm user
echo "Running database migrations..."
su -c "python3 migrate_add_cost_tracking.py" pkm

echo "Starting PKM Bridge Server..."
echo "Note: Incremental embeddings will run automatically at 3am daily via APScheduler"

# Start MCP server in the background (port 8001 by default)
echo "Starting MCP server on port ${MCP_PORT:-8001}..."
su -c "python3 -m mcp_server.server" pkm &
MCP_PID=$!

# Ensure MCP server is cleaned up when the main process exits
trap "kill $MCP_PID 2>/dev/null" EXIT

# Switch to pkm user and run Flask server (foreground)
exec su -c "python3 pkm-bridge-server.py" pkm
