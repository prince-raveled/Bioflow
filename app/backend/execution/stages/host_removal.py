"""Removal of human reads with Bowtie2 against GRCh38."""

from pathlib import Path
import re
import shlex

from backend.execution.record import ValidationResult
from backend.execution.stage import (
    RunContext,
    Stage,
    StageCommand,
    check_exists,
    check_gzip_readable,
)
from backend.samples import Sample


ALIGNMENT_RATE = re.compile(r"([\d.]+)%\s+overall alignment rate")

#: Characters that make /bin/sh reinterpret a word rather than take it literally.
_SHELL_SENSITIVE = set(" \t\n\"'`$&|;<>()[]{}*?!~#\\")


def gzip_output_argument(path: Path) -> str:
    """Quote a Bowtie2 gzip-output path so its own shell call parses it whole.

    BioFlow always executes tools through an argument list, never a shell. But
    Bowtie2's --un-gz / --un-conc-gz options are different: Bowtie2 takes the
    value and builds the shell command `gzip -c >VALUE`, which it runs through
    `sh -c` without quoting it. A path containing a space or a parenthesis is
    then split by that shell, and the run dies with a syntax error before any
    reads are written.

    Quoting only where the value genuinely needs it keeps ordinary paths
    byte-for-byte unchanged, so nothing about the common case is altered.
    """
    text = str(path)
    if not any(character in _SHELL_SENSITIVE for character in text):
        return text
    return shlex.quote(text)


class HostRemovalStage(Stage):
    """Keep the reads that do not align to the human reference.

    Single-end uses -U with --un-gz, producing one file. Paired-end uses -1/-2
    with --un-conc-gz and Bowtie2's %  template, which writes the two mate files
    separately and only keeps pairs where neither mate aligned.
    """

    key = "host_removal"
    title = "Host removal (Bowtie2)"
    environment_key = "hostrem"
    required_databases = ("grch38",)

    def inputs(self, sample: Sample, context: RunContext) -> list[Path]:
        return context.workspace.trimmed_reads(sample)

    def commands(self, sample: Sample, context: RunContext) -> list[StageCommand]:
        if context.host_index_prefix is None:
            raise ValueError("No GRCh38 Bowtie2 index is configured for host removal.")
        workspace = context.workspace
        trimmed = self.inputs(sample, context)

        command = [
            "bowtie2",
            "--very-sensitive",
            "-p",
            context.threads,
            "-x",
            str(context.host_index_prefix),
        ]
        if sample.is_paired:
            command += [
                "-1",
                str(trimmed[0]),
                "-2",
                str(trimmed[1]),
                "--un-conc-gz",
                gzip_output_argument(workspace.host_removed_pattern(sample)),
            ]
        else:
            command += [
                "-U",
                str(trimmed[0]),
                "--un-gz",
                gzip_output_argument(workspace.host_removed_reads(sample)[0]),
            ]
        command += ["-S", "/dev/null"]

        layout = "paired-end" if sample.is_paired else "single-end"
        return [
            StageCommand(
                description=f"Remove human reads from {sample.name} ({layout})",
                command=command,
                environment_key=self.environment_key,
                # Bowtie2 prints its alignment summary to stderr.
                stderr_to=workspace.bowtie2_log(sample),
            )
        ]

    def outputs(self, sample: Sample, context: RunContext) -> list[Path]:
        workspace = context.workspace
        return [*workspace.host_removed_reads(sample), workspace.bowtie2_log(sample)]

    @staticmethod
    def alignment_rate(log_path: Path) -> float | None:
        """Percentage of reads that aligned to the host, from the Bowtie2 log."""
        try:
            match = ALIGNMENT_RATE.search(log_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return None
        return float(match.group(1)) if match else None

    def validate(self, sample: Sample, context: RunContext) -> ValidationResult:
        workspace = context.workspace
        result = ValidationResult()

        for path in workspace.host_removed_reads(sample):
            if check_exists(result, path, minimum_bytes=32):
                check_gzip_readable(result, path)

        log_path = workspace.bowtie2_log(sample)
        if not check_exists(result, log_path, minimum_bytes=16):
            return result

        rate = self.alignment_rate(log_path)
        # Bowtie2 can exit zero after aligning nothing if the index is wrong, so
        # require the summary line that proves it actually processed the reads.
        result.add(
            "Bowtie2 reported an alignment rate",
            rate is not None,
            f"{rate}% of reads were host" if rate is not None else "no summary line in the log",
        )
        return result
