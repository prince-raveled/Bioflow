"""Read-quality stages: FastQC before and after trimming, and MultiQC."""

from pathlib import Path
import zipfile

from backend.execution.record import ValidationResult
from backend.execution.stage import (
    RunContext,
    Stage,
    StageCommand,
    check_exists,
)
from backend.samples import Sample, strip_fastq_extension


def fastqc_basename(path: Path) -> str:
    """FastQC names its outputs after the file with its FASTQ extension removed."""
    return strip_fastq_extension(path.name)


class FastQCStage(Stage):
    """Run FastQC over a sample's reads.

    Single-end passes one file, paired-end passes both mates as separate files:
    FastQC reports on each mate independently, so pairing is never collapsed.
    """

    environment_key = "qc"
    short_title = "QC"
    chip_title = "FastQC"

    def __init__(self, key: str, title: str, source: str, directory: str):
        self.key = key
        self.title = title
        #: "raw" reads the sample's inputs; "trimmed" reads fastp's output.
        self._source = source
        self._directory = directory

    def _directory_for(self, context: RunContext) -> Path:
        return getattr(context.workspace, self._directory)

    def inputs(self, sample: Sample, context: RunContext) -> list[Path]:
        if self._source == "raw":
            return sample.reads()
        return context.workspace.trimmed_reads(sample)

    def report_command(
        self,
        reads: list[Path],
        directory: Path,
        context: RunContext,
        label: str = "",
    ) -> StageCommand:
        """Build one FastQC invocation over the given files.

        Separate from commands() so the standalone FastQC page reports on files
        the user picked without a second copy of the flags. The two callers
        differ only in which files are read and where the reports go.
        """
        named = f" ({label})" if label else ""
        return StageCommand(
            description=f"FastQC on {len(reads)} file(s){named}",
            command=[
                "fastqc",
                "--threads",
                context.threads,
                "--outdir",
                str(directory),
                *[str(path) for path in reads],
            ],
            environment_key=self.environment_key,
        )

    def commands(self, sample: Sample, context: RunContext) -> list[StageCommand]:
        return [
            self.report_command(
                self.inputs(sample, context), self._directory_for(context), context
            )
        ]

    def outputs(self, sample: Sample, context: RunContext) -> list[Path]:
        directory = self._directory_for(context)
        produced: list[Path] = []
        for path in self.inputs(sample, context):
            base = fastqc_basename(path)
            produced.append(directory / f"{base}_fastqc.html")
            produced.append(directory / f"{base}_fastqc.zip")
        return produced

    def validate(self, sample: Sample, context: RunContext) -> ValidationResult:
        result = ValidationResult()
        for path in self.outputs(sample, context):
            if not check_exists(result, path, minimum_bytes=256):
                continue
            if path.suffix == ".zip":
                # A truncated archive is the usual sign of an interrupted run.
                try:
                    with zipfile.ZipFile(path) as archive:
                        broken = archive.testzip()
                    result.add(f"{path.name} is a complete archive", broken is None, broken or "")
                except zipfile.BadZipFile as error:
                    result.add(f"{path.name} is a complete archive", False, str(error))
        return result


def fastqc_raw_stage() -> FastQCStage:
    return FastQCStage("fastqc_raw", "FastQC (raw reads)", "raw", "qc_raw")


def fastqc_trimmed_stage() -> FastQCStage:
    return FastQCStage("fastqc_trimmed", "FastQC (trimmed reads)", "trimmed", "qc_trimmed")


class MultiQCStage(Stage):
    """Aggregate every QC report produced by the run into one document."""

    key = "multiqc"
    title = "MultiQC report"
    short_title = "Report"
    chip_title = "MultiQC"
    environment_key = "qc"
    per_project = True

    def inputs(self, sample: Sample, context: RunContext) -> list[Path]:
        # Scanned recursively, so the fingerprint tracks the report files found.
        workspace = context.workspace
        found: list[Path] = []
        for directory in (workspace.qc_raw, workspace.qc_trimmed, workspace.trimmed):
            if directory.is_dir():
                found.extend(sorted(directory.glob("*.zip")))
                found.extend(sorted(directory.glob("*.json")))
        return found

    def aggregate_command(
        self, sources: list[Path], directory: Path, label: str = ""
    ) -> StageCommand:
        """Build one MultiQC invocation over the given sources.

        Separate from commands() so the standalone MultiQC page aggregates
        reports the user picked without a second copy of the flags. MultiQC
        accepts directories and files alike, which is the only way the two
        callers differ: the workflow hands it the three directories it filled,
        the page hands it the files someone chose.
        """
        named = f" ({label})" if label else ""
        return StageCommand(
            description=f"Aggregate QC reports with MultiQC{named}",
            command=[
                "multiqc",
                *[str(path) for path in sources],
                "--outdir",
                str(directory),
                "--force",
            ],
            environment_key=self.environment_key,
        )

    def commands(self, sample: Sample, context: RunContext) -> list[StageCommand]:
        workspace = context.workspace
        return [
            self.aggregate_command(
                [workspace.qc_raw, workspace.qc_trimmed, workspace.trimmed],
                workspace.multiqc,
            )
        ]

    def outputs(self, sample: Sample, context: RunContext) -> list[Path]:
        return [context.workspace.multiqc_report()]

    def validate(self, sample: Sample, context: RunContext) -> ValidationResult:
        result = ValidationResult()
        report = context.workspace.multiqc_report()
        # MultiQC writes a report even when it parses nothing, so require substance.
        check_exists(result, report, minimum_bytes=10_000)
        return result
