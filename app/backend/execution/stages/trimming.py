"""Adapter and quality trimming with fastp."""

from pathlib import Path
import json

from backend.execution.record import ValidationResult
from backend.execution.stage import (
    RunContext,
    Stage,
    StageCommand,
    check_exists,
    check_gzip_readable,
)
from backend.samples import Sample


class FastpStage(Stage):
    """Trim adapters and low-quality bases.

    Single-end uses -i/-o with one file. Paired-end uses -i/-I and -o/-O so both
    mates are trimmed together and stay synchronised; fastp drops a read pair as
    a unit, which is what keeps the output correctly paired for Bowtie2 later.
    """

    key = "fastp"
    title = "Trimming (fastp)"
    environment_key = "qc"

    def inputs(self, sample: Sample, context: RunContext) -> list[Path]:
        return sample.reads()

    def commands(self, sample: Sample, context: RunContext) -> list[StageCommand]:
        workspace = context.workspace
        trimmed = workspace.trimmed_reads(sample)
        html = workspace.fastp_report(sample, "html")
        report_json = workspace.fastp_report(sample, "json")

        command = [
            "fastp",
            "--thread",
            context.threads,
            "-i",
            str(sample.read1),
            "-o",
            str(trimmed[0]),
        ]
        if sample.is_paired:
            command += ["-I", str(sample.read2), "-O", str(trimmed[1])]
        command += ["--html", str(html), "--json", str(report_json)]

        layout = "paired-end" if sample.is_paired else "single-end"
        return [
            StageCommand(
                description=f"Trim {sample.name} ({layout}) with fastp",
                command=command,
                environment_key=self.environment_key,
            )
        ]

    def outputs(self, sample: Sample, context: RunContext) -> list[Path]:
        workspace = context.workspace
        return [
            *workspace.trimmed_reads(sample),
            workspace.fastp_report(sample, "html"),
            workspace.fastp_report(sample, "json"),
        ]

    def validate(self, sample: Sample, context: RunContext) -> ValidationResult:
        workspace = context.workspace
        result = ValidationResult()

        for path in workspace.trimmed_reads(sample):
            if check_exists(result, path, minimum_bytes=32):
                check_gzip_readable(result, path)

        report_json = workspace.fastp_report(sample, "json")
        if not check_exists(result, report_json, minimum_bytes=32):
            return result

        try:
            report = json.loads(report_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            result.add("fastp report is readable", False, str(error))
            return result
        result.add("fastp report is readable", True)

        after = report.get("summary", {}).get("after_filtering", {})
        surviving = int(after.get("total_reads", 0) or 0)
        # A run that discards every read exits zero but is scientifically useless.
        result.add(
            "reads survived trimming",
            surviving > 0,
            f"{surviving} read(s) after filtering",
        )

        if sample.is_paired:
            expected = report.get("read2_after_filtering") is not None
            result.add(
                "fastp reported both mates",
                expected,
                "" if expected else "no read2 section in the fastp report",
            )
        return result
