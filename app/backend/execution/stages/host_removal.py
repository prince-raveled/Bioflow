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
    short_title = "Host"
    chip_title = "Host"
    environment_key = "hostrem"
    required_databases = ("grch38",)

    def inputs(self, sample: Sample, context: RunContext) -> list[Path]:
        return context.workspace.trimmed_reads(sample)

    def removal_command(
        self,
        reads: list[Path],
        unmatched: Path,
        log: Path,
        context: RunContext,
        paired: bool,
        label: str = "",
    ) -> StageCommand:
        """Build one Bowtie2 host-removal invocation for the given reads.

        Separate from commands() so the standalone host-removal page filters
        files the user picked without a second copy of the flags. `unmatched` is
        Bowtie2's output template: for a pair the `%` pattern --un-conc-gz
        expands into two mate files, for a single sample the one file --un-gz
        writes. Getting that distinction wrong is how a paired run silently
        produces a single interleaved file, so it is decided in one place.
        """
        if context.host_index_prefix is None:
            raise ValueError("No GRCh38 Bowtie2 index is configured for host removal.")

        command = [
            "bowtie2",
            "--very-sensitive",
            # Without this, the surviving reads come out in whatever order the
            # threads finished in, so two identical runs write the same reads
            # to different lines of the file. That is invisible here - the
            # reads are the same and every check still passes - and it becomes
            # visible one stage later, because MetaPhlAn's subsampling draws
            # from the file in order. Two runs of the same analysis then
            # profile different reads and report different abundances.
            #
            # Measured on 20,000 pairs with -p 8: two runs without --reorder
            # produced different read orders, two runs with it produced
            # identical ones, and all four contained exactly the same reads.
            # It costs some buffering; a result that cannot be reproduced costs
            # more.
            "--reorder",
            "-p",
            context.threads,
            "-x",
            str(context.host_index_prefix),
        ]
        if paired:
            command += [
                "-1",
                str(reads[0]),
                "-2",
                str(reads[1]),
                "--un-conc-gz",
                gzip_output_argument(unmatched),
            ]
        else:
            command += [
                "-U",
                str(reads[0]),
                "--un-gz",
                gzip_output_argument(unmatched),
            ]
        command += ["-S", "/dev/null"]

        layout = "paired-end" if paired else "single-end"
        named = f"{label} " if label else ""
        return StageCommand(
            description=f"Remove human reads from {named}({layout})",
            command=command,
            environment_key=self.environment_key,
            # Bowtie2 prints its alignment summary to stderr.
            stderr_to=log,
        )

    def commands(self, sample: Sample, context: RunContext) -> list[StageCommand]:
        workspace = context.workspace
        unmatched = (
            workspace.host_removed_pattern(sample)
            if sample.is_paired
            else workspace.host_removed_reads(sample)[0]
        )
        return [
            self.removal_command(
                reads=self.inputs(sample, context),
                unmatched=unmatched,
                log=workspace.bowtie2_log(sample),
                context=context,
                paired=sample.is_paired,
                label=sample.name,
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
