# BioFlow: Architecture and Roadmap

## Purpose

BioFlow is a Linux desktop application for guided metagenomic analysis. Its goal
is to let a researcher move from raw sequencing reads to organised, reproducible
results without constructing or running bioinformatics commands by hand.

The user-facing workflow is deliberately simple:

```text
Launch BioFlow → install backends from the Setup page → select reads → choose output → Run
```

BioFlow then selects the correct backend environment, builds the command,
streams live output into the application, saves logs, and keeps a history
record for later review.

The product promise is:

> From raw metagenomic reads to interpretable, reproducible results without
> requiring the terminal.

## Distribution model

BioFlow ships as a **single AppImage**. It bundles only Python and PyQt6 — about
60 MB — which is the minimum needed to draw a window. Everything heavier is
provisioned by the application itself, at the user's choice, from the Setup &
Resources page: the Micromamba runtime, each analysis environment, and each
reference database.

Nothing is installed outside BioFlow's own data directory, and no administrator
access or system Conda installation is required.

## Setup engine

This is the subsystem that makes "download one file, run it, everything else
configures itself" true.

```text
backend/config.py            Every path, environment name, and database location
backend/setup/registry.py    Declarative specs for environments and databases
backend/setup/manager.py     Status detection and dependency-complete planning
backend/setup/plan.py        CommandStep / ActionStep / SetupPlan
backend/setup/executor.py    Runs a plan, streams output, supports cancellation
backend/setup/bootstrap.py   Micromamba install and resumable reference downloads
backend/setup/preflight.py   CPU, memory, disk, writability, and network checks
backend/setup/cli.py         The same engine, headless, for scripts and CI
```

The registry is the single place a new tool is declared. Adding an entry gives
it a Setup row, a status check, a disk-space estimate, dependency resolution,
and a place in the install plan without touching the GUI.

### Components the registry currently declares

| Key | Component | Approximate size |
| --- | --- | --- |
| `micromamba` | Private package manager | 30 MB |
| `env:qc` | FastQC, fastp, MultiQC | 2 GB |
| `env:hostrem` | Bowtie2, samtools, bwa, fastp, pigz, seqkit | 1 GB |
| `env:taxonomy` | MetaPhlAn 4, Bowtie2 | 4 GB |
| `env:function` | HUMAnN 3, DIAMOND, Bowtie2 (Python 3.9) | 3 GB |
| `db:grch38` | GRCh38 primary assembly + Bowtie2 index | 6 GB |
| `db:metaphlan_chocophlan` | `mpa_vJan25_CHOCOPhlAnSGB_202503` markers | 33 GB |
| `db:humann_chocophlan` | HUMAnN nucleotide pangenomes | 17 GB |
| `db:humann_uniref50` | HUMAnN UniRef50 DIAMOND database | 6 GB |

Selecting a database automatically pulls in the environment whose tools install
it, so a user never has to reason about ordering.

### Planning and execution

`SetupManager.build_plan()` turns a selection into an ordered list of steps:
runtime first, then environments, then reference data. A step is either a
`CommandStep` (an external command, streamed line by line) or an `ActionStep`
(in-process Python, used for the Micromamba bootstrap and resumable HTTP
downloads so a bare machine needs no `curl` or `tar`).

`PlanExecutor` runs the plan in a worker thread, mirrors every line to the GUI
and to a timestamped file under `logs/`, and can be cancelled: it terminates the
running command, then kills it if it does not stop within ten seconds.

### Preflight

Before any download, BioFlow verifies the Python version, CPU count, installed
memory, that its data directory is writable, that free disk covers the selection
plus 15% headroom, and that package hosts are reachable. Failures marked
blocking prevent the install from starting rather than failing halfway through a
33 GB download.

## Execution engine

The setup engine provisions backends; the execution engine runs science with
them. They are separate subsystems that share only the configuration module.

```text
backend/samples.py                  Sample model, layout detection, FASTQ validation
backend/project.py                  Project metadata and bioflow-project.json
backend/execution/environment.py    Strict resolution against managed environments
backend/execution/workspace.py      Directory layout and inter-stage naming contract
backend/execution/stage.py          The Stage contract and validation helpers
backend/execution/stages/           One module per workflow step
backend/execution/runner.py         Runs one command, capturing everything
backend/execution/record.py         Stage records, validation results, fingerprints
backend/execution/pipeline.py       Ordering, dependency gate, failure, resume
```

### Environment resolution

A command resolves **only** to BioFlow's own environment:

```text
micromamba run -r <root> -n <environment> <tool> <arguments>
```

There is no fallback to `PATH` and none to a system Conda installation. A
missing backend raises `MissingBackend`, which names the setup components to
install. Two machines with the same BioFlow setup therefore run the same
binaries, and any result is traceable to the environment BioFlow installed.

### What a stage guarantees

Each stage declares its environment, its required databases, the commands it
runs for a given sample, the outputs it expects, and how to validate them.
Validation is content-aware rather than existence-based: FastQC archives must
open, fastp must report surviving reads, Bowtie2 must emit an alignment rate,
MetaPhlAn's profile must contain clade rows, HUMAnN's tables must contain data.

A stage succeeds only when its command exits zero **and** validation passes. A
tool that finishes without producing usable output is a failure.

### Failure and resume

A failed stage marks every later stage for that sample as `blocked`; other
samples continue, and the run as a whole is reported as failed. Every stage
records its command, arguments, environment, start and end time, exit code,
stdout and stderr paths, output paths, and each validation check, into
`bioflow-run.json`.

Resume reuses a previous result only when three conditions hold together: the
earlier run succeeded, the fingerprint of its inputs and commands is unchanged,
and its outputs validate at the moment of the retry. The fingerprint covers
input size and modification time, so replacing an input invalidates everything
downstream of it — something a file-existence check would miss.

### Single-end and paired-end

Both are first-class. Detection from `_R1`/`_R2` naming is a proposal the user
confirms or overrides in the interface. Pairing is preserved through trimming
and host removal, with mates kept in separate files. Two stages take a single
input because their tools require it, and only those two: MetaPhlAn receives
`R1,R2` as one comma-separated argument, and HUMAnN receives a concatenated
file, since it accepts only one input.

## Application architecture

```text
app/
├── main.py                         Application entry point
├── gui/
│   ├── main_window.py              Window layout, theme, sidebar navigation
│   ├── pages/
│   │   ├── setup_page.py           Backend provisioning, preflight, setup log
│   │   ├── pipeline_page.py        Sample selection, layout override, workflow run
│   │   ├── qc_tool_page.py         Shared single-tool UI and process execution
│   │   ├── fastqc_page.py          FastQC as a standalone job
│   │   ├── fastp_page.py           fastp as a standalone job
│   │   ├── multiqc_page.py         MultiQC as a standalone job
│   │   ├── host_removal_page.py    Batch Bowtie2 host removal as a standalone job
│   │   └── history_page.py         Persistent run-history interface
│   └── widgets/                    Sidebar and visual components
└── backend/
    ├── config.py                   Central path and environment configuration
    ├── samples.py                  Sample model and FASTQ validation
    ├── project.py                  Project metadata and workspace binding
    ├── logger.py                   Timestamped session log files
    ├── history.py                  Local SQLite run-history store
    ├── setup/                      Backend provisioning engine
    └── execution/                  Pipeline execution engine
```

### Runtime execution path

```text
Tool page
  → validates GUI selections
  → builds command arguments safely
  → resolves the program against BioFlow's own environments
  → QProcess starts the backend without freezing the GUI
  → stdout/stderr are streamed to the run log
  → a detailed log is written to disk
  → local SQLite run history is updated
  → result status and an output shortcut are shown in the GUI
```

## Data layout

```text
~/.local/share/bioflow/            (override with BIOFLOW_DATA_DIR)
├── bin/micromamba
├── micromamba-root/envs/          bioflow-qc, bioflow-hostrem, ...
├── databases/
│   ├── human/hg38/GRCh38_index.*
│   ├── metaphlan/
│   └── humann/{chocophlan,uniref}/
├── logs/                          Setup session logs
├── config.json                    User-adjustable settings
└── history.sqlite3                Run history
```

`config.json` holds the database root, default thread count, environment name
overrides, and explicitly configured external references. Every one of those is
read through `backend/config.py`; no module hard-codes a home directory, an
environment name, or a database path. The file is written on first run and
rewritten only when a setting actually changes.

```json
{
  "format_version": 1,
  "database_root": "<data_root>/databases",
  "default_threads": 4,
  "environment_names": { "qc": "bioflow-qc", "...": "..." },
  "references": { "grch38_index_prefix": null }
}
```

## Managed and external resources

BioFlow separates the copy of a resource it **installs and owns** from a copy
the user has **explicitly configured** elsewhere.

- **Installation** always targets the managed store. `managed_grch38_index_prefix`
  is not overridable by configuration or by any environment variable, so an
  external reference can never redirect a download or an index build.
- **Run-time resolution** may prefer an external copy, in this order: the
  development/test override `BIOFLOW_GRCH38_INDEX`, then the persisted external
  reference, then the managed installation. BioFlow never scans directories
  looking for a reference.
- **Status** reports the state and the path actually in use:
  `missing`, `incomplete`, `managed`, `external`, or `development`. A resource
  resolved from outside the managed store is never labelled as installed by
  BioFlow, and `ComponentStatus.location` always names the copy in use while
  `managed_location` names where BioFlow would install its own.

A GRCh38 index counts as valid only when all six Bowtie2 files exist for one
prefix (`.1`, `.2`, `.3`, `.4`, `.rev.1`, `.rev.2`, in `.bt2` or `.bt2l`). A
partial set is reported as `incomplete` rather than treated as usable.

`BIOFLOW_GRCH38_INDEX` exists for development and automated tests. It is never
persisted, never changes an install target, and never makes a resource appear
managed.

## What is implemented today

### Desktop application

A PyQt6 desktop GUI with a warm, vintage-inspired visual design: a sidebar with
Setup, Quality control, Host removal, and History sections; a responsive
execution log per module; user-selectable output locations; thread sliders
rather than command-line options; an animated nucleotide header; and
output-folder shortcuts after successful analyses. The window opens on the Setup
page until the core backend is installed.

### The workflow

The **Run workflow** page executes the documented pipeline: FastQC on raw reads,
fastp trimming, FastQC on trimmed reads, Bowtie2 host removal, MetaPhlAn
taxonomic profiling, HUMAnN functional profiling, and a MultiQC summary. Stages
are individually selectable, single-end and paired-end are both supported, and
the run refuses to start when a required backend is missing.

Verified with real tools through BioFlow's managed runtime: the quality-control
chain (FastQC, fastp, FastQC, MultiQC) runs end to end for both single-end and
paired-end input, resumes correctly, and reports failure when a tool fails or
its output does not validate. Host removal, MetaPhlAn and HUMAnN are implemented
and unit-tested against the specification but have not yet been executed
against their real databases.

### Individual tools

| Module | What it does | Environment |
| --- | --- | --- |
| FastQC | Per-read quality reports for selected FASTQ files. | `bioflow-qc` |
| fastp | Trims one single-end or one paired-end sample, with HTML/JSON reports. | `bioflow-qc` |
| MultiQC | Aggregates selected QC report files into a combined report. | `bioflow-qc` |

### Host removal

Bowtie2 against a GRCh38 index, supporting single-end and paired-end batches,
automatic R1/R2 matching, index completeness validation, per-sample logs, safe
output names for inputs containing spaces or parentheses, and sequential
execution so hundreds of samples do not start at once.

Paired-end:

```bash
bowtie2 --very-sensitive -p <threads> \
  -x <grch38_index_prefix> \
  -1 <sample_R1.fastq.gz> \
  -2 <sample_R2.fastq.gz> \
  --un-conc-gz <output>/<sample>_nohost_R%.fastq.gz \
  -S /dev/null
```

Single-end:

```bash
bowtie2 --very-sensitive -p <threads> \
  -x <grch38_index_prefix> \
  -U <sample.fastq.gz> \
  --un-gz <output>/<sample>_nohost.fastq.gz \
  -S /dev/null
```

### Run history and logs

Every execution records its start and finish time, module name, status, input
summary, output folder, exact generated command, log path, and exit code, in a
local SQLite database. No cloud service or external database is involved.

## Known hardware constraints

The full reference-data set is roughly 62 GB, so every database is opt-in and
gated behind a disk-space check.

HUMAnN's full three-stage mode (MetaPhlAn prescreen → ChocoPhlAn nucleotide
search → DIAMOND translated search) assumes an HPC-class machine. On a laptop or
workstation, BioFlow's HUMAnN module will default to the documented protein-only
mode (`--bypass-prescreen --bypass-nucleotide-search`), which needs only the
UniRef50 database. This is a supported HUMAnN mode, not a workaround, and the
trade-off must be stated in the interface and in generated reports: gene
families and pathway abundances are still produced, but gene calls are not
attributed to individual species.

## Recommended roadmap

### Milestone 1 — Make the current foundation release-ready

- Batch fastp so it matches the FastQC and host-removal pages.
- Improve MultiQC validation: recognise FastQC ZIP files and warn clearly when
  no parseable report data is found.
- Extract and display the Bowtie2 overall alignment rate in the result card.
- Add cancellation and retry controls to running analysis jobs.
- Add automated tests for command construction, paired-read matching, index
  validation, setup planning, and history.
- Build and publish the AppImage.

### Milestone 2 — Project workspace

- New/Open Project wizard writing a portable `bioflow-project.json`.
- Sample table that detects single/paired reads and flags unmatched files.
- Store metadata, options, input checksums, and run records with the results.
- Resume-from-last-successful-step and re-run-failed-step.

### Milestone 3 — Taxonomic profiling

- Taxonomic Profiling sidebar section driven by the `taxonomy` environment and
  the `metaphlan_chocophlan` database already declared in the registry.
- Run MetaPhlAn offline from host-removed reads, producing a profile table and a
  Bowtie2 map file per sample.
- Abundance tables, composition plots, and per-sample summaries.

### Milestone 4 — Functional profiling

- HUMAnN module defaulting to protein-only mode, with an explicit opt-in to full
  mode when the ChocoPhlAn database is installed.
- Concatenate paired host-removed reads before running HUMAnN.
- Post-processing: `humann_renorm_table` (CPM) and `humann_regroup_table`.
- Merge gene-family and pathway tables across samples.

### Milestone 5 — Reporting and reproducibility

- One publication-ready HTML report per project: QC status, host-removal rate,
  taxonomy, functional pathways, methods, parameters, software versions, and
  database versions.
- HTML/PDF export and downloadable tables and figures.
- Any AI-written summary clearly labelled and traceable to the underlying data.

### Milestone 6 — Scale and optional modules

- Optional HPC/SLURM and Nextflow execution adapters behind the same interface.
- Optional plugins: AMR (RGI/CARD, VFDB, PlasmidFinder), MAG assembly
  (MEGAHIT, MetaBAT2, CheckM), Kraken2/Bracken, differential abundance.

## Engineering principles

- Never hard-code a user's home directory, database path, or environment path.
- Declare a new backend in the setup registry; do not add ad-hoc install code.
- Prefer safe defaults, but expose advanced parameters when needed.
- Keep results, logs, commands, tool versions, and database versions recoverable
  and portable with the project.
- Treat an exit code of zero as necessary but not sufficient: inspect output
  where tools can finish without producing a useful result.
- Keep generated FASTQ files, reports, logs, and analysis outputs out of Git.
- Add a module only when its complete path is supported: setup, validation,
  execution, output review, history, and reproducibility record.
