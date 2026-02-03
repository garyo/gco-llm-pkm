#!/usr/bin/env bash
set -euo pipefail

ssh docker-server bash -s <<'REMOTE'
set -euo pipefail
cd containers/gco-llm-pkm

echo "==> Pulling latest code..."
git pull

echo "==> Building and deploying..."
docker compose up -d --build

echo "==> Waiting for pkm-bridge-server to start..."
for i in $(seq 1 10); do
    status=$(docker inspect --format='{{.State.Status}}' pkm-bridge-server 2>/dev/null || echo "not_found")
    if [ "$status" = "running" ]; then
        break
    fi
    sleep 1
done

if [ "$status" = "running" ]; then
    # Verify it stays running (not crash-looping)
    sleep 3
    status=$(docker inspect --format='{{.State.Status}}' pkm-bridge-server 2>/dev/null || echo "not_found")
fi

echo ""
docker compose logs --tail=15 pkm-bridge

if [ "$status" = "running" ]; then
    echo ""
    echo "==> Deploy successful: pkm-bridge-server is running"
    exit 0
else
    echo ""
    echo "==> Deploy FAILED: pkm-bridge-server status: $status"
    exit 1
fi
REMOTE
