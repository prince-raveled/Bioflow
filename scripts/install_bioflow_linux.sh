#!/usr/bin/env bash
# Prepare a Linux machine to run BioFlow from a source checkout.
#
# This script only creates the desktop Python environment. Every analysis
# backend - the Micromamba runtime, tool environments, and reference databases -
# is provisioned by BioFlow's own setup engine, either from the Setup & Resources
# page in the app or through the headless CLI shown at the end.
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_ROOT="${BIOFLOW_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/bioflow}"
INSTALL_COMPONENTS=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --data-dir) DATA_ROOT="$2"; shift 2 ;;
        --install) shift; INSTALL_COMPONENTS="$*"; break ;;
        -h|--help)
            cat <<'USAGE'
Usage: install_bioflow_linux.sh [--data-dir DIR] [--install KEY...]

  --data-dir DIR   Where BioFlow keeps its runtime and reference data.
  --install KEY... Also install these backends now, without opening the GUI.
                   Run with --install to see the available keys, for example:
                     --install env:qc env:hostrem db:grch38
USAGE
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

command -v python3 >/dev/null || {
    echo "Missing required system command: python3" >&2
    exit 1
}

PYTHON_ENV="$DATA_ROOT/python"
mkdir -p "$DATA_ROOT"

echo "Creating the BioFlow desktop environment in $PYTHON_ENV..."
if [[ ! -x "$PYTHON_ENV/bin/python" ]]; then
    python3 -m venv "$PYTHON_ENV"
fi
"$PYTHON_ENV/bin/pip" install --upgrade --quiet pip
"$PYTHON_ENV/bin/pip" install --quiet -r "$APP_ROOT/requirements-desktop.txt"

export BIOFLOW_DATA_DIR="$DATA_ROOT"

if [[ -n "$INSTALL_COMPONENTS" ]]; then
    echo "Installing analysis backends: $INSTALL_COMPONENTS"
    ( cd "$APP_ROOT/app" && "$PYTHON_ENV/bin/python" -m backend.setup.cli --install $INSTALL_COMPONENTS )
else
    echo
    echo "Backend status:"
    ( cd "$APP_ROOT/app" && "$PYTHON_ENV/bin/python" -m backend.setup.cli --status )
fi

cat <<EOF

BioFlow's desktop environment is ready. Start it with:
  $APP_ROOT/scripts/run_bioflow_linux.sh

Install analysis backends from the Setup & Resources page inside the app, or
headlessly with:
  cd $APP_ROOT/app && $PYTHON_ENV/bin/python -m backend.setup.cli --install env:qc env:hostrem db:grch38
EOF
