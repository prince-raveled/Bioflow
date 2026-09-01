"""Taxonomic profiling with MetaPhlAn 4."""

from pathlib import Path

from backend.execution.record import ValidationResult
from backend.execution.stage import (
    RunContext,
    Stage,
    StageCommand,
    check_exists,
    count_data_rows,
)
from backend.samples import Sample


class MetaPhlAnStage(Stage):
    """Profile microbial composition from clade-specific marker genes.

    MetaPhlAn takes its reads as one argument. For paired-end samples the two
    mate files are given as a single comma-separated value, which is MetaPhlAn's
    own convention for paired input - the files are not merged on disk.
    """

    key = "metaphlan"
    title = "Taxonomic profiling (MetaPhlAn)"
    environment_key = "taxonomy"
    required_databases = ("metaphlan_chocophlan",)

    def inputs(self, sample: Sample, context: RunContext) -> list[Path]:
        return context.workspace.host_removed_reads(sample)

    def commands(self, sample: Sample, context: RunContext) -> list[StageCommand]:
        if context.metaphlan_database is None or not context.metaphlan_index:
            raise ValueError("No MetaPhlAn database is configured for taxonomic profiling.")
        workspace = context.workspace
        reads = self.inputs(sample, context)

        layout = "paired-end" if sample.is_paired else "single-end"
        return [
            StageCommand(
                description=f"Profile {sample.name} ({layout}) with MetaPhlAn",
                command=[
                    "metaphlan",
                    ",".join(str(path) for path in reads),
                    "--input_type",
                    "fastq",
                    "--db_dir",
                    str(context.metaphlan_database),
                    "-x",
                    context.metaphlan_index,
                    "--offline",
                    "--nproc",
                    context.threads,
                    "--mapout",
                    str(workspace.taxonomic_map(sample)),
                    "-o",
                    str(workspace.taxonomic_profile(sample)),
                ],
                environment_key=self.environment_key,
            )
        ]

    def outputs(self, sample: Sample, context: RunContext) -> list[Path]:
        workspace = context.workspace
        return [workspace.taxonomic_profile(sample), workspace.taxonomic_map(sample)]

    def validate(self, sample: Sample, context: RunContext) -> ValidationResult:
        workspace = context.workspace
        result = ValidationResult()
        profile = workspace.taxonomic_profile(sample)
        if not check_exists(result, profile, minimum_bytes=16):
            return result

        # MetaPhlAn writes a header-only profile when nothing is classified;
        # that exits zero but carries no taxonomy.
        rows = count_data_rows(profile)
        result.add(
            "profile contains taxonomic assignments",
            rows > 0,
            f"{rows} clade row(s)",
        )
        check_exists(result, workspace.taxonomic_map(sample), minimum_bytes=16)
        return result
