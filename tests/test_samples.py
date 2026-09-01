"""Input handling: layout detection, pairing rules, and FASTQ validation.

All tests here are pure logic against small generated files. No real tools run.
"""

from pathlib import Path
import gzip
import tempfile
import unittest

from support import write_fastq  # noqa: E402
from backend.samples import (  # noqa: E402
    ReadLayout,
    Sample,
    detect_layout,
    detect_samples,
    read_identifier,
    sample_name_for,
    split_mate_marker,
    validate_fastq_file,
    validate_sample,
)


class LayoutDetectionTests(unittest.TestCase):
    def test_r1_r2_names_are_detected_as_paired(self):
        files = [Path("a_R1.fastq.gz"), Path("a_R2.fastq.gz")]
        self.assertIs(detect_layout(files), ReadLayout.PAIRED)

    def test_a_lone_file_is_detected_as_single(self):
        self.assertIs(detect_layout([Path("sample.fastq.gz")]), ReadLayout.SINGLE)

    def test_a_lone_r1_without_its_mate_is_not_paired(self):
        self.assertIs(detect_layout([Path("a_R1.fastq.gz")]), ReadLayout.SINGLE)

    def test_numeric_mate_markers_are_recognised(self):
        files = [Path("SRR1_1.fastq.gz"), Path("SRR1_2.fastq.gz")]
        self.assertIs(detect_layout(files), ReadLayout.PAIRED)

    def test_mate_marker_splitting(self):
        self.assertEqual(split_mate_marker("sample_R1"), ("sample", "1"))
        self.assertEqual(split_mate_marker("sample.2"), ("sample", "2"))
        self.assertEqual(split_mate_marker("sample"), ("sample", None))

    def test_sample_name_ignores_extension_and_mate(self):
        self.assertEqual(sample_name_for(Path("/x/demo_R1.fastq.gz")), "demo")
        self.assertEqual(sample_name_for(Path("/x/demo.fq")), "demo")


class SampleGroupingTests(unittest.TestCase):
    def test_valid_paired_end_selection(self):
        result = detect_samples([Path("a_R1.fastq.gz"), Path("a_R2.fastq.gz")])
        self.assertTrue(result.ok)
        sample = result.samples[0]
        self.assertTrue(sample.is_paired)
        self.assertEqual(sample.read1.name, "a_R1.fastq.gz")
        self.assertEqual(sample.read2.name, "a_R2.fastq.gz")

    def test_valid_single_end_selection(self):
        result = detect_samples([Path("a.fastq.gz"), Path("b.fastq.gz")])
        self.assertTrue(result.ok)
        self.assertEqual(len(result.samples), 2)
        self.assertTrue(all(not s.is_paired for s in result.samples))

    def test_missing_r2_is_reported_not_guessed(self):
        result = detect_samples([Path("a_R1.fastq.gz")], layout=ReadLayout.PAIRED)
        self.assertFalse(result.samples)
        self.assertIn("R2 is missing", result.problems[0])

    def test_mismatched_r1_and_r2_do_not_pair(self):
        result = detect_samples(
            [Path("alpha_R1.fastq.gz"), Path("beta_R2.fastq.gz")], layout=ReadLayout.PAIRED
        )
        self.assertFalse(result.samples)
        self.assertEqual(len(result.problems), 2)

    def test_unsupported_file_is_rejected(self):
        result = detect_samples([Path("notes.txt")])
        self.assertFalse(result.samples)
        self.assertIn("not a FASTQ file", result.problems[0])

    def test_user_can_force_single_end_on_r1_r2_names(self):
        # The override must win over detection, giving two independent samples.
        result = detect_samples(
            [Path("a_R1.fastq.gz"), Path("a_R2.fastq.gz")], layout=ReadLayout.SINGLE
        )
        self.assertEqual(len(result.samples), 2)
        self.assertTrue(all(not s.is_paired for s in result.samples))

    def test_duplicate_sample_names_are_rejected(self):
        result = detect_samples(
            [Path("/one/a.fastq.gz"), Path("/two/a.fastq.gz")], layout=ReadLayout.SINGLE
        )
        self.assertEqual(len(result.samples), 1)
        self.assertIn("already used by", result.problems[0])

    def test_a_paired_sample_cannot_be_built_without_r2(self):
        with self.assertRaises(ValueError):
            Sample("x", ReadLayout.PAIRED, Path("a_R1.fastq.gz"))

    def test_a_single_sample_cannot_carry_r2(self):
        with self.assertRaises(ValueError):
            Sample("x", ReadLayout.SINGLE, Path("a.fastq.gz"), Path("b.fastq.gz"))


class FastqValidationTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-fastq-")
        self.directory = Path(self._temporary.name)

    def tearDown(self):
        self._temporary.cleanup()

    def test_a_valid_plain_fastq_passes(self):
        path = write_fastq(self.directory / "good.fastq")
        self.assertTrue(validate_fastq_file(path).valid)

    def test_a_valid_gzipped_fastq_passes(self):
        path = write_fastq(self.directory / "good.fastq.gz")
        self.assertTrue(validate_fastq_file(path).valid)

    def test_a_missing_file_is_reported(self):
        outcome = validate_fastq_file(self.directory / "absent.fastq")
        self.assertFalse(outcome.valid)
        self.assertIn("does not exist", outcome.reason)

    def test_an_empty_file_is_reported(self):
        path = self.directory / "empty.fastq"
        path.write_text("", encoding="utf-8")
        self.assertIn("empty", validate_fastq_file(path).reason)

    def test_an_unsupported_extension_is_reported(self):
        path = self.directory / "reads.txt"
        path.write_text("@r\nACGT\n+\nIIII\n", encoding="utf-8")
        self.assertIn("unsupported extension", validate_fastq_file(path).reason)

    def test_a_file_named_gz_that_is_not_gzip_is_reported(self):
        path = self.directory / "fake.fastq.gz"
        path.write_bytes(b"@read\nACGT\n+\nIIII\n")
        self.assertIn("not gzip-compressed", validate_fastq_file(path).reason)

    def test_a_corrupt_gzip_stream_is_reported_not_raised(self):
        path = self.directory / "broken.fastq.gz"
        path.write_bytes(b"\x1f\x8b\x08\x00 followed by rubbish that is not deflate data")
        outcome = validate_fastq_file(path)
        self.assertFalse(outcome.valid)
        self.assertIn("unreadable", outcome.reason)

    def test_a_truncated_record_is_reported(self):
        path = self.directory / "short.fastq"
        path.write_text("@read0\nACGTACGTAC\n+\n", encoding="utf-8")
        self.assertIn("truncated", validate_fastq_file(path).reason)

    def test_mismatched_sequence_and_quality_lengths_are_reported(self):
        path = self.directory / "ragged.fastq"
        path.write_text("@read0\nACGTACGTAC\n+\nIIII\n", encoding="utf-8")
        self.assertIn("quality", validate_fastq_file(path).reason)

    def test_a_header_without_at_is_reported(self):
        path = self.directory / "noat.fastq"
        path.write_text("read0\nACGT\n+\nIIII\n", encoding="utf-8")
        self.assertIn("'@'", validate_fastq_file(path).reason)

    def test_read_identifier_strips_mate_suffixes(self):
        self.assertEqual(read_identifier("@SRR1.1/1"), "SRR1.1")
        self.assertEqual(read_identifier("@SRR1 extra"), "SRR1")

    def test_paired_files_from_different_samples_are_caught(self):
        read1 = write_fastq(self.directory / "x_R1.fastq.gz", mate="1")
        read2 = self.directory / "x_R2.fastq.gz"
        with gzip.open(read2, "wt") as handle:
            handle.write("@somethingelse/2\nACGTACGTAC\n+\nIIIIIIIIII\n")
        sample = Sample("x", ReadLayout.PAIRED, read1, read2)
        problems = validate_sample(sample)
        self.assertTrue(problems)
        self.assertIn("do not look like mates", problems[0])

    def test_genuine_mates_pass_the_pairing_check(self):
        read1 = write_fastq(self.directory / "y_R1.fastq.gz", mate="1")
        read2 = write_fastq(self.directory / "y_R2.fastq.gz", mate="2")
        sample = Sample("y", ReadLayout.PAIRED, read1, read2)
        self.assertEqual(validate_sample(sample), [])


if __name__ == "__main__":
    unittest.main()
