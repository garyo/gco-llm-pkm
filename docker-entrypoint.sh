#!/bin/bash
set -e

echo "Running database migrations..."
python3 migrate_add_cost_tracking.py

echo "Starting PKM Bridge Server..."
exec python3 pkm-bridge-server.py
