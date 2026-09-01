"""FASTQ discovery, validation, and the single-end / paired-end sample model.

Single-end and paired-end are equally first-class throughout BioFlow. Pairing may
be detected from R1/R2 naming, but the detected layout is always presented to the
user as a proposal they can confirm or override.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import gzip
import re
import zlib


FASTQ_EXTENSIONS = (".fastq.gz", ".fq.gz", ".fastq", ".fq")

#: Qt file-dialog filter, kept beside the extensions it must stay in step with.
FASTQ_FILE_FILTER = "FASTQ files (*.fastq *.fastq.gz *.fq *.fq.gz)"

#: Matches the mate marker in names such as sample_R1, sample.1, sample-2.
_MATE_MARKER = re.compile(r"(?:[_.-])(R?[12])(?=[_.-]|$)", re.IGNORECASE)


class ReadLayout(str, Enum):
    """How a sample's reads are laid out on disk."""

    SINGLE = "single"
    PAIRED = "paired"

    @property
    def label(self) -> str:
        return "Single-end" if self is ReadLayout.SINGLE else "Paired-end"


@dataclass(frozen=True)
class Sample:
    """One biological sample and the FASTQ file(s) that hold its reads."""

    name: str
    layout: ReadLayout
    read1: Path
    read2: Path | None = None

    def __post_init__(self):
        if self.layout is ReadLayout.PAIRED and self.read2 is None:
            raise ValueError(f"Paired-end sample '{self.name}' is missing its R2 file.")
        if self.layout is ReadLayout.SINGLE and self.read2 is not None:
            raise ValueError(f"Single-end sample '{self.name}' must not carry an R2 file.")

    @property
    def is_paired(self) -> bool:
        return self.layout is ReadLayout.PAIRED

    def reads(self) -> list[Path]:
        """Every input file for this sample, in mate order."""
        return [self.read1] if self.read2 is None else [self.read1, self.read2]

    def describe(self) -> str:
        if self.is_paired:
            return f"{self.name} (paired-end: {self.read1.name}, {self.read2.name})"
        return f"{self.name} (single-end: {self.read1.name})"


@dataclass
class DetectionResult:
    """Samples found in a file selection, plus anything the user must resolve."""

    samples: list[Sample] = field(default_factory=list)
    #: Files that could not be turned into a sample, with the reason why.
    problems: list[str] = field(default_factory=list)
    #: Files left over, so the interface can show exactly what was ignored.
    unassigned: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.samples) and not self.problems


def strip_fastq_extension(name: str) -> str:
    """Remove a FASTQ extension, longest form first."""
    lowered = name.lower()
    for extension in FASTQ_EXTENSIONS:
        if lowered.endswith(extension):
            return name[: -len(extension)]
    return name


def is_fastq_path(path: Path) -> bool:
    return path.name.lower().endswith(FASTQ_EXTENSIONS)


def split_mate_marker(stem: str) -> tuple[str, str | None]:
    """Split 'sample_R1' into ('sample', '1'); return (stem, None) when absent."""
    match = None
    for candidate in _MATE_MARKER.finditer(stem):
        match = candidate  # The mate marker is the last one in the name.
    if match is None:
        return stem, None
    base = f"{stem[: match.start()]}{stem[match.end():]}"
    return base, match.group(1)[-1]


def sample_name_for(path: Path) -> str:
    """Sample name for a single-end file: the stem without any mate marker."""
    base, _mate = split_mate_marker(strip_fastq_extension(path.name))
    return base.strip("._-") or strip_fastq_extension(path.name)


def detect_layout(paths: list[Path]) -> ReadLayout:
    """Propose a layout from naming alone, without deciding for the user."""
    groups: dict[str, set[str]] = {}
    for path in paths:
        base, mate = split_mate_marker(strip_fastq_extension(path.name))
        if mate:
            groups.setdefault(base.lower(), set()).add(mate)
    complete_pairs = sum(1 for mates in groups.values() if {"1", "2"} <= mates)
    return ReadLayout.PAIRED if complete_pairs else ReadLayout.SINGLE


def detect_samples(paths: list[Path], layout: ReadLayout | None = None) -> DetectionResult:
    """Group files into samples for a chosen layout, or a detected one.

    Passing an explicit layout is how the user overrides detection; the result
    then reports why any file does not fit that choice rather than silently
    switching modes.
    """
    result = DetectionResult()
    candidates: list[Path] = []
    for path in paths:
        if is_fastq_path(path):
            candidates.append(path)
        else:
            result.problems.append(f"{path.name}: not a FASTQ file (expected .fastq/.fq, optionally .gz)")
            result.unassigned.append(path)
    if not candidates:
        if not result.problems:
            result.problems.append("No FASTQ files were selected.")
        return result

    resolved_layout = layout or detect_layout(candidates)
    if resolved_layout is ReadLayout.SINGLE:
        # In single-end mode every file is its own sample, so the mate marker is
        # part of the name: forcing single-end on a_R1/a_R2 must give two
        # distinct samples rather than two samples both called "a".
        for path in sorted(candidates):
            result.samples.append(
                Sample(
                    name=strip_fastq_extension(path.name).strip("._-"),
                    layout=ReadLayout.SINGLE,
                    read1=path,
                )
            )
        _reject_duplicate_names(result)
        return result

    groups: dict[str, dict[str, Path]] = {}
    for path in sorted(candidates):
        base, mate = split_mate_marker(strip_fastq_extension(path.name))
        if mate is None:
            result.problems.append(
                f"{path.name}: no R1/R2 marker, so it cannot be used as paired-end input"
            )
            result.unassigned.append(path)
            continue
        mates = groups.setdefault(base.strip("._-").lower() or base, {})
        if mate in mates:
            result.problems.append(
                f"{path.name}: a second R{mate} file for sample '{base}' "
                f"(already have {mates[mate].name})"
            )
            result.unassigned.append(path)
            continue
        mates[mate] = path

    for base, mates in groups.items():
        if "1" in mates and "2" in mates:
            name = sample_name_for(mates["1"])
            result.samples.append(
                Sample(name=name, layout=ReadLayout.PAIRED, read1=mates["1"], read2=mates["2"])
            )
        else:
            present = mates["1"] if "1" in mates else mates["2"]
            missing = "R2" if "1" in mates else "R1"
            result.problems.append(
                f"{present.name}: {missing} is missing for sample '{base}'. "
                f"Add it, or switch this run to single-end."
            )
            result.unassigned.append(present)

    _reject_duplicate_names(result)
    return result


def _reject_duplicate_names(result: DetectionResult) -> None:
    """Two samples with the same name would overwrite each other's outputs."""
    seen: dict[str, Sample] = {}
    unique: list[Sample] = []
    for sample in result.samples:
        if sample.name in seen:
            result.problems.append(
                f"{sample.read1.name}: sample name '{sample.name}' is already used by "
                f"{seen[sample.name].read1.name}; rename one of the files."
            )
            result.unassigned.append(sample.read1)
            continue
        seen[sample.name] = sample
        unique.append(sample)
    result.samples = unique


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------
GZIP_MAGIC = b"\x1f\x8b"


@dataclass(frozen=True)
class FastqValidation:
    """The outcome of inspecting one FASTQ file before an analysis starts."""

    path: Path
    valid: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.valid


def _open_reads(path: Path):
    if path.name.lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("rt", encoding="utf-8", errors="replace")


def read_identifier(header: str) -> str:
    """Normalise a FASTQ header to the part shared by both mates of a pair."""
    name = header[1:].split()[0] if header.startswith("@") else header.split()[0]
    for suffix in ("/1", "/2", ".1", ".2"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def validate_fastq_file(path: Path, records_to_check: int = 2) -> FastqValidation:
    """Check that a file is a readable FASTQ before committing to a long run.

    This deliberately reads only the first few records: it catches truncated
    downloads, wrong file types, and mis-named text files without paying to
    stream a multi-gigabyte file.
    """
    if not path.exists():
        return FastqValidation(path, False, "file does not exist")
    if not path.is_file():
        return FastqValidation(path, False, "not a regular file")
    if path.stat().st_size == 0:
        return FastqValidation(path, False, "file is empty")
    if not is_fastq_path(path):
        return FastqValidation(path, False, "unsupported extension (expected .fastq/.fq, optionally .gz)")

    if path.name.lower().endswith(".gz"):
        with path.open("rb") as handle:
            if handle.read(2) != GZIP_MAGIC:
                return FastqValidation(path, False, "named .gz but is not gzip-compressed")

    try:
        with _open_reads(path) as handle:
            for record_index in range(records_to_check):
                lines = [handle.readline() for _ in range(4)]
                if not lines[0]:
                    if record_index == 0:
                        return FastqValidation(path, False, "contains no reads")
                    break  # A short file is fine as long as its records are whole.
                if any(line == "" for line in lines):
                    return FastqValidation(
                        path, False, f"truncated: record {record_index + 1} is incomplete"
                    )
                header, sequence, separator, quality = (line.rstrip("\n") for line in lines)
                if not header.startswith("@"):
                    return FastqValidation(
                        path, False, f"record {record_index + 1} header does not start with '@'"
                    )
                if not separator.startswith("+"):
                    return FastqValidation(
                        path, False, f"record {record_index + 1} separator does not start with '+'"
                    )
                if len(sequence) != len(quality):
                    return FastqValidation(
                        path,
                        False,
                        f"record {record_index + 1}: sequence is {len(sequence)} bases but "
                        f"quality is {len(quality)} characters",
                    )
    except (OSError, EOFError, gzip.BadGzipFile, zlib.error) as error:
        return FastqValidation(path, False, f"unreadable: {error}")

    return FastqValidation(path, True)


def validate_sample(sample: Sample, check_pairing: bool = True) -> list[str]:
    """Validate a sample's files, including that its mates actually correspond."""
    problems: list[str] = []
    for path in sample.reads():
        outcome = validate_fastq_file(path)
        if not outcome.valid:
            problems.append(f"{path.name}: {outcome.reason}")
    if problems or not (check_pairing and sample.is_paired):
        return problems

    try:
        with _open_reads(sample.read1) as first, _open_reads(sample.read2) as second:
            header_one, header_two = first.readline().strip(), second.readline().strip()
    except (OSError, EOFError, gzip.BadGzipFile, zlib.error) as error:
        return [f"{sample.name}: could not compare mates ({error})"]

    if header_one and header_two:
        identifier_one, identifier_two = read_identifier(header_one), read_identifier(header_two)
        if identifier_one != identifier_two:
            problems.append(
                f"{sample.name}: R1 and R2 do not look like mates "
                f"(first read is '{identifier_one}' in R1 but '{identifier_two}' in R2)"
            )
    return problems
