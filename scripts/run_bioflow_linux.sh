#!/usr/bin/env bash
# Launch BioFlow against its private Micromamba installation.
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_ROOT="${BIOFLOW_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/bioflow}"
export BIOFLOW_MICROMAMBA="$DATA_ROOT/bin/micromamba"
export BIOFLOW_MAMBA_ROOT_PREFIX="$DATA_ROOT/micromamba-root"
export BIOFLOW_GRCH38_INDEX="$DATA_ROOT/databases/human/hg38/GRCh38_index"

if [[ ! -x "$BIOFLOW_MICROMAMBA" || ! -x "$DATA_ROOT/python/bin/python" ]]; then
    echo "BioFlow is not set up yet. Run: $APP_ROOT/scripts/install_bioflow_linux.sh" >&2
    exit 1
fi

exec "$DATA_ROOT/python/bin/python" "$APP_ROOT/app/main.py"
