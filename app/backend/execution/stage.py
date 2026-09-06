"""The contract every pipeline stage implements."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import gzip
import os
import shutil
import zlib

from backend.execution.record import ValidationResult
from backend.execution.workspace import Workspace
from backend.samples import Sample


LogCallback = Callable[[str], None]


@dataclass(frozen=True)
class StageCommand:
    """One external command belonging to a stage."""

    description: str
    #: Tool name and arguments; the resolver wraps this in its environment.
    command: list[str]
    environment_key: str
    #: Redirect stdout to a file, for tools that print their result.
    stdout_to: Path | None = None
    #: Redirect stderr to a file, for tools whose summary goes to stderr.
    stderr_to: Path | None = None


@dataclass
class RunOptions:
    """User-adjustable execution settings for one analysis."""

    threads: int = 4
    #: Profile only this many read pairs, or None to use every read.
    #:
    #: MetaPhlAn's paired subsampling is a speed control, not a quality one: it
    #: throws reads away. Left unset, the whole sample is profiled.
    metaphlan_subsample_pairs: int | None = None
    #: HUMAnN's protein-only mode; the documented path for non-HPC hardware.
    humann_protein_only: bool = True
    #: Normalise HUMAnN tables to copies per million after profiling.
    humann_normalise: bool = True


@dataclass
class RunContext:
    """Everything a stage needs to build its commands."""

    workspace: Workspace
    options: RunOptions = field(default_factory=RunOptions)
    #: Bowtie2 index prefix for host removal; resolved from configuration.
    host_index_prefix: Path | None = None
    #: MetaPhlAn database directory and index name.
    metaphlan_database: Path | None = None
    metaphlan_index: str = ""
    #: Where the Bowtie2 shim lives, and what it needs to activate the
    #: environment. All resolved from configuration, so nothing here depends on
    #: where BioFlow was installed.
    bowtie2_memory_mapped_shim: Path | None = None
    micromamba_binary: Path | None = None
    micromamba_root: Path | None = None
    taxonomy_environment: str = ""

    @property
    def threads(self) -> str:
        return str(max(1, self.options.threads))


class Stage(ABC):
    """A single step of the metagenomic workflow, for one sample."""

    key: str = ""
    title: str = ""
    #: One word for the workflow strip in the header. Several stages can share
    #: one - both FastQC passes are "QC" - and repeats collapse when the strip
    #: is built, so it reads as the shape of the workflow rather than a list.
    short_title: str = ""
    #: Label for this stage's chip in the pipeline row, where every stage is
    #: shown separately and naming the tool is more use than naming the step.
    #: A second vocabulary on purpose, but owned here rather than in the widget:
    #: a map keyed by stage key in the interface is free to drift from the
    #: stages that actually exist, which is how the header came to advertise a
    #: stage the release had withheld.
    chip_title: str = ""
    environment_key: str = ""
    #: Setup database keys this stage cannot run without.
    required_databases: tuple[str, ...] = ()
    #: True when the stage runs once for the whole project rather than per sample.
    per_project: bool = False

    # ------------------------------------------------------------------
    @abstractmethod
    def inputs(self, sample: Sample, context: RunContext) -> list[Path]:
        """Files this stage reads. Used for checkpoint fingerprinting."""

    @abstractmethod
    def commands(self, sample: Sample, context: RunContext) -> list[StageCommand]:
        """The external commands to execute, in order."""

    @abstractmethod
    def outputs(self, sample: Sample, context: RunContext) -> list[Path]:
        """Files this stage is expected to produce."""

    @abstractmethod
    def validate(self, sample: Sample, context: RunContext) -> ValidationResult:
        """Check the outputs are genuinely usable, not merely present."""

    # ------------------------------------------------------------------
    def databases_for(self, context: RunContext) -> tuple[str, ...]:
        """Databases needed for this run; may depend on the chosen options."""
        return self.required_databases

    def prepare(self, sample: Sample, context: RunContext, log: LogCallback) -> None:
        """Optional in-process work before the commands run."""

    def output_directory(self, context: RunContext) -> Path:
        return context.workspace.root

    def ensure_directories(self, sample: Sample, context: RunContext) -> None:
        for path in self.outputs(sample, context):
            path.parent.mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------------
# Validation helpers shared by the concrete stages
# ----------------------------------------------------------------------
def check_exists(result: ValidationResult, path: Path, minimum_bytes: int = 1) -> bool:
    """Assert a file exists and is not a zero-length stub."""
    if not path.exists():
        result.add(f"{path.name} was produced", False, "file is missing")
        return False
    size = path.stat().st_size
    if size < minimum_bytes:
        result.add(f"{path.name} was produced", False, f"only {size} bytes")
        return False
    result.add(f"{path.name} was produced", True, f"{size} bytes")
    return True


#: Read size for verifying a compressed output. Large enough that decompressing
#: a multi-gigabyte FASTQ is bounded by throughput rather than syscalls.
_VERIFY_CHUNK = 4 * 1024 * 1024


def check_gzip_readable(result: ValidationResult, path: Path) -> bool:
    """Assert a gzip file decompresses completely and its checksum matches.

    The whole stream is read, not a sample of it. Reading only the first
    kilobyte accepted a file truncated anywhere after it, which is exactly the
    shape a stage interrupted part-way through writing leaves behind: the file
    exists, it is large, its first block decompresses, and the reads after the
    cut are simply gone. The stage was then recorded as complete and a resume
    trusted it, so an analysis could run to a confident finish on a fraction of
    the data with nothing anywhere reporting a problem.

    Reading to the end also makes the gzip module verify the trailing CRC and
    length, so corruption in the middle is caught as well as truncation.
    """
    read = 0
    try:
        with gzip.open(path, "rb") as handle:
            while chunk := handle.read(_VERIFY_CHUNK):
                read += len(chunk)
    except (OSError, EOFError, zlib.error) as error:
        result.add(
            f"{path.name} is complete and readable",
            False,
            f"{error} (after {read} decompressed bytes)",
        )
        return False
    if read == 0:
        result.add(f"{path.name} is complete and readable", False, "decompressed to nothing")
        return False
    result.add(f"{path.name} is complete and readable", True, f"{read} bytes of reads")
    return True


def count_data_rows(path: Path, comment_prefix: str = "#") -> int:
    """Count non-comment, non-blank lines, for table-shaped outputs."""
    rows = 0
    try:
        with path.open("rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped and not stripped.startswith(comment_prefix):
                    rows += 1
    except OSError:
        return 0
    return rows


def concatenate_gzip(sources: list[Path], destination: Path, log: LogCallback) -> None:
    """Concatenate FASTQ files into one gzip stream.

    Only used where the tool itself requires a single input file (HUMAnN);
    every other stage keeps paired mates separate.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".partial")
    log(f"Combining {len(sources)} file(s) into {destination.name} for HUMAnN")
    with temporary.open("wb") as output:
        for source in sources:
            if source.name.lower().endswith(".gz"):
                # Concatenated gzip members are a valid gzip stream.
                with source.open("rb") as handle:
                    shutil.copyfileobj(handle, output, length=1024 * 1024)
            else:
                with source.open("rb") as handle, gzip.GzipFile(fileobj=output, mode="wb") as packer:
                    shutil.copyfileobj(handle, packer, length=1024 * 1024)
    os.replace(temporary, destination)
    log(f"Combined input ready: {destination} ({destination.stat().st_size} bytes)")
