#!/bin/bash
set -e

echo "Running database migrations..."
uv run --script migrate_add_cost_tracking.py

echo "Starting PKM Bridge Server..."
exec uv run --script pkm-bridge-server.py
