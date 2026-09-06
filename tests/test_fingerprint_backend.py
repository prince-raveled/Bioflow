"""A result must remember which execution backend produced it.

The checkpoint fingerprint hashes the stage's own command, not the resolved one,
which is the right choice: the resolved form carries an installation's absolute
paths and would invalidate a checkpoint merely because BioFlow moved. But it
means the command alone cannot tell two backends apart. FastQC, fastp, MultiQC
and host removal pass no argument that differs between running in a Micromamba
environment and running in a container, so their fingerprints matched across
both. Switching backend and pressing Run marked those four up to date from the
other backend's results and re-ran only MetaPhlAn - leaving a record that
claimed one execution environment for a result mostly produced by the other.

Native contributes nothing to the digest on purpose. It is the baseline, and an
empty identity keeps every checkpoint written before backends existed valid,
which matters when the stage being spared is a ninety-minute profiling run.
"""

from pathlib import Path
import gzip
import tempfile
import unittest

import support  # noqa: F401  (puts app/ on the path)

from backend.execution.pipeline import PipelineExecutor  # noqa: E402
from backend.execution.record import (  # noqa: E402
    PipelineRecord,
    StageStatus,
    ValidationResult,
    fingerprint_for,
)
from backend.execution.stage import (  # noqa: E402
    RunContext,
    Stage,
    StageCommand,
    check_exists,
)
from backend.execution.workspace import Workspace  # noqa: E402
from backend.samples import ReadLayout, Sample  # noqa: E402


CONTAINER = "container:bioflow-tools@sha256:aaaa"
OTHER_IMAGE = "container:bioflow-tools@sha256:bbbb"


class _NoopResolver:
    class _Config:
        def environment_name(self, key):
            return "test"

    config = _Config()

    def resolve(self, environment_key, command):
        return "/bin/true", []


class _PlainStage(Stage):
    """A stage whose command names nothing backend-specific.

    This is the important shape. MetaPhlAn happens to pass a shim path that
    differs between backends, so it would have been re-run anyway; the four
    stages that do not are the ones that were silently reused.
    """

    key = "plain"
    title = "Plain stage"
    environment_key = "qc"

    def target(self, sample, context):
        return context.workspace.trimmed / f"{sample.name}.fastq.gz"

    def inputs(self, sample, context):
        return [sample.read1]

    def commands(self, sample, context):
        return [StageCommand("run", ["fastqc", "--outdir", "out", str(sample.read1)], "qc")]

    def outputs(self, sample, context):
        return [self.target(sample, context)]

    def validate(self, sample, context):
        result = ValidationResult()
        check_exists(result, self.target(sample, context))
        return result


class ExecutionIdentityTests(unittest.TestCase):
    def test_native_contributes_nothing(self):
        self.assertEqual(RunContext(workspace=Workspace(Path("/x"))).execution_identity, "")

    def test_a_container_names_its_image(self):
        context = RunContext(
            workspace=Workspace(Path("/x")),
            execution_backend="container",
            execution_image="bioflow-tools@sha256:aaaa",
        )
        self.assertEqual(context.execution_identity, CONTAINER)


class FingerprintTests(unittest.TestCase):
    COMMANDS = [["fastqc", "--outdir", "/out", "/in/a.fastq.gz"]]

    def test_an_empty_identity_leaves_the_digest_unchanged(self):
        # Checkpoints written before backends existed must stay valid.
        self.assertEqual(
            fingerprint_for(self.COMMANDS, []),
            fingerprint_for(self.COMMANDS, [], ""),
        )

    def test_a_backend_changes_the_digest(self):
        self.assertNotEqual(
            fingerprint_for(self.COMMANDS, []),
            fingerprint_for(self.COMMANDS, [], CONTAINER),
        )

    def test_two_images_are_not_interchangeable(self):
        # A rebuilt image is a different set of binaries even at the same tag.
        self.assertNotEqual(
            fingerprint_for(self.COMMANDS, [], CONTAINER),
            fingerprint_for(self.COMMANDS, [], OTHER_IMAGE),
        )

    def test_the_identity_cannot_be_confused_with_a_command(self):
        # The separator must not let an identity and a command argument
        # collide into the same digest.
        self.assertNotEqual(
            fingerprint_for([["a"]], [], "b"),
            fingerprint_for([["b"]], [], "a"),
        )

    def test_the_same_backend_still_matches_itself(self):
        self.assertEqual(
            fingerprint_for(self.COMMANDS, [], CONTAINER),
            fingerprint_for(self.COMMANDS, [], CONTAINER),
        )


class ResumeAcrossBackendsTests(unittest.TestCase):
    """The defect, at the level a user would meet it."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-backend-")
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        reads = self.root / "in.fastq"
        reads.write_text("@r\nACGT\n+\nIIII\n", encoding="utf-8")
        self.sample = Sample("sample", ReadLayout.SINGLE, reads)
        self.stage = _PlainStage()

    def _context(self, backend="native", image=""):
        return RunContext(
            workspace=Workspace(self.root / "run"),
            execution_backend=backend,
            execution_image=image,
            memory_limit_bytes=16 * 1024 ** 3,
        )

    def _executor(self, context):
        return PipelineExecutor(
            context, [self.stage], resolver=_NoopResolver(), on_log=lambda _m: None
        )

    def _completed_run(self, context):
        """A finished, validated stage and the record describing it."""
        context.workspace.create()
        target = self.stage.target(self.sample, context)
        target.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(target, "wt") as handle:
            handle.write("@r\nACGT\n+\nIIII\n")
        record = self._executor(context)._run_stage(
            self.stage, self.sample, None, False, 1, 1
        )
        self.assertIs(record.status, StageStatus.COMPLETED)
        previous = PipelineRecord(project_name="p")
        previous.add(record)
        return record, previous

    def _can_skip(self, context, previous):
        executor = self._executor(context)
        fingerprint = fingerprint_for(
            [c.command for c in self.stage.commands(self.sample, context)],
            self.stage.inputs(self.sample, context),
            context.execution_identity,
        )
        return executor._can_skip(self.stage, self.sample, fingerprint, previous)

    def test_the_same_backend_resumes(self):
        native = self._context()
        _record, previous = self._completed_run(native)
        self.assertTrue(
            self._can_skip(native, previous),
            "a result from the same backend should still be reused",
        )

    def test_a_container_run_does_not_reuse_a_native_result(self):
        native = self._context()
        _record, previous = self._completed_run(native)
        self.assertFalse(
            self._can_skip(self._context("container", "bioflow-tools@sha256:aaaa"), previous),
            "a container run reused a result produced natively",
        )

    def test_a_native_run_does_not_reuse_a_container_result(self):
        container = self._context("container", "bioflow-tools@sha256:aaaa")
        _record, previous = self._completed_run(container)
        self.assertFalse(
            self._can_skip(self._context(), previous),
            "a native run reused a result produced in a container",
        )

    def test_a_rebuilt_image_does_not_reuse_the_old_one(self):
        first = self._context("container", "bioflow-tools@sha256:aaaa")
        _record, previous = self._completed_run(first)
        self.assertFalse(
            self._can_skip(self._context("container", "bioflow-tools@sha256:bbbb"), previous),
            "a different image reused a result from another image",
        )

    def test_the_recorded_fingerprint_carries_the_backend(self):
        container = self._context("container", "bioflow-tools@sha256:aaaa")
        record, _previous = self._completed_run(container)
        native_equivalent = fingerprint_for(
            [c.command for c in self.stage.commands(self.sample, container)],
            self.stage.inputs(self.sample, container),
        )
        self.assertNotEqual(record.fingerprint, native_equivalent)


if __name__ == "__main__":
    unittest.main()
