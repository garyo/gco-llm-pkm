#!/bin/bash
set -e

# Supervision below needs `wait -n` (bash 4.3+); the runtime image ships 5.x.
# Without this guard an older shell would report a phantom crash on startup.
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    echo "ERROR: docker-entrypoint.sh requires bash 4.3+ (found ${BASH_VERSION:-unknown})" >&2
    exit 1
fi

# Run database migrations as pkm user
echo "Running database migrations..."
su -c "python3 migrate_add_cost_tracking.py" pkm

echo "Starting PKM Bridge Server..."
echo "Note: Incremental embeddings will run automatically at 3am daily via APScheduler"

# Both servers run as supervised children: whichever exits first takes the
# container down with it. A half-dead container would otherwise keep running
# (Flask alive, MCP gone) and merely go unhealthy, which nothing acts on —
# restart policies don't restart unhealthy containers, only stopped ones.

echo "Starting MCP server on port ${MCP_PORT:-8001}..."
su -c "python3 -m mcp_server.server" pkm &
mcp_pid=$!

su -c "python3 pkm-bridge-server.py" pkm &
flask_pid=$!

shutdown() {
    trap - TERM INT
    echo "Shutting down..."
    kill "$mcp_pid" "$flask_pid" 2>/dev/null || true
    wait 2>/dev/null || true
    exit 0
}
trap shutdown TERM INT

status=0
wait -n || status=$?

if kill -0 "$mcp_pid" 2>/dev/null; then
    dead="Flask server (port ${PORT:-8000})"
else
    dead="MCP server (port ${MCP_PORT:-8001})"
fi
echo "ERROR: $dead exited with status $status — stopping the container so it restarts." >&2

kill "$mcp_pid" "$flask_pid" 2>/dev/null || true
wait 2>/dev/null || true
exit "$status"
