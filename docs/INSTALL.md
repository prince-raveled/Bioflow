# Installing BioFlow

BioFlow analyses shotgun metagenomic reads:

    FASTQ -> FastQC -> fastp -> host removal -> MetaPhlAn -> MultiQC

Single-end and paired-end input are both supported.

It installs its own analysis backend. There is no system Conda to configure and
no bioinformatics tool to install by hand: BioFlow downloads a private
Micromamba, builds the environments it needs, and fetches the reference data.

---

## 1. The application

    scripts/install_bioflow_linux.sh

This creates only the desktop Python environment, under
`~/.local/share/bioflow/python`. It checks two things first, because both fail
later with messages that do not name the cause:

- the XCB and xkbcommon libraries PyQt6's wheel links against, which the
  distribution owns rather than pip
- `python3-venv`, which minimal Ubuntu images omit

If either is missing the script prints the exact `apt` or `dnf` command and
stops.

Start BioFlow with:

    scripts/run_bioflow_linux.sh

## 2. The analysis backend

From **Setup & Resources** inside the application, or headlessly:

    cd app
    ~/.local/share/bioflow/python/bin/python -m backend.setup.cli --status
    ~/.local/share/bioflow/python/bin/python -m backend.setup.cli --install env:qc env:hostrem db:grch38

Components:

| key | what it is | approximate size |
|---|---|---|
| `micromamba` | the private package runtime | 20 MB |
| `env:qc` | FastQC, fastp, MultiQC | 2 GB |
| `env:hostrem` | Bowtie2, samtools, bwa, pigz, seqkit | 1 GB |
| `env:taxonomy` | MetaPhlAn and its Bowtie2 | 4 GB |
| `db:grch38` | human reference, Bowtie2 index | ~4 GB |
| `db:metaphlan_chocophlan` | MetaPhlAn marker database | ~51 GB |

Setup is resumable. An interrupted download continues where it stopped, and a
partially unpacked database is reported as incomplete and repaired rather than
treated as absent.

---

## Reference databases

Databases live outside the application, under
`~/.local/share/bioflow/databases` by default. They survive upgrades, are shared
between the native and container backends, and can be shared between machines.

### Pointing at data you already have

If GRCh38 or the MetaPhlAn database is already on this machine or on shared
storage, do not download it again.

- **GRCh38** — Setup & Resources, "Use existing index...", and select any file
  of the six-file Bowtie2 index. BioFlow verifies all six are present before
  saving the choice, so a mistyped path cannot be stored as a working reference.
- **MetaPhlAn** — set `BIOFLOW_METAPHLAN_DB` to the directory holding it.

Both are checked for completeness wherever they point. Redirecting is never a
way past that check: an incomplete database is reported as incomplete.

### Putting the databases on another disk

The MetaPhlAn database is around 51 GB, and free space is checked before the
download starts rather than part-way through it. Two ways to place it:

- move everything, by setting `BIOFLOW_DATA_DIR` to a directory on the larger
  filesystem before starting BioFlow
- move only the databases, by setting `database_root` in
  `~/.local/share/bioflow/config.json`

## Environment variables

None of these are required. Each overrides a location for one process without
changing what is saved.

| variable | what it moves |
|---|---|
| `BIOFLOW_DATA_DIR` | everything BioFlow owns: runtime, environments, databases, history |
| `BIOFLOW_METAPHLAN_DB` | the MetaPhlAn database directory |
| `BIOFLOW_GRCH38_INDEX` | a GRCh38 index, reported as a development override |
| `BIOFLOW_EXECUTION_BACKEND` | forces `native` or `container` for one process |
| `BIOFLOW_HISTORY_DB` | the run-history database |
| `BIOFLOW_MICROMAMBA`, `BIOFLOW_MAMBA_ROOT_PREFIX` | the package runtime and its root |
| `BIOFLOW_NATIVE_DIALOGS` | forces the native or Qt file dialog |

An unrecognised value for `BIOFLOW_EXECUTION_BACKEND` is ignored rather than
refusing to start.

---

## Memory

MetaPhlAn's marker table needs about 7 GB before a single read is aligned, and
the Bowtie2 index it searches is 33 GB.

Below **24 GB of RAM**, BioFlow memory-maps that index instead of loading it and
caps MetaPhlAn at one thread. That is a rescue, not an optimisation: on a 14 GB
machine it is the difference between a run that finishes and one the kernel
kills. Above 24 GB the index is loaded normally and the thread count you choose
is used.

**Disk swap matters.** A machine with only zram and no swap file had MetaPhlAn
killed twice at 6.8 GB; the same command on the same machine with a 16 GB swap
file ran to completion. zram is compressed memory living in RAM, and the largest
thing BioFlow runs compresses badly. Preflight distinguishes the two and says so.

If BioFlow is started from an editor's built-in terminal, its processes may
inherit a positive `oom_score_adj`, which makes the kernel prefer to kill them
even when memory is available. Start it from an ordinary terminal or its desktop
entry. BioFlow warns before profiling rather than after a run dies.

---

## Container execution (optional)

Analysis runs on this machine by default, and that stays the default. It needs
nothing beyond what BioFlow installs, which is what makes a fresh Linux machine
work without asking anything of you.

Containers are worth having for three reasons, none of them "it is more modern":

- **A frozen userland.** A pinned package specification still solves against a
  live channel; an image digest is a filesystem, identical forever.
- **Insulation from the host.** FastQC needs a JRE and bioconda binaries assume
  a glibc floor. On an old or very new distribution, native installs break in
  ways that are painful to diagnose remotely.
- **A path to clusters.** The same OCI image converts to Apptainer.

### Using it

    docker/build.sh

Then, in Setup & Resources, set **Analysis environment** to "In a container".
Standalone tool pages follow the same setting as the workflow.

Podman is preferred and is what Fedora and RHEL ship: no daemon, and rootless it
needs no group membership. Docker works identically - the image is OCI, not
Docker-specific - but note that membership of the `docker` group is equivalent
to root on that machine.

### What the container may do

Every analysis container runs with no network, no capabilities, a read-only root
filesystem, a writable tmpfs on `/tmp`, and as you rather than as root. Reference
databases are mounted read-only; results are mounted writable. Nothing else is
visible to it. BioFlow never uses `--privileged`, never `--network=host`, and
never mounts a container runtime socket.

Host paths are mounted at the same path inside the container, so logs, tool
error messages and the paths written into MultiQC reports all name directories
you recognise.

### On SELinux systems

Fedora and RHEL enforce SELinux labels on bind mounts. BioFlow drops SELinux
confinement for the container rather than relabelling the mounts: relabelling
would rewrite the labels of a 51 GB database, and the private-label form would
leave that database unreadable by the native backend. No host label is changed.

### Where data cannot live

A directory cannot be mounted over the container's own filesystem, so input or
results directly under `/usr`, `/etc`, `/var`, `/opt`, `/bin`, `/lib`, `/proc`,
`/sys`, `/dev`, `/root` or `/tmp` cannot be used in container mode. BioFlow says
so and names the alternatives. `/home`, `/mnt`, `/media`, `/run/media`, `/data`
and `/srv` are all fine.

---

## Network

| | needs the network | does not |
|---|---|---|
| Installing the application | yes | |
| Building environments / downloading databases | yes, once | |
| Pulling or building the container image | yes, once | |
| **Running an analysis** | | **no** |

After installation a complete analysis needs no network at all. In container
mode this is enforced rather than promised: the container is given no network
interface.

### Air-gapped installation

Both backends can be installed without the machine ever being online:

- copy a database directory across and point BioFlow at it, as above
- move the image with `podman save` / `podman load`

---

## Reproducibility

Every run records what produced it: the BioFlow version, the execution backend,
the container image by digest where one applies, the MetaPhlAn index name, and
the exact command each stage ran.

Two runs of the same analysis on the same data produce the same result. That is
not automatic - Bowtie2 with several threads writes surviving reads in whatever
order its threads finish in, and MetaPhlAn's subsampling then draws different
reads from a differently ordered file - so BioFlow passes `--reorder` and states
the subsampling seed rather than inheriting it.

Results are reused between runs only when the inputs, the command and the
execution backend are all unchanged, and the outputs still verify. Switching
between native and container execution re-runs the analysis rather than mixing
results from both.

---

## Uninstalling

Everything BioFlow installs is under one directory:

    rm -rf ~/.local/share/bioflow

Remove the container image, if you built one, with
`podman rmi localhost/bioflow-tools:0.1.0`. Your reads and results are wherever
you put them and are never inside that directory.
