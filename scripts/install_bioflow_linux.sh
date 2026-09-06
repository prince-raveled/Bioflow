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

# ----------------------------------------------------------------------
# System dependencies
#
# pip installs PyQt6's wheel, but that wheel links against X11/XCB libraries
# the package manager owns. Without them Qt aborts at startup with "no Qt
# platform plugin could be initialized", which says nothing about the actual
# cause. Minimal Ubuntu images also ship python3 without ensurepip, so the
# venv below would fail on a message that never mentions python3-venv.
# Check both here, while there is still room to name the fix.
# ----------------------------------------------------------------------
declare -a MISSING_APT=() MISSING_DNF=()

require_lib() {
    # $1 = soname, $2 = Debian package, $3 = Fedora package
    if ! ldconfig -p 2>/dev/null | grep -q "$1"; then
        MISSING_APT+=("$2")
        MISSING_DNF+=("$3")
    fi
}

require_lib libxcb-cursor.so.0    libxcb-cursor0      xcb-util-cursor
require_lib libxkbcommon-x11.so.0 libxkbcommon-x11-0  libxkbcommon-x11
require_lib libxcb-icccm.so.4     libxcb-icccm4       xcb-util-wm
require_lib libxcb-keysyms.so.1   libxcb-keysyms1     xcb-util-keysyms
require_lib libxcb-shape.so.0     libxcb-shape0       libxcb
require_lib libxcb-xkb.so.1       libxcb-xkb1         libxcb

if ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
    MISSING_APT+=(python3-venv)
    MISSING_DNF+=(python3-libs)
fi

if (( ${#MISSING_APT[@]} )); then
    # De-duplicate: several sonames map to one Fedora package.
    readarray -t MISSING_APT < <(printf '%s\n' "${MISSING_APT[@]}" | sort -u)
    readarray -t MISSING_DNF < <(printf '%s\n' "${MISSING_DNF[@]}" | sort -u)

    echo "BioFlow needs some system packages that pip cannot install." >&2
    echo >&2
    if command -v apt-get >/dev/null; then
        echo "  sudo apt install -y ${MISSING_APT[*]}" >&2
    elif command -v dnf >/dev/null; then
        echo "  sudo dnf install -y ${MISSING_DNF[*]}" >&2
    else
        echo "  Debian/Ubuntu: ${MISSING_APT[*]}" >&2
        echo "  Fedora/RHEL:   ${MISSING_DNF[*]}" >&2
    fi
    echo >&2
    echo "Run that, then start this script again." >&2
    exit 1
fi

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
