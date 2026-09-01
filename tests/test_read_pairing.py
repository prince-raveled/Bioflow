"""Tests for the FASTQ pairing and naming rules used by host removal."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from gui.pages.host_removal_page import HostRemovalPage  # noqa: E402


class ReadPairingTests(unittest.TestCase):
    def test_underscore_r1_r2_files_pair(self):
        pairs, unmatched = HostRemovalPage._pair_reads(
            ["/data/healthy_R1.fastq.gz", "/data/healthy_R2.fastq.gz"]
        )
        self.assertEqual(pairs, [("/data/healthy_R1.fastq.gz", "/data/healthy_R2.fastq.gz")])
        self.assertEqual(unmatched, [])

    def test_numeric_suffix_files_pair(self):
        pairs, unmatched = HostRemovalPage._pair_reads(
            ["/data/SRR6303066_1.fastq.gz", "/data/SRR6303066_2.fastq.gz"]
        )
        self.assertEqual(len(pairs), 1)
        self.assertEqual(unmatched, [])

    def test_multiple_samples_pair_independently(self):
        pairs, unmatched = HostRemovalPage._pair_reads(
            [
                "/data/disease_R1.fastq.gz",
                "/data/healthy_R2.fastq.gz",
                "/data/healthy_R1.fastq.gz",
                "/data/disease_R2.fastq.gz",
            ]
        )
        self.assertEqual(len(pairs), 2)
        self.assertEqual(unmatched, [])

    def test_a_lone_mate_is_reported_as_unmatched(self):
        pairs, unmatched = HostRemovalPage._pair_reads(["/data/healthy_R1.fastq.gz"])
        self.assertEqual(pairs, [])
        self.assertEqual(unmatched, ["/data/healthy_R1.fastq.gz"])

    def test_a_file_without_a_mate_marker_is_unmatched(self):
        pairs, unmatched = HostRemovalPage._pair_reads(["/data/sample.fastq.gz"])
        self.assertEqual(pairs, [])
        self.assertEqual(unmatched, ["/data/sample.fastq.gz"])

    def test_output_names_are_safe_for_shell_created_files(self):
        self.assertEqual(
            HostRemovalPage._safe_output_name("healthy sample (run 2)"),
            "healthy_sample_run_2",
        )
        self.assertEqual(HostRemovalPage._safe_output_name("!!!"), "host_removed_sample")

    def test_sample_name_drops_extension_and_mate_marker(self):
        self.assertEqual(
            HostRemovalPage._sample_name("/data/healthy_R1.trim.fastq.gz"), "healthy"
        )


class IndexValidationTests(unittest.TestCase):
    def test_all_six_index_files_are_required(self):
        parts = ("1", "2", "3", "4", "rev.1", "rev.2")
        complete = [Path(f"/db/GRCh38_index.{part}.bt2") for part in parts]
        self.assertEqual(
            HostRemovalPage._find_complete_index(complete), Path("/db/GRCh38_index")
        )
        self.assertIsNone(HostRemovalPage._find_complete_index(complete[:-1]))

    def test_large_index_extension_is_accepted(self):
        parts = ("1", "2", "3", "4", "rev.1", "rev.2")
        complete = [Path(f"/db/GRCh38_index.{part}.bt2l") for part in parts]
        self.assertEqual(
            HostRemovalPage._find_complete_index(complete), Path("/db/GRCh38_index")
        )

    def test_files_from_two_different_indexes_do_not_validate(self):
        mixed = [Path("/db/one.1.bt2"), Path("/db/two.2.bt2"), Path("/db/two.3.bt2")]
        self.assertIsNone(HostRemovalPage._find_complete_index(mixed))


if __name__ == "__main__":
    unittest.main()
