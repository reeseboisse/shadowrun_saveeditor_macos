#!/usr/bin/env bash
# Set up the Python environment the SwiftUI app expects to find at
# ~/.shadowrun-editor/venv. Idempotent.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VENV="$HOME/.shadowrun-editor/venv"

if [[ ! -d "$VENV" ]]; then
    echo "Creating virtualenv at $VENV"
    /usr/bin/env python3 -m venv "$VENV"
fi

"$VENV/bin/pip" install --upgrade pip > /dev/null
echo "Installing shadowrun_editor (editable) from $REPO_ROOT"
"$VENV/bin/pip" install -e "$REPO_ROOT"

echo
echo "Python environment ready."
echo "  Python:  $VENV/bin/python3"
echo "  Bridge:  $VENV/bin/shadowrun-editor-bridge"
echo
echo "Smoke test:"
echo '  {"id":1,"method":"ping","params":{}}' | "$VENV/bin/shadowrun-editor-bridge"
