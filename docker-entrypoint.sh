#!/bin/bash
set -e

# Run database migrations as pkm user
echo "Running database migrations..."
su -c "python3 migrate_add_cost_tracking.py" pkm

echo "Starting PKM Bridge Server..."
echo "Note: Incremental embeddings will run automatically at 3am daily via APScheduler"

# Switch to pkm user and run server
exec su -c "python3 pkm-bridge-server.py" pkm
