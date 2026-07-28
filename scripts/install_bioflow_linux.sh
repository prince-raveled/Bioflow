#!/usr/bin/env bash
# Install the BioFlow desktop runtime plus QC and Host Removal backends.
# Run from a BioFlow source checkout: ./scripts/install_bioflow_linux.sh
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/bioflow"
INSTALL_REFERENCE=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --data-dir) DATA_ROOT="$2"; shift 2 ;;
        --skip-grch38) INSTALL_REFERENCE=0; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

for command in curl tar python3; do
    command -v "$command" >/dev/null || {
        echo "Missing required system command: $command" >&2
        exit 1
    }
done

MAMBA_ROOT="$DATA_ROOT/micromamba-root"
MAMBA_BIN="$DATA_ROOT/bin/micromamba"
PYTHON_ENV="$DATA_ROOT/python"
DATABASE_ROOT="$DATA_ROOT/databases"
mkdir -p "$DATA_ROOT/bin" "$MAMBA_ROOT" "$DATABASE_ROOT"

if [[ ! -x "$MAMBA_BIN" ]]; then
    case "$(uname -m)" in
        x86_64|amd64) MAMBA_PLATFORM="linux-64" ;;
        aarch64|arm64) MAMBA_PLATFORM="linux-aarch64" ;;
        *) echo "Unsupported Linux architecture: $(uname -m)" >&2; exit 1 ;;
    esac
    TEMP_DIR="$(mktemp -d)"
    trap 'rm -rf "$TEMP_DIR"' EXIT
    echo "Downloading Micromamba..."
    curl --fail --location --silent --show-error \
        "https://micro.mamba.pm/api/micromamba/$MAMBA_PLATFORM/latest" \
        --output "$TEMP_DIR/micromamba.tar.bz2"
    tar -xjf "$TEMP_DIR/micromamba.tar.bz2" -C "$TEMP_DIR" bin/micromamba
    install -m 755 "$TEMP_DIR/bin/micromamba" "$MAMBA_BIN"
fi

echo "Creating BioFlow quality-control environment..."
"$MAMBA_BIN" create -y -r "$MAMBA_ROOT" -n bioflow-qc \
    -c conda-forge -c bioconda fastqc fastp multiqc

echo "Creating BioFlow host-removal environment..."
"$MAMBA_BIN" create -y -r "$MAMBA_ROOT" -n bioflow-hostrem \
    -c conda-forge -c bioconda bowtie2 samtools bwa fastp pigz

if [[ "$INSTALL_REFERENCE" -eq 1 ]]; then
    HG38_DIR="$DATABASE_ROOT/human/hg38"
    REFERENCE="$HG38_DIR/GRCh38.primary_assembly.genome.fa.gz"
    INDEX_PREFIX="$HG38_DIR/GRCh38_index"
    mkdir -p "$HG38_DIR"
    if [[ ! -f "$REFERENCE" ]]; then
        echo "Downloading the GRCh38 human reference (~806 MB)..."
        curl --fail --location --continue-at - \
            "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_43/GRCh38.primary_assembly.genome.fa.gz" \
            --output "$REFERENCE"
    fi
    if [[ ! -f "$INDEX_PREFIX.1.bt2" && ! -f "$INDEX_PREFIX.1.bt2l" ]]; then
        echo "Building the GRCh38 Bowtie2 index; this can take several minutes..."
        "$MAMBA_BIN" run -r "$MAMBA_ROOT" -n bioflow-hostrem \
            bowtie2-build "$REFERENCE" "$INDEX_PREFIX"
    fi
fi

echo "Verifying installed tools..."
"$MAMBA_BIN" run -r "$MAMBA_ROOT" -n bioflow-qc fastqc --version
"$MAMBA_BIN" run -r "$MAMBA_ROOT" -n bioflow-qc fastp --version
"$MAMBA_BIN" run -r "$MAMBA_ROOT" -n bioflow-qc multiqc --version
"$MAMBA_BIN" run -r "$MAMBA_ROOT" -n bioflow-hostrem bowtie2 --version

if [[ ! -d "$PYTHON_ENV" ]]; then
    python3 -m venv "$PYTHON_ENV"
fi
"$PYTHON_ENV/bin/pip" install --upgrade pip
"$PYTHON_ENV/bin/pip" install -r "$APP_ROOT/requirements-desktop.txt"

echo
echo "BioFlow backend setup is complete. Start it with:"
echo "  $APP_ROOT/scripts/run_bioflow_linux.sh"
