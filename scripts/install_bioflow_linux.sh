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

# Ask the dynamic linker whether it can load the library, which is the same
# question Qt asks at startup and the only one that matters.
#
# This used to run `ldconfig -p`, which fails silently for an ordinary user on
# Debian: /usr/sbin is not on their PATH, so the command is not found, the error
# goes to /dev/null, and every library is reported missing. Installing the
# packages changed nothing, so the script asked for them again - an unbreakable
# loop for anyone on Debian. Ubuntu only escaped it by putting /usr/sbin on the
# user PATH.
#
# python3 is guaranteed here: it is checked above, and the script exits without
# it. ldconfig is still tried at its real locations as a fallback for a system
# whose Python cannot load shared objects.
have_lib() {
    if python3 - "$1" <<'PY' >/dev/null 2>&1
import ctypes, sys
ctypes.CDLL(sys.argv[1])
PY
    then
        return 0
    fi
    local finder
    for finder in ldconfig /usr/sbin/ldconfig /sbin/ldconfig; do
        if command -v "$finder" >/dev/null 2>&1 && "$finder" -p 2>/dev/null | grep -q "$1"; then
            return 0
        fi
    done
    return 1
}

require_lib() {
    # $1 = soname, $2 = Debian package, $3 = Fedora package
    if ! have_lib "$1"; then
        MISSING_APT+=("$2")
        MISSING_DNF+=("$3")
    fi
}

# Read from PyQt6's own Qt platform plugin (libqxcb.so), not guessed: these are
# what it links against and cannot start without. Six of them used to be
# checked, and a minimal Debian then installed cleanly and died at launch on
# libGL - which is exactly the cryptic failure this check exists to prevent.
require_lib libxcb-cursor.so.0      libxcb-cursor0      xcb-util-cursor
require_lib libxcb-icccm.so.4       libxcb-icccm4       xcb-util-wm
require_lib libxcb-keysyms.so.1     libxcb-keysyms1     xcb-util-keysyms
require_lib libxcb-image.so.0       libxcb-image0       xcb-util-image
require_lib libxcb-render-util.so.0 libxcb-render-util0 xcb-util-renderutil
require_lib libxcb-util.so.1        libxcb-util1        xcb-util
require_lib libxcb-shape.so.0       libxcb-shape0       libxcb
require_lib libxcb-xkb.so.1         libxcb-xkb1         libxcb
require_lib libX11-xcb.so.1         libx11-xcb1         libX11-xcb
require_lib libxkbcommon.so.0       libxkbcommon0       libxkbcommon
require_lib libxkbcommon-x11.so.0   libxkbcommon-x11-0  libxkbcommon-x11
require_lib libfontconfig.so.1      libfontconfig1      fontconfig
require_lib libdbus-1.so.3          libdbus-1-3         dbus-libs
require_lib libglib-2.0.so.0        libglib2.0-0        glib2
require_lib libGL.so.1              libgl1              mesa-libGL
require_lib libEGL.so.1             libegl1             mesa-libEGL

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
    # One package can supply several of the libraries above, so name it once.
    mapfile -t MISSING_APT < <(printf '%s\n' "${MISSING_APT[@]}" | awk '!seen[$0]++')
    mapfile -t MISSING_DNF < <(printf '%s\n' "${MISSING_DNF[@]}" | awk '!seen[$0]++')
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

# ----------------------------------------------------------------------
# Prove Qt actually starts.
#
# The list above is a fast pre-check that saves a pip download when something
# obvious is absent, but it is a list, and a list can be short. It was: six
# libraries were checked, a minimal Debian installed cleanly, and the
# application then died at launch on libGL - the exact cryptic failure the
# pre-check exists to prevent. Widening it found libEGL, then libglib.
#
# So after PyQt6 is installed, ask it to start. Whatever is still missing is
# named here by the loader itself, which cannot be out of date.
# ----------------------------------------------------------------------
QT_PROBE=$(QT_QPA_PLATFORM=offscreen "$PYTHON_ENV/bin/python" -c \
    'from PyQt6.QtWidgets import QApplication; QApplication([])' 2>&1) || {
    MISSING_SO=$(printf '%s\n' "$QT_PROBE" | grep -oE 'lib[A-Za-z0-9_.+-]*\.so\.[0-9]+' | head -1)
    echo >&2
    echo "BioFlow installed, but its interface cannot start on this system." >&2
    if [[ -n "$MISSING_SO" ]]; then
        echo >&2
        echo "  A system library is missing: $MISSING_SO" >&2
        echo >&2
        if command -v apt-get >/dev/null; then
            echo "  Find the package that provides it with:" >&2
            echo "    apt-file search $MISSING_SO" >&2
        elif command -v dnf >/dev/null; then
            echo "  Install it with:" >&2
            echo "    sudo dnf install -y \"*/$MISSING_SO\"" >&2
        fi
        echo >&2
        echo "  Then run this script again." >&2
    else
        echo >&2
        printf '%s\n' "$QT_PROBE" | tail -5 >&2
    fi
    exit 1
}

if [[ -n "$INSTALL_COMPONENTS" ]]; then
    echo "Installing analysis backends: $INSTALL_COMPONENTS"
    ( cd "$APP_ROOT/app" && "$PYTHON_ENV/bin/python" -m backend.setup.cli --install $INSTALL_COMPONENTS )
else
    echo
    echo "Backend status:"
    ( cd "$APP_ROOT/app" && "$PYTHON_ENV/bin/python" -m backend.setup.cli --status )
fi

# ----------------------------------------------------------------------
# Container execution is optional, and stays optional.
#
# Analysis runs on this machine by default, in environments BioFlow installs
# itself, which is what lets a fresh Linux machine work without asking anything
# of whoever is using it. Containers are for pinning the exact tool binaries -
# worth having, never required - so this reports what is available and does not
# install a runtime or fail without one.
# ----------------------------------------------------------------------
if command -v podman >/dev/null; then
    CONTAINER_NOTE="Podman is installed, so container execution is available if you want it."
elif command -v docker >/dev/null; then
    CONTAINER_NOTE="Docker is installed, so container execution is available if you want it."
else
    CONTAINER_NOTE="No container runtime found. Analysis runs on this machine, which needs nothing further."
fi

cat <<EOF

BioFlow's desktop environment is ready. Start it with:
  $APP_ROOT/scripts/run_bioflow_linux.sh

Install analysis backends from the Setup & Resources page inside the app, or
headlessly with:
  cd $APP_ROOT/app && $PYTHON_ENV/bin/python -m backend.setup.cli --install env:qc env:hostrem db:grch38

Reference databases are downloaded once and reused. They are large - GRCh38 is
several GB and the MetaPhlAn database around 51 GB - and live outside the
application, so they survive upgrades and can be shared between machines. If you
already have them, point BioFlow at them from Setup & Resources instead of
downloading again.

$CONTAINER_NOTE
  Build the image:  $APP_ROOT/docker/build.sh
  Then choose "In a container" under Analysis environment in Setup & Resources.
EOF
