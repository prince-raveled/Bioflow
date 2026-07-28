# BioFlow

BioFlow is a one-click metagenomic analysis platform.

## Features

- FastQC
- fastp
- MetaPhlAn
- HUMAnN
- MultiQC

## Planned Features

- Interactive GUI
- AI-generated report
- Batch processing
- Docker support

## Test on a new Linux machine

The current test installer provisions the completed Quality Control and Host
Removal backends without relying on a system Conda installation. It installs a
private Micromamba runtime, the `bioflow-qc` and `bioflow-hostrem`
environments, and optionally the GRCh38 human reference and Bowtie2 index.

From a BioFlow source checkout:

```bash
chmod +x scripts/install_bioflow_linux.sh scripts/run_bioflow_linux.sh
./scripts/install_bioflow_linux.sh
./scripts/run_bioflow_linux.sh
```

If a custom backend data location was used, supply it when launching:

```bash
BIOFLOW_DATA_DIR=/your/bioflow-data ./scripts/run_bioflow_linux.sh
```

The GRCh38 download is about 806 MB and index construction takes additional
disk space and time. To test only the GUI and QC backend first:

```bash
./scripts/install_bioflow_linux.sh --skip-grch38
```

## Author

Prince Kumar
