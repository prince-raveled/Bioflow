"""Functional profiling with HUMAnN 3."""

from pathlib import Path

from backend.execution.record import ValidationResult
from backend.execution.stage import (
    LogCallback,
    RunContext,
    Stage,
    StageCommand,
    check_exists,
    concatenate_gzip,
    count_data_rows,
)
from backend.samples import Sample


class HumannStage(Stage):
    """Produce gene-family and pathway profiles.

    HUMAnN accepts a single input file, so a paired-end sample's two host-removed
    mate files are concatenated into one combined FASTQ first. This is required by
    the tool, and it is the only place in the workflow where mates are merged.

    The default is HUMAnN's documented protein-only mode
    (--bypass-prescreen --bypass-nucleotide-search), which needs only UniRef50 and
    is feasible without HPC-class memory. Gene families and pathways are still
    produced; per-species gene attribution is not.
    """

    key = "humann"
    title = "Functional profiling (HUMAnN)"
    environment_key = "function"

    required_databases = ("humann_uniref50",)

    def databases_for(self, context: RunContext) -> tuple[str, ...]:
        """Full mode additionally needs the nucleotide pangenome database."""
        if context.options.humann_protein_only:
            return ("humann_uniref50",)
        return ("humann_uniref50", "humann_chocophlan")

    def inputs(self, sample: Sample, context: RunContext) -> list[Path]:
        return context.workspace.host_removed_reads(sample)

    def prepare(self, sample: Sample, context: RunContext, log: LogCallback) -> None:
        if not sample.is_paired:
            return
        combined = context.workspace.humann_input(sample)
        sources = self.inputs(sample, context)
        if combined.exists() and combined.stat().st_size > 0:
            newest_source = max(path.stat().st_mtime for path in sources if path.exists())
            if combined.stat().st_mtime >= newest_source:
                log(f"Reusing the existing combined input: {combined.name}")
                return
        concatenate_gzip(sources, combined, log)

    def commands(self, sample: Sample, context: RunContext) -> list[StageCommand]:
        workspace = context.workspace
        directory = workspace.functional_directory(sample)
        command = [
            "humann",
            "--input",
            str(workspace.humann_input(sample)),
            "--output",
            str(directory),
            "--threads",
            context.threads,
        ]
        if context.options.humann_protein_only:
            command += ["--bypass-prescreen", "--bypass-nucleotide-search"]

        mode = "protein-only" if context.options.humann_protein_only else "full"
        planned = [
            StageCommand(
                description=f"Functional profiling of {sample.name} ({mode} mode)",
                command=command,
                environment_key=self.environment_key,
            )
        ]

        if context.options.humann_normalise:
            produced = workspace.humann_outputs(sample)
            for kind in ("genefamilies", "pathabundance"):
                planned.append(
                    StageCommand(
                        description=f"Normalise {kind} to copies per million",
                        command=[
                            "humann_renorm_table",
                            "--input",
                            str(produced[kind]),
                            "--output",
                            str(workspace.humann_normalised(sample, kind)),
                            "--units",
                            "cpm",
                        ],
                        environment_key=self.environment_key,
                    )
                )
        return planned

    def outputs(self, sample: Sample, context: RunContext) -> list[Path]:
        workspace = context.workspace
        produced = list(workspace.humann_outputs(sample).values())
        if context.options.humann_normalise:
            produced += [
                workspace.humann_normalised(sample, "genefamilies"),
                workspace.humann_normalised(sample, "pathabundance"),
            ]
        return produced

    def validate(self, sample: Sample, context: RunContext) -> ValidationResult:
        workspace = context.workspace
        result = ValidationResult()
        for name, path in workspace.humann_outputs(sample).items():
            if not check_exists(result, path, minimum_bytes=16):
                continue
            rows = count_data_rows(path)
            result.add(f"{name} table has data", rows > 0, f"{rows} row(s)")
        if context.options.humann_normalise:
            for kind in ("genefamilies", "pathabundance"):
                check_exists(result, workspace.humann_normalised(sample, kind), minimum_bytes=16)
        return result
