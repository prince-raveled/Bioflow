"""Executor behaviour: ordering, failure propagation, validation, and resume.

These use stand-in executables installed into a temporary BioFlow environment,
so the real resolver, runner, and executor code paths all run. No genuine
bioinformatics tool is invoked.
"""

from pathlib import Path
import tempfile
import unittest

from support import (  # noqa: E402
    clear_bioflow_environment,
    failing_tool,
    install_fake_micromamba,
    install_fake_tool,
    make_config,
)
from backend.execution.environment import EnvironmentResolver, MissingBackend  # noqa: E402
from backend.execution.pipeline import PipelineExecutor  # noqa: E402
from backend.execution.record import StageStatus, ValidationResult  # noqa: E402
from backend.execution.stage import RunContext, Stage, StageCommand, check_exists  # noqa: E402
from backend.execution.workspace import Workspace  # noqa: E402
from backend.samples import ReadLayout, Sample  # noqa: E402


class WritingStage(Stage):
    """A stand-in stage that runs one tool which writes a marker file."""

    environment_key = "qc"

    def __init__(self, key: str, tool: str = "writer"):
        self.key = key
        self.title = f"Stage {key}"
        self._tool = tool

    def target(self, sample, context) -> Path:
        return context.workspace.root / f"{self.key}_{sample.name}.out"

    def inputs(self, sample, context):
        return [sample.read1]

    def commands(self, sample, context):
        return [
            StageCommand(
                description=f"{self.key} for {sample.name}",
                command=[self._tool, str(self.target(sample, context))],
                environment_key=self.environment_key,
            )
        ]

    def outputs(self, sample, context):
        return [self.target(sample, context)]

    def validate(self, sample, context) -> ValidationResult:
        result = ValidationResult()
        check_exists(result, self.target(sample, context))
        return result


WRITER = """#!/usr/bin/env bash
set -euo pipefail
mkdir -p "$(dirname "$1")"
printf 'written by the stand-in tool\\n' > "$1"
echo "wrote $1"
"""

LIAR = """#!/usr/bin/env bash
# Exits zero without producing the file it was asked for.
echo "pretending to work"
exit 0
"""


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        clear_bioflow_environment()
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-pipeline-")
        self.root = Path(self._temporary.name)
        self.config = make_config(self.root / "backend")
        install_fake_micromamba(self.config)
        install_fake_tool(self.config, "qc", "writer", WRITER)

        self.workspace = Workspace(self.root / "project")
        self.context = RunContext(workspace=self.workspace)
        self.resolver = EnvironmentResolver(self.config)

        self.reads = self.root / "sample.fastq"
        self.reads.write_text("@r\nACGT\n+\nIIII\n", encoding="utf-8")
        self.sample = Sample("sample", ReadLayout.SINGLE, self.reads)

    def tearDown(self):
        self._temporary.cleanup()

    def executor(self, stages):
        return PipelineExecutor(self.context, stages, self.resolver, on_log=lambda _m: None)


class SuccessfulRunTests(PipelineTestCase):
    def test_every_stage_runs_in_order_and_succeeds(self):
        stages = [WritingStage("one"), WritingStage("two"), WritingStage("three")]
        outcome = self.executor(stages).run([self.sample])
        self.assertTrue(outcome.succeeded)
        self.assertEqual(
            [record.stage_key for record in outcome.record.stages], ["one", "two", "three"]
        )
        for record in outcome.record.stages:
            self.assertIs(record.status, StageStatus.COMPLETED)

    def test_every_command_is_recorded_in_full(self):
        outcome = self.executor([WritingStage("one")]).run([self.sample])
        command = outcome.record.stages[0].commands[0]
        self.assertEqual(command.exit_code, 0)
        self.assertTrue(command.started_at and command.finished_at)
        self.assertTrue(Path(command.stdout_path).is_file())
        self.assertTrue(Path(command.stderr_path).is_file())
        self.assertIn("bioflow-qc", command.environment)

    def test_the_checkpoint_file_is_written(self):
        self.executor([WritingStage("one")]).run([self.sample])
        self.assertTrue(self.workspace.checkpoint_file.is_file())

    def test_several_samples_each_get_their_own_records(self):
        second_reads = self.root / "second.fastq"
        second_reads.write_text("@r\nACGT\n+\nIIII\n", encoding="utf-8")
        second = Sample("second", ReadLayout.SINGLE, second_reads)
        outcome = self.executor([WritingStage("one")]).run([self.sample, second])
        self.assertTrue(outcome.succeeded)
        self.assertEqual({r.sample_name for r in outcome.record.stages}, {"sample", "second"})


class FailurePropagationTests(PipelineTestCase):
    def test_a_failing_command_fails_the_pipeline(self):
        install_fake_tool(self.config, "qc", "broken", failing_tool("boom", 3))
        stages = [WritingStage("one"), WritingStage("two", tool="broken")]
        outcome = self.executor(stages).run([self.sample])
        self.assertFalse(outcome.succeeded)
        failed = outcome.record.find("two", "sample")
        self.assertIs(failed.status, StageStatus.FAILED)
        self.assertEqual(failed.commands[0].exit_code, 3)
        self.assertIn("boom", failed.commands[0].stderr_tail)

    def test_later_stages_are_blocked_for_that_sample(self):
        install_fake_tool(self.config, "qc", "broken", failing_tool())
        stages = [WritingStage("one", tool="broken"), WritingStage("two"), WritingStage("three")]
        outcome = self.executor(stages).run([self.sample])
        self.assertFalse(outcome.succeeded)
        self.assertIs(outcome.record.find("two", "sample").status, StageStatus.BLOCKED)
        self.assertIs(outcome.record.find("three", "sample").status, StageStatus.BLOCKED)

    def test_one_sample_failing_does_not_stop_another(self):
        second_reads = self.root / "second.fastq"
        second_reads.write_text("@r\nACGT\n+\nIIII\n", encoding="utf-8")
        second = Sample("second", ReadLayout.SINGLE, second_reads)

        class SelectiveStage(WritingStage):
            def commands(self, sample, context):
                tool = "broken" if sample.name == "sample" else "writer"
                return [
                    StageCommand(
                        description=f"{self.key} for {sample.name}",
                        command=[tool, str(self.target(sample, context))],
                        environment_key="qc",
                    )
                ]

        install_fake_tool(self.config, "qc", "broken", failing_tool())
        outcome = self.executor([SelectiveStage("one"), WritingStage("two")]).run(
            [self.sample, second]
        )
        self.assertFalse(outcome.succeeded)
        self.assertIs(outcome.record.find("one", "second").status, StageStatus.COMPLETED)
        self.assertIs(outcome.record.find("two", "second").status, StageStatus.COMPLETED)
        self.assertIs(outcome.record.find("two", "sample").status, StageStatus.BLOCKED)

    def test_exit_zero_is_not_enough_when_the_output_is_missing(self):
        # The defining case: the tool claims success but produced nothing.
        install_fake_tool(self.config, "qc", "liar", LIAR)
        outcome = self.executor([WritingStage("one", tool="liar")]).run([self.sample])
        self.assertFalse(outcome.succeeded)
        record = outcome.record.find("one", "sample")
        self.assertIs(record.status, StageStatus.FAILED)
        self.assertEqual(record.commands[0].exit_code, 0)
        self.assertIn("failed validation", record.message)


class DependencyGateTests(PipelineTestCase):
    def test_a_missing_environment_stops_the_run_before_any_work(self):
        class OtherEnvironmentStage(WritingStage):
            environment_key = "taxonomy"

        executor = self.executor([OtherEnvironmentStage("tax")])
        self.assertIn("env:taxonomy", executor.missing_components())
        outcome = executor.run([self.sample])
        self.assertFalse(outcome.succeeded)
        self.assertIn("not installed", outcome.message)
        self.assertFalse(outcome.record.stages, "no stage should have been attempted")

    def test_a_missing_database_stops_the_run(self):
        class DatabaseStage(WritingStage):
            required_databases = ("grch38",)

        executor = self.executor([DatabaseStage("needs_db")])
        self.assertIn("db:grch38", executor.missing_components())
        self.assertFalse(executor.run([self.sample]).succeeded)

    def test_no_samples_is_refused_rather_than_reported_as_success(self):
        outcome = self.executor([WritingStage("one")]).run([])
        self.assertFalse(outcome.succeeded)
        self.assertIn("no samples", outcome.message)

    def test_the_resolver_never_falls_back_to_a_system_installation(self):
        resolver = EnvironmentResolver(self.config)
        with self.assertRaises(MissingBackend) as caught:
            resolver.resolve("taxonomy", ["metaphlan", "--version"])
        self.assertIn("Setup & Resources", str(caught.exception))

    def test_a_tool_absent_from_an_installed_environment_is_reported(self):
        with self.assertRaises(MissingBackend) as caught:
            EnvironmentResolver(self.config).resolve("qc", ["nonexistent-tool"])
        self.assertIn("not present", str(caught.exception))


class ResumeTests(PipelineTestCase):
    def test_valid_outputs_are_reused_on_a_second_run(self):
        stages = [WritingStage("one"), WritingStage("two")]
        self.assertTrue(self.executor(stages).run([self.sample]).succeeded)
        outcome = self.executor(stages).run([self.sample])
        self.assertTrue(outcome.succeeded)
        for record in outcome.record.stages:
            self.assertIs(record.status, StageStatus.SKIPPED)

    def test_a_deleted_output_makes_only_that_stage_rerun(self):
        stages = [WritingStage("one"), WritingStage("two")]
        self.executor(stages).run([self.sample])
        stages[1].target(self.sample, self.context).unlink()
        outcome = self.executor(stages).run([self.sample])
        self.assertIs(outcome.record.find("one", "sample").status, StageStatus.SKIPPED)
        self.assertIs(outcome.record.find("two", "sample").status, StageStatus.COMPLETED)

    def test_a_changed_input_invalidates_the_checkpoint(self):
        # Existence alone would wrongly skip here; the fingerprint must catch it.
        stages = [WritingStage("one")]
        self.executor(stages).run([self.sample])
        self.reads.write_text("@r\nACGTACGT\n+\nIIIIIIII\n", encoding="utf-8")
        outcome = self.executor(stages).run([self.sample])
        self.assertIs(outcome.record.find("one", "sample").status, StageStatus.COMPLETED)

    def test_resume_can_be_switched_off(self):
        stages = [WritingStage("one")]
        self.executor(stages).run([self.sample])
        outcome = self.executor(stages).run([self.sample], resume=False)
        self.assertIs(outcome.record.find("one", "sample").status, StageStatus.COMPLETED)

    def test_a_previously_failed_stage_is_retried(self):
        install_fake_tool(self.config, "qc", "broken", failing_tool())
        stages = [WritingStage("one", tool="broken")]
        self.assertFalse(self.executor(stages).run([self.sample]).succeeded)
        # Repair the tool; the retry must actually run rather than stay failed.
        install_fake_tool(self.config, "qc", "broken", WRITER)
        outcome = self.executor(stages).run([self.sample])
        self.assertTrue(outcome.succeeded)
        self.assertIs(outcome.record.find("one", "sample").status, StageStatus.COMPLETED)

    def test_a_corrupt_checkpoint_file_causes_a_full_rerun(self):
        stages = [WritingStage("one")]
        self.executor(stages).run([self.sample])
        self.workspace.checkpoint_file.write_text("{not json", encoding="utf-8")
        outcome = self.executor(stages).run([self.sample])
        self.assertTrue(outcome.succeeded)
        self.assertIs(outcome.record.find("one", "sample").status, StageStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
