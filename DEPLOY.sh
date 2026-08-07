#!/usr/bin/env bash
set -euo pipefail

# A deploy counts as successful only when the commit we pushed is the one
# running, every service reports healthy (see the healthcheck in
# docker-compose.yml, which probes Flask on :8000 and MCP on :8001), and both
# hostnames answer through Traefik.

expected_sha=$(git rev-parse HEAD)

if [ -n "$(git status --porcelain -uno)" ]; then
    echo "Note: working tree has uncommitted changes — deploying ${expected_sha:0:12} as pushed."
fi

echo "==> Deploying ${expected_sha:0:12}"

ssh docker-server "EXPECTED_SHA=$expected_sha bash -s" <<'REMOTE'
set -euo pipefail
cd containers/gco-llm-pkm

echo "==> Pulling latest code..."
git pull --ff-only

actual_sha=$(git rev-parse HEAD)
if [ "$actual_sha" != "$EXPECTED_SHA" ]; then
    echo ""
    echo "==> Deploy FAILED: server checked out ${actual_sha:0:12}, expected ${EXPECTED_SHA:0:12}." >&2
    echo "    Push your commit first." >&2
    exit 1
fi

echo "==> Building and waiting for services to report healthy..."
if ! GIT_SHA="$EXPECTED_SHA" docker compose up -d --build --wait --wait-timeout 240; then
    echo ""
    docker compose logs --tail=40 pkm-bridge
    echo ""
    echo "==> Deploy FAILED: services did not become healthy." >&2
    exit 1
fi

# --wait proves something healthy is running; this proves it is our build.
running_sha=$(docker inspect \
    --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
    pkm-bridge-server)
if [ "$running_sha" != "$EXPECTED_SHA" ]; then
    echo ""
    echo "==> Deploy FAILED: container is running ${running_sha:-unknown}, expected ${EXPECTED_SHA:0:12}." >&2
    exit 1
fi

echo ""
docker compose logs --tail=15 pkm-bridge
echo ""
echo "==> Container healthy, running ${EXPECTED_SHA:0:12}"
REMOTE

# Probed from here rather than on the server: the container healthcheck only
# ever curls localhost, so it cannot see broken router labels, DNS, or certs.
echo "==> Checking public endpoints..."
unreachable=0
for url in https://pkm.oberbrunner.com/health https://mcp.oberbrunner.com/health; do
    if curl -fsS --max-time 15 -o /dev/null "$url"; then
        echo "    ok       $url"
    else
        echo "    FAILED   $url" >&2
        unreachable=1
    fi
done

if [ "$unreachable" -ne 0 ]; then
    echo ""
    echo "==> Deploy FAILED: container is healthy but not reachable through Traefik." >&2
    exit 1
fi

echo ""
echo "==> Deploy successful: ${expected_sha:0:12} healthy on :8000 and :8001"
