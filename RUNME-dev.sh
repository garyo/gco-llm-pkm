#!/usr/bin/env bash
set -e

SESSION="dev"
BACKEND_CMD="./pkm-bridge-server.py"
FRONTEND_CMD="cd frontend && bun run dev"

# --- Function to start in Zellij ---
run_in_zellij() {
  echo "Starting development session in Zellij..."

  # create a temporary layout file
  LAYOUT_FILE=$(mktemp -t zellij-dev-layout)
  cat >"$LAYOUT_FILE" <<KDL
layout {
    tab name="Dev" {
        pane command="bash" {
          args "-lc" "./pkm-bridge-server.py"
        }
        pane command="bash" {
          args "-lc" "cd frontend && bun run dev"
        }
    }
}
KDL

  # start Zellij with that layout
  zellij --layout "$LAYOUT_FILE"

  # clean up afterward
  rm -f "$LAYOUT_FILE"
}

# --- Function to start in plain terminals ---
run_plain() {
  echo "Zellij not found. Starting servers in plain terminals..."
  gnome-terminal -- bash -c "${BACKEND_CMD}; exec bash" 2>/dev/null \
    || open -a Terminal.app "${PWD}/backend" 2>/dev/null \
    || ( ${BACKEND_CMD} & )
  sleep 1
  gnome-terminal -- bash -c "${FRONTEND_CMD}; exec bash" 2>/dev/null \
    || open -a Terminal.app "${PWD}/frontend" 2>/dev/null \
    || ( ${FRONTEND_CMD} & )
}

# --- Main logic ---
if command -v zellij >/dev/null 2>&1; then
  run_in_zellij
else
  run_plain
fi
