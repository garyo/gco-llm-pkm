#!/bin/sh
# Inject build timestamp into service worker
# This ensures the SW updates on every deployment

SW_PATH="${1:-dist/sw.js}"

if [ ! -f "$SW_PATH" ]; then
  echo "❌ Service worker not found at: $SW_PATH"
  exit 1
fi

# Generate timestamp (ISO format)
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Replace placeholder with actual timestamp
sed -i.bak "s/__BUILD_TIMESTAMP__/$TIMESTAMP/g" "$SW_PATH"
rm -f "$SW_PATH.bak"

echo "✅ Injected build timestamp into sw.js: $TIMESTAMP"
