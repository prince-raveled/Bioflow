# BioFlow

BioFlow is a guided desktop application for metagenomic analysis on Linux. It
takes raw sequencing reads to organised, reproducible results without requiring
the terminal, and it installs and manages its own analysis tools and databases.

Architecture and roadmap: [docs/BIOFLOW_ARCHITECTURE.md](docs/BIOFLOW_ARCHITECTURE.md)

---

## 1. Installing BioFlow on Linux

From a source checkout:

```bash
chmod +x scripts/install_bioflow_linux.sh scripts/run_bioflow_linux.sh
./scripts/install_bioflow_linux.sh     # creates BioFlow's desktop Python environment
./scripts/run_bioflow_linux.sh         # launches the application
```

The installer only sets up the desktop application itself. It needs `python3`
and nothing else — no root access, no system Conda, no pre-installed
bioinformatics tools.

## 2. How BioFlow manages its own dependencies

Every analysis tool runs inside an environment that **BioFlow installs and
owns**, under its private data directory. BioFlow never uses a tool from your
`PATH` and never falls back to a system Conda installation: if a required
environment is missing, the analysis refuses to start and tells you which
component to install. This keeps results reproducible and traceable to a known
set of binaries.

Open **Setup & Resources** in the application to install components. Each row
shows its size and whether it is installed, dependencies are added
automatically, and BioFlow checks CPU, memory, free disk and network before
downloading anything.

Everything lives in `~/.local/share/bioflow` (override with `BIOFLOW_DATA_DIR`):

```text
~/.local/share/bioflow/
├── bin/micromamba              BioFlow's private package manager
├── micromamba-root/envs/       bioflow-qc, bioflow-hostrem, bioflow-taxonomy, bioflow-humann
├── databases/                  reference data
├── logs/                       setup logs
├── config.json                 settings
└── history.sqlite3             run history
```

## 3. Disk space

| Component | Key | Size |
|---|---|---|
| Micromamba runtime | `micromamba` | 30 MB |
| Quality control (FastQC, fastp, MultiQC) | `env:qc` | ~2 GB |
| Host removal (Bowtie2, samtools, seqkit) | `env:hostrem` | ~1 GB |
| Taxonomic profiling (MetaPhlAn 4) | `env:taxonomy` | ~4 GB |
| Functional profiling (HUMAnN 3, DIAMOND) | `env:function` | ~3 GB |
| GRCh38 reference + Bowtie2 index | `db:grch38` | ~6 GB |
| MetaPhlAn marker database | `db:metaphlan_chocophlan` | ~33 GB |
| HUMAnN UniRef50 (DIAMOND) | `db:humann_uniref50` | ~6 GB |
| HUMAnN ChocoPhlAn (nucleotide) | `db:humann_chocophlan` | ~17 GB |

**Everything: about 72 GB.** Quality control alone needs ~2 GB. QC plus host
removal needs ~9 GB. Install only what your workflow uses — each item is
optional and BioFlow refuses to start a download that would not fit.

## 4. Setting up databases

In the application: **Setup & Resources** → tick the databases → **Install
selected**. Selecting a database automatically adds the environment whose tools
install it, so ticking `db:grch38` also installs `env:hostrem`.

Headlessly, the same engine is available from the command line:

```bash
cd app
python -m backend.setup.cli --status
python -m backend.setup.cli --install env:qc env:hostrem db:grch38
python -m backend.setup.cli --install db:metaphlan_chocophlan --dry-run
```

Downloads resume: if one is interrupted, run it again and BioFlow reuses what it
already fetched rather than starting over.

### Using a GRCh38 index you already have

If you already have a Bowtie2 index for GRCh38, you can point BioFlow at it
instead of downloading another copy. In **Setup & Resources**, use
**Existing GRCh38 index → Use existing index...** and select any one of the six
index files. BioFlow validates that the complete set is present, then saves the
choice to `config.json`:

```json
{
  "references": {
    "grch38_index_prefix": "/path/to/GRCh38_index"
  }
}
```

The setting survives restarts, and **Clear** returns BioFlow to its own managed
copy. BioFlow never scans your disk looking for a reference — an external index
is used only when you have configured it explicitly.

**Resolution order**, applied every time host removal runs:

1. `BIOFLOW_GRCH38_INDEX`, if set and complete — a **development and test
   override only**. It is reported as *Development override*, is never saved as
   configuration, and never changes where an installation writes.
2. The external index configured above, if complete.
3. BioFlow's own managed installation.

**Installation always writes to the managed location**
(`<data_root>/databases/human/hg38/GRCh38_index`), whatever is configured for
run time. Configuring an external index never redirects a download, and Setup
still lets you install the managed copy alongside it.

Setup reports each resource honestly: `Not installed`, `Incomplete` (some index
files present but not all six), `Managed` (installed by BioFlow), or
`External` / `Development override` with the path actually in use.

## 5. Starting an analysis

1. Open **Run workflow**.
2. **Select FASTQ files** — one or many samples at once.
3. Confirm the **read layout** (see below).
4. Choose a **results folder** and a project name.
5. Tick the **stages** to run.
6. Set **threads**, and choose HUMAnN's mode if you are running it.
7. Press **Run workflow**.

BioFlow validates every input file first, then checks that all required
environments and databases are installed. If anything is missing it names it
and does not start.

## 6. Single-end FASTQ

Provide one file per sample:

```text
sample.fastq.gz
```

BioFlow creates one sample per file. It never asks for a second mate and never
fabricates one. Commands use single-end forms throughout — `fastp -i/-o`,
`bowtie2 -U … --un-gz`.

## 7. Paired-end FASTQ

Provide both mates:

```text
sample_R1.fastq.gz
sample_R2.fastq.gz
```

BioFlow detects `_R1`/`_R2`, `_1`/`_2` and similar markers and groups them into
one sample. Detection is only a proposal: the **Read layout** control lets you
force Single-end or Paired-end, and the sample table shows exactly how your
files were interpreted before you run anything.

Pairing is preserved end to end — mates are trimmed together and stay in
separate files. If a mate is missing or two files are not genuine mates (BioFlow
compares their first read identifiers), it reports the problem instead of
guessing.

Two stages take a single input because the tools require it, and BioFlow states
this in the interface:

- **MetaPhlAn** receives `R1,R2` as one comma-separated argument, its own
  convention for paired input. The files are not merged.
- **HUMAnN** accepts only one file, so the two host-removed mate files are
  concatenated into `<sample>_combined.fastq.gz` first.

## 8. Where results are stored

Inside the results folder you chose, under your project name:

```text
<results>/<project>/
├── 01_qc_raw/            FastQC on the raw reads
├── 02_trimmed/           trimmed FASTQ + fastp HTML/JSON reports
├── 03_qc_trimmed/        FastQC on the trimmed reads
├── 04_host_removed/      non-human reads + Bowtie2 logs
├── 05_taxonomy/          MetaPhlAn profiles and map files
├── 06_functional/        HUMAnN gene-family, pathway and coverage tables
├── 07_multiqc/           combined MultiQC report
├── logs/                 stdout and stderr for every command
├── reports/              project reports
├── bioflow-project.json  samples, layout and options
└── bioflow-run.json      per-stage results, used for resuming
```

## 9. Resuming a failed analysis

Leave **Reuse valid results** ticked and press **Run workflow** again.

A stage is skipped only when all three hold: it succeeded before, its inputs and
commands are unchanged, and its outputs still pass validation now. Anything
else re-runs. So if MetaPhlAn failed after QC, trimming and host removal
succeeded, only MetaPhlAn re-runs — but if you replace an input FASTQ,
everything downstream of it re-runs automatically.

Deleting `bioflow-run.json` forces a complete re-run.

## 10. Inspecting logs

- **In the app**: the analysis log streams live; **Run history** lists every run
  with its status, command and output folder.
- **On disk**: `logs/<stage>__<sample>.out.log` and `.err.log` hold the complete
  captured output of each command. `bioflow-run.json` records, for every stage:
  the command and arguments, the environment used, start and end time, exit
  code, log paths, output files, and each output-validation check.

Host removal's Bowtie2 summary, including the overall alignment rate, is kept at
`04_host_removed/<sample>_bowtie2.log`.

## 11. What each stage does

| Stage | Purpose | Environment | Needs |
|---|---|---|---|
| **FastQC (raw)** | Baseline read quality: adapter content, quality drop-off, composition | `bioflow-qc` | — |
| **fastp** | Trim adapters and low-quality bases; produces its own HTML/JSON report | `bioflow-qc` | — |
| **FastQC (trimmed)** | Confirm trimming improved quality | `bioflow-qc` | — |
| **Host removal** | Align to GRCh38 with Bowtie2 and keep only non-human reads | `bioflow-hostrem` | GRCh38 index |
| **MetaPhlAn** | Taxonomic profile from clade-specific marker genes, run offline | `bioflow-taxonomy` | MetaPhlAn markers |
| **HUMAnN** | Gene-family and pathway abundances, normalised to copies per million | `bioflow-humann` | UniRef50 |
| **MultiQC** | Aggregate every QC report into one document | `bioflow-qc` | — |

A stage counts as successful only if its command exits zero **and** its outputs
pass validation — a tool that finishes without producing usable results is
reported as a failure, not a success.

### HUMAnN modes

The default is HUMAnN's documented protein-only mode
(`--bypass-prescreen --bypass-nucleotide-search`), which needs only UniRef50 and
runs on ordinary hardware. Gene families and pathway abundances are still
produced; individual genes are not attributed to species. Switching off
**HUMAnN protein-only mode** enables the full three-stage search, which also
requires the ChocoPhlAn database and considerably more memory.

## Developing BioFlow

Working on BioFlow itself needs one thing the end-user install does not: a
Python environment for the desktop application. Create it once:

```bash
./scripts/setup_dev_env.sh            # creates .venv using python3 from PATH
BIOFLOW_PYTHON=/usr/bin/python3.12 ./scripts/setup_dev_env.sh   # or a specific one
```

This installs only PyQt6, into a `.venv` in the repository root. It has nothing
to do with the bioinformatics tools, which always live in BioFlow's managed
Micromamba environments and are installed from Setup & Resources.

Then:

```bash
.venv/bin/python app/main.py                          # launch the GUI
.venv/bin/python -m unittest discover -s tests        # run the tests
```

### In VS Code

Open the repository folder. The workspace settings point the Python extension at
`${workspaceFolder}/.venv/bin/python`, so once `.venv` exists:

- **Run and Debug → "BioFlow (desktop app)"** launches the application, or
- **Run Python File** on `app/main.py` works directly.

If VS Code still shows the wrong interpreter, run
**Python: Select Interpreter** and choose the one inside `.venv`. Without it,
VS Code falls back to the system Python, which has no PyQt6 and fails with
`ModuleNotFoundError: No module named 'PyQt6'`.

The committed `.vscode/` files (`settings.json`, `launch.json`,
`extensions.json`) use `${workspaceFolder}` only and contain no machine-specific
paths; anything else VS Code writes there stays out of version control.

## Running the tests

```bash
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests
```

Most tests use stand-in executables and run in seconds.
`tests/test_real_smoke.py` invokes genuine tools through BioFlow's managed
environment and is skipped automatically when that environment is not installed.

## Author

Prince Kumar
