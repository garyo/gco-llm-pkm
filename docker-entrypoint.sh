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

# It runs unsupervised alongside the Flask server below, so a fast failure
# (e.g. missing MCP_AUTH_PASSWORD) would otherwise be silent until the
# :8001 healthcheck goes red. Give it a moment and log loudly if it's
# already dead.
sleep 2
if ! kill -0 "$MCP_PID" 2>/dev/null; then
    echo "ERROR: MCP server (port ${MCP_PORT:-8001}) exited immediately — check logs above. Continuing without it; the container healthcheck will report unhealthy." >&2
fi

# Ensure MCP server is cleaned up when the main process exits
trap "kill $MCP_PID 2>/dev/null" EXIT

# Switch to pkm user and run Flask server (foreground)
exec su -c "python3 pkm-bridge-server.py" pkm
