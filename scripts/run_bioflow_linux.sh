#!/usr/bin/env bash
# Launch BioFlow against its private desktop environment.
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_ROOT="${BIOFLOW_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/bioflow}"
export BIOFLOW_DATA_DIR="$DATA_ROOT"

if [[ ! -x "$DATA_ROOT/python/bin/python" ]]; then
    echo "BioFlow is not set up yet. Run: $APP_ROOT/scripts/install_bioflow_linux.sh" >&2
    exit 1
fi

exec "$DATA_ROOT/python/bin/python" "$APP_ROOT/app/main.py"
