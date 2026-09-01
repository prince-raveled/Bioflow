"""The contract every pipeline stage implements."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import gzip
import os
import shutil

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

    @property
    def threads(self) -> str:
        return str(max(1, self.options.threads))


class Stage(ABC):
    """A single step of the metagenomic workflow, for one sample."""

    key: str = ""
    title: str = ""
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


def check_gzip_readable(result: ValidationResult, path: Path) -> bool:
    """Assert a gzip file can actually be decompressed, catching truncation."""
    try:
        with gzip.open(path, "rb") as handle:
            handle.read(1024)
    except (OSError, EOFError) as error:
        result.add(f"{path.name} is readable", False, str(error))
        return False
    result.add(f"{path.name} is readable", True)
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
