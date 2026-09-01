#!/usr/bin/env bash
# Create the local development environment for working on BioFlow itself.
#
# This is ONLY the desktop application's Python environment (PyQt6). It has
# nothing to do with the bioinformatics tools: those always live in BioFlow's
# managed Micromamba environments and are installed from Setup & Resources.
#
#   ./scripts/setup_dev_env.sh                 # uses python3 from PATH
#   BIOFLOW_PYTHON=/usr/bin/python3.12 ./scripts/setup_dev_env.sh
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$APP_ROOT/.venv"
PYTHON="${BIOFLOW_PYTHON:-python3}"

command -v "$PYTHON" >/dev/null || {
    echo "Cannot find '$PYTHON'. Install Python 3.10+ or set BIOFLOW_PYTHON." >&2
    exit 1
}

"$PYTHON" - <<'PY' || exit 1
import sys
if sys.version_info < (3, 10):
    sys.exit(
        f"BioFlow needs Python 3.10 or newer; {sys.executable} is "
        f"{sys.version_info.major}.{sys.version_info.minor}."
    )
PY

echo "Creating the development environment in .venv using $("$PYTHON" -c 'import sys; print(sys.executable)')"
if [[ ! -x "$VENV/bin/python" ]]; then
    "$PYTHON" -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --upgrade --quiet pip
"$VENV/bin/python" -m pip install --quiet -r "$APP_ROOT/requirements-desktop.txt"

"$VENV/bin/python" - <<'PY'
import PyQt6.QtWidgets  # noqa: F401
print("PyQt6 is available in the development environment.")
PY

cat <<EOF

Development environment ready: $VENV

  Launch the GUI:   $VENV/bin/python app/main.py
  Run the tests:    $VENV/bin/python -m unittest discover -s tests

In VS Code, reopen the folder and select the interpreter at .venv/bin/python
(the workspace settings already point at it), then use Run and Debug ->
"BioFlow (desktop app)".

Analysis tools are NOT installed here. Install them from Setup & Resources
inside the application, or with:
  cd app && $VENV/bin/python -m backend.setup.cli --install env:qc
EOF
