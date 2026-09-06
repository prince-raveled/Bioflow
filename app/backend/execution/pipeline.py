"""The real pipeline executor: ordering, dependencies, failure, and resume.

A run succeeds only when every required stage actually executed (or was validly
skipped) and its outputs passed validation. No stage is ever marked complete
because a loop reached the end of its body.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import threading

from backend.execution.environment import EnvironmentResolver, MissingBackend
from backend.execution.record import (
    PipelineRecord,
    StageRecord,
    StageStatus,
    fingerprint_for,
    now,
)
from backend.execution.runner import CommandCancelled, CommandRunner
from backend.execution.stage import RunContext, Stage
from backend.release import stage_is_available
from backend.execution.stages.functional import HumannStage
from backend.execution.stages.host_removal import HostRemovalStage
from backend.execution.stages.qc import MultiQCStage, fastqc_raw_stage, fastqc_trimmed_stage
from backend.execution.stages.taxonomy import MetaPhlAnStage
from backend.execution.stages.trimming import FastpStage
from backend.samples import ReadLayout, Sample


LogCallback = Callable[[str], None]

#: Stand-in sample for stages that run once for the whole project.
PROJECT_SAMPLE = Sample(name="project", layout=ReadLayout.SINGLE, read1=Path("."))


def all_stages() -> list[Stage]:
    """Every stage in the workflow order documented in the specification.

    Includes stages this release withholds; use `default_stages` for what the
    application actually offers.
    """
    return [
        fastqc_raw_stage(),
        FastpStage(),
        fastqc_trimmed_stage(),
        HostRemovalStage(),
        MetaPhlAnStage(),
        HumannStage(),
        MultiQCStage(),
    ]


def default_stages() -> list[Stage]:
    """The stages this release offers, in workflow order."""
    return [stage for stage in all_stages() if stage_is_available(stage.key)]


def stages_by_key() -> dict[str, Stage]:
    """Every stage by key, including withheld ones, so tests can reach them."""
    return {stage.key: stage for stage in all_stages()}


@dataclass
class PipelineEvent:
    """Progress notification for the interface."""

    stage_key: str
    stage_title: str
    sample_name: str
    status: StageStatus
    index: int = 0
    total: int = 0
    detail: str = ""


@dataclass
class PipelineOutcome:
    """The verdict of a run, with the evidence behind it."""

    record: PipelineRecord
    succeeded: bool
    message: str
    missing_components: list[str] = field(default_factory=list)


class PipelineExecutor:
    """Run the selected stages over the selected samples."""

    def __init__(
        self,
        context: RunContext,
        stages: list[Stage] | None = None,
        resolver: EnvironmentResolver | None = None,
        on_log: LogCallback | None = None,
        on_event: Callable[[PipelineEvent], None] | None = None,
        cancel_event: threading.Event | None = None,
    ):
        self.context = context
        self.stages = stages if stages is not None else default_stages()
        self.resolver = resolver or EnvironmentResolver()
        self.cancel_event = cancel_event or threading.Event()
        self.runner = CommandRunner(self.resolver, self.cancel_event)
        self._on_log = on_log
        self._on_event = on_event

    # ------------------------------------------------------------------
    def log(self, message: str) -> None:
        if message and self._on_log:
            self._on_log(message)

    def emit(self, event: PipelineEvent) -> None:
        if self._on_event:
            self._on_event(event)

    def cancel(self) -> None:
        self.cancel_event.set()
        self.runner.cancel()

    # ------------------------------------------------------------------
    def missing_components(self) -> list[str]:
        """Setup components required by the selected stages but not installed."""
        missing: list[str] = []
        for stage in self.stages:
            for component in self.resolver.check(
                stage.environment_key, stage.databases_for(self.context)
            ):
                if component not in missing:
                    missing.append(component)
        return missing

    def describe_missing(self, components: list[str]) -> str:
        return self.resolver.describe(components)

    # ------------------------------------------------------------------
    def run(self, samples: list[Sample], resume: bool = True) -> PipelineOutcome:
        """Execute the pipeline. Refuses to start if a backend is missing."""
        workspace = self.context.workspace
        try:
            workspace.create()
        except OSError as error:
            # A results folder that cannot be made is an ordinary mistake - a
            # read-only disk, a removed drive, a path typed by hand - and the
            # user can fix it immediately if told. Letting it escape turned it
            # into "the analysis stopped unexpectedly" with an errno.
            message = (
                f"Analysis not started: the results folder {workspace.root} "
                f"could not be created ({error.strerror or error}). Choose a "
                f"different folder."
            )
            self.log(message)
            record = PipelineRecord(project_name=workspace.root.name, status=StageStatus.FAILED)
            record.message = message
            return PipelineOutcome(record, False, message)

        missing = self.missing_components()
        if missing:
            message = (
                f"Analysis not started: {self.describe_missing(missing)} "
                f"{'is' if len(missing) == 1 else 'are'} not installed."
            )
            self.log(message)
            record = PipelineRecord(project_name=workspace.root.name, status=StageStatus.FAILED)
            record.message = message
            return PipelineOutcome(record, False, message, missing)

        if not samples:
            message = "Analysis not started: no samples were selected."
            self.log(message)
            record = PipelineRecord(project_name=workspace.root.name, status=StageStatus.FAILED)
            record.message = message
            return PipelineOutcome(record, False, message)

        previous = PipelineRecord.load(workspace.checkpoint_file) if resume else None
        record = PipelineRecord(project_name=workspace.root.name, status=StageStatus.RUNNING)
        if previous:
            self.log(f"Resuming: found a previous run from {previous.started_at}.")

        per_sample = [stage for stage in self.stages if not stage.per_project]
        per_project = [stage for stage in self.stages if stage.per_project]
        total = len(per_sample) * len(samples) + len(per_project)
        position = 0
        blocked: set[str] = set()

        try:
            for stage in per_sample:
                for sample in samples:
                    position += 1
                    if sample.name in blocked:
                        self._record_blocked(record, stage, sample, position, total)
                        continue
                    stage_record = self._run_stage(stage, sample, previous, resume, position, total)
                    record.add(stage_record)
                    record.save(workspace.checkpoint_file)
                    if stage_record.status is StageStatus.FAILED:
                        blocked.add(sample.name)

            for stage in per_project:
                position += 1
                stage_record = self._run_stage(
                    stage, PROJECT_SAMPLE, previous, resume, position, total
                )
                record.add(stage_record)
                record.save(workspace.checkpoint_file)
        except CommandCancelled:
            record.status = StageStatus.FAILED
            record.message = "Analysis cancelled."
            record.finished_at = now()
            record.save(workspace.checkpoint_file)
            self.log("Analysis cancelled.")
            return PipelineOutcome(record, False, record.message)

        failures = record.failures
        record.finished_at = now()
        if failures:
            record.status = StageStatus.FAILED
            names = ", ".join(f"{item.stage_title} [{item.sample_name}]" for item in failures[:4])
            more = "" if len(failures) <= 4 else f" and {len(failures) - 4} more"
            record.message = f"Analysis failed at: {names}{more}."
        else:
            record.status = StageStatus.COMPLETED
            record.message = (
                f"Analysis completed: {len(self.stages)} stage(s) over {len(samples)} sample(s)."
            )
        record.save(workspace.checkpoint_file)
        self.log(record.message)
        return PipelineOutcome(record, not failures, record.message)

    # ------------------------------------------------------------------
    def _record_blocked(
        self, record: PipelineRecord, stage: Stage, sample: Sample, position: int, total: int
    ) -> None:
        blocked = StageRecord(
            stage_key=stage.key,
            stage_title=stage.title,
            sample_name=sample.name,
            status=StageStatus.BLOCKED,
            message="Skipped because an earlier stage failed for this sample.",
        )
        record.add(blocked)
        self.log(f"[{position}/{total}] {stage.title} — {sample.name}: blocked by an earlier failure.")
        self.emit(
            PipelineEvent(stage.key, stage.title, sample.name, StageStatus.BLOCKED, position, total)
        )

    def _run_stage(
        self,
        stage: Stage,
        sample: Sample,
        previous: PipelineRecord | None,
        resume: bool,
        position: int,
        total: int,
    ) -> StageRecord:
        workspace = self.context.workspace
        stage_record = StageRecord(
            stage_key=stage.key,
            stage_title=stage.title,
            sample_name=sample.name,
            started_at=now(),
        )
        header = f"[{position}/{total}] {stage.title} — {sample.name}"

        try:
            planned = stage.commands(sample, self.context)
        except (ValueError, MissingBackend) as error:
            stage_record.status = StageStatus.FAILED
            stage_record.message = str(error)
            stage_record.finished_at = now()
            self.log(f"{header}: {error}")
            self.emit(
                PipelineEvent(stage.key, stage.title, sample.name, StageStatus.FAILED, position, total, str(error))
            )
            return stage_record

        stage_record.outputs = [str(path) for path in stage.outputs(sample, self.context)]
        stage_record.fingerprint = fingerprint_for(
            [command.command for command in planned], stage.inputs(sample, self.context)
        )

        if resume and self._can_skip(stage, sample, stage_record.fingerprint, previous):
            stage_record.status = StageStatus.SKIPPED
            stage_record.validation = stage.validate(sample, self.context)
            stage_record.message = "Outputs from a previous run are still valid."
            stage_record.finished_at = now()
            self.log(f"{header}: up to date, skipping.")
            self.emit(
                PipelineEvent(stage.key, stage.title, sample.name, StageStatus.SKIPPED, position, total)
            )
            return stage_record

        self.log("")
        self.log(header)
        self.emit(
            PipelineEvent(stage.key, stage.title, sample.name, StageStatus.RUNNING, position, total)
        )
        stage.ensure_directories(sample, self.context)

        try:
            stage.prepare(sample, self.context, self.log)
        except Exception as error:  # noqa: BLE001 - recorded against this stage
            # Broader than OSError on purpose. Preparation is arbitrary
            # in-process work, and letting anything else escape would abandon
            # the whole run rather than failing this one stage and carrying on
            # with the remaining samples, which is how every other failure here
            # behaves.
            stage_record.status = StageStatus.FAILED
            stage_record.message = f"Preparation failed: {error}"
            stage_record.finished_at = now()
            self.log(f"{header}: FAILED — {stage_record.message}")
            # Without this the stage sits at "Running" in the interface for the
            # rest of the session: the flow widget is driven entirely by events.
            self.emit(
                PipelineEvent(
                    stage.key, stage.title, sample.name, StageStatus.FAILED,
                    position, total, stage_record.message,
                )
            )
            return stage_record

        for index, stage_command in enumerate(planned, start=1):
            suffix = "" if len(planned) == 1 else f".{index}"
            command_record = self.runner.run(
                stage_command,
                stdout_path=workspace.stage_log(stage.key, sample.name, f"out{suffix}"),
                stderr_path=workspace.stage_log(stage.key, sample.name, f"err{suffix}"),
                log=self.log,
            )
            stage_record.commands.append(command_record)
            stage_record.log_path = command_record.stderr_path
            if not command_record.succeeded:
                stage_record.status = StageStatus.FAILED
                stage_record.message = (
                    f"{stage_command.description} failed"
                    + (f" (exit code {command_record.exit_code})" if command_record.exit_code is not None else "")
                )
                stage_record.finished_at = now()
                self.log(f"{header}: FAILED — {stage_record.message}")
                self.emit(
                    PipelineEvent(
                        stage.key, stage.title, sample.name, StageStatus.FAILED, position, total,
                        stage_record.message,
                    )
                )
                return stage_record

        # Exit code zero is necessary but not sufficient: the outputs must hold up.
        stage_record.validation = stage.validate(sample, self.context)
        stage_record.finished_at = now()
        if stage_record.validation.valid:
            stage_record.status = StageStatus.COMPLETED
            # Record what was validated, so a later resume can tell that these
            # are the same bytes rather than verifying them all over again.
            stage_record.output_fingerprint = fingerprint_for(
                [], stage.outputs(sample, self.context)
            )
            stage_record.message = stage_record.validation.summary()
            self.log(f"{header}: completed — {stage_record.message}")
            self.emit(
                PipelineEvent(stage.key, stage.title, sample.name, StageStatus.COMPLETED, position, total)
            )
        else:
            stage_record.status = StageStatus.FAILED
            stage_record.message = (
                f"The command finished but its output failed validation: "
                f"{stage_record.validation.summary()}"
            )
            self.log(f"{header}: FAILED — {stage_record.message}")
            self.emit(
                PipelineEvent(
                    stage.key, stage.title, sample.name, StageStatus.FAILED, position, total,
                    stage_record.message,
                )
            )
        return stage_record

    def _can_skip(
        self, stage: Stage, sample: Sample, fingerprint: str, previous: PipelineRecord | None
    ) -> bool:
        """Reuse a previous result only when it is still provably good.

        Three things must hold: the earlier run succeeded, its inputs and
        commands are unchanged, and the outputs validate right now. Existence
        alone is never enough.
        """
        if previous is None:
            return False
        earlier = previous.find(stage.key, sample.name)
        if earlier is None or not earlier.status.is_success:
            return False
        if not earlier.fingerprint or earlier.fingerprint != fingerprint:
            return False
        # Verifying an output means decompressing it in full, so re-reading
        # every output of a finished project just to decide nothing has changed
        # can take minutes with nothing on screen. Skip that only when the
        # outputs are byte-identical to the ones already verified; anything
        # else - a missing file, a different size, a newer timestamp, or a
        # record from before this was tracked - falls through to the full check.
        if earlier.output_fingerprint:
            current = fingerprint_for([], stage.outputs(sample, self.context))
            if current == earlier.output_fingerprint:
                return True
        return stage.validate(sample, self.context).valid
