"""What a resume is allowed to trust.

Two defects met here. Output verification used to read the first kilobyte of a
compressed file, so a FASTQ truncated anywhere after it validated cleanly and a
resume reused it - an analysis could finish confidently on a fraction of its
reads. Verifying the whole stream fixed that but made a resume decompress every
output again, so what was verified is now recorded and re-checked only when the
files are no longer the ones that were verified.
"""

from pathlib import Path
import gzip
import os
import tempfile
import unittest

import support  # noqa: F401  (puts app/ on the path)

from backend.execution.pipeline import PipelineExecutor  # noqa: E402
from backend.execution.record import PipelineRecord, StageStatus, ValidationResult  # noqa: E402
from backend.execution.stage import (  # noqa: E402
    RunContext,
    Stage,
    StageCommand,
    check_exists,
    check_gzip_readable,
)
from backend.execution.workspace import Workspace  # noqa: E402
from backend.samples import ReadLayout, Sample  # noqa: E402


def write_gzip(path: Path, records: int = 4000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as handle:
        for index in range(records):
            handle.write(f"@read{index}\n{'ACGT' * 30}\n+\n{'I' * 120}\n")


class _GzipStage(Stage):
    """Writes one compressed output and validates it the way real stages do."""

    key = "gz"
    title = "Compressed output"
    environment_key = "qc"

    def target(self, sample, context):
        return context.workspace.trimmed / f"{sample.name}.fastq.gz"

    def inputs(self, sample, context):
        return [sample.read1]

    def commands(self, sample, context):
        return [StageCommand("write", ["writer"], "qc")]

    def outputs(self, sample, context):
        return [self.target(sample, context)]

    def validate(self, sample, context):
        result = ValidationResult()
        path = self.target(sample, context)
        if check_exists(result, path, minimum_bytes=32):
            check_gzip_readable(result, path)
        return result


class _NoopResolver:
    """Resolves every command to /bin/true.

    These tests are about what a resume trusts, not about running tools: the
    output file is written directly by the test, and the stage's command only
    has to succeed so that validation is reached.
    """

    class _Config:
        def environment_name(self, key):
            return "test"

    config = _Config()

    def check(self, *args, **kwargs):
        return []

    def describe(self, components):
        return ""

    def resolve(self, environment_key, command):
        return "/bin/true", []


class TruncationDetectionTests(unittest.TestCase):
    """The check must read far enough to notice a cut in the middle."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-trunc-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.path = self.root / "reads.fastq.gz"
        write_gzip(self.path)
        self.intact = self.path.read_bytes()

    def _verdict(self, payload: bytes) -> bool:
        self.path.write_bytes(payload)
        result = ValidationResult()
        return check_gzip_readable(result, self.path) and result.valid

    def test_an_intact_file_passes(self):
        self.assertTrue(self._verdict(self.intact))

    def test_a_file_cut_in_half_is_rejected(self):
        self.assertFalse(
            self._verdict(self.intact[: len(self.intact) // 2]),
            "half a FASTQ passed as a complete one",
        )

    def test_a_file_missing_only_its_trailer_is_rejected(self):
        self.assertFalse(self._verdict(self.intact[:-40]))

    def test_a_single_corrupted_byte_is_rejected(self):
        middle = len(self.intact) // 2
        damaged = (
            self.intact[:middle] + bytes([self.intact[middle] ^ 0xFF]) + self.intact[middle + 1:]
        )
        self.assertNotEqual(damaged, self.intact)
        self.assertFalse(self._verdict(damaged), "the stored checksum was not checked")

    def test_an_empty_stream_is_rejected(self):
        empty = self.root / "empty.fastq.gz"
        with gzip.open(empty, "wt"):
            pass
        result = ValidationResult()
        check_gzip_readable(result, empty)
        self.assertFalse(result.valid, "a file that decompresses to nothing is not an output")


class ResumeTrustTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-resume-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.workspace = Workspace(self.root / "run")
        self.context = RunContext(workspace=self.workspace)
        reads = self.root / "in.fastq"
        reads.write_text("@r\nACGT\n+\nIIII\n", encoding="utf-8")
        self.sample = Sample("sample", ReadLayout.SINGLE, reads)
        self.stage = _GzipStage()

    def _executor(self):
        return PipelineExecutor(
            self.context, [self.stage], resolver=_NoopResolver(), on_log=lambda _m: None
        )

    def _complete_a_run(self):
        """Produce a validated result and the record that describes it."""
        self.workspace.create()
        write_gzip(self.stage.target(self.sample, self.context))
        return self._executor()._run_stage(self.stage, self.sample, None, False, 1, 1)

    def _previous(self, record):
        previous = PipelineRecord(project_name="p")
        previous.add(record)
        return previous

    def _can_skip(self, record, previous):
        return self._executor()._can_skip(
            self.stage, self.sample, record.fingerprint, previous
        )

    def test_a_completed_stage_records_what_it_validated(self):
        record = self._complete_a_run()
        self.assertIs(record.status, StageStatus.COMPLETED)
        self.assertTrue(
            record.output_fingerprint, "nothing was recorded to compare against later"
        )

    def test_an_unchanged_output_is_reused(self):
        record = self._complete_a_run()
        self.assertTrue(self._can_skip(record, self._previous(record)))

    def test_a_truncated_output_is_never_reused(self):
        record = self._complete_a_run()
        previous = self._previous(record)
        target = self.stage.target(self.sample, self.context)
        intact = target.read_bytes()
        target.write_bytes(intact[: len(intact) // 2])
        self.assertFalse(self._can_skip(record, previous), "a resume reused a truncated output")

    def test_a_same_sized_corruption_is_not_reused(self):
        # The fast path compares size and timestamp; a rewrite moves the
        # timestamp, so the full check still runs and rejects the file.
        record = self._complete_a_run()
        previous = self._previous(record)
        target = self.stage.target(self.sample, self.context)
        intact = target.read_bytes()
        middle = len(intact) // 2
        target.write_bytes(
            intact[:middle] + bytes([intact[middle] ^ 0xFF]) + intact[middle + 1:]
        )
        os.utime(target, (0, 0))
        self.assertFalse(self._can_skip(record, previous))

    def test_a_deleted_output_is_not_reused(self):
        record = self._complete_a_run()
        previous = self._previous(record)
        self.stage.target(self.sample, self.context).unlink()
        self.assertFalse(self._can_skip(record, previous))

    def test_a_checkpoint_from_before_this_was_tracked_is_verified_in_full(self):
        record = self._complete_a_run()
        record.output_fingerprint = ""      # as an older checkpoint would be
        previous = self._previous(record)
        self.assertTrue(self._can_skip(record, previous))

        target = self.stage.target(self.sample, self.context)
        intact = target.read_bytes()
        target.write_bytes(intact[: len(intact) // 2])
        self.assertFalse(
            self._can_skip(record, previous),
            "an untracked checkpoint must fall back to verifying the bytes",
        )

    def test_a_failed_stage_is_never_reused(self):
        record = self._complete_a_run()
        record.status = StageStatus.FAILED
        self.assertFalse(self._can_skip(record, self._previous(record)))
