"""The fastp page's Read layout control and its file-count validation.

The control states what fastp is about to do; it must not alter how the fastp
command is built, which stays driven by the selected files.
"""

from pathlib import Path
import os
import tempfile
import unittest

from support import isolate_bioflow_data_directory, write_fastq  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class FastpLayoutTests(unittest.TestCase):
    application = None

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        isolate_bioflow_data_directory(self)
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-fastp-layout-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        from gui.pages.fastp_page import FastPPage

        self.page = FastPPage()
        self.page.output_directory = self.root / "out"

    def test_the_control_offers_both_modes(self):
        options = [
            self.page.layout_choice.itemText(i)
            for i in range(self.page.layout_choice.count())
        ]
        self.assertEqual(options, ["Single-end", "Paired-end"])

    def test_selecting_two_files_switches_to_paired_end(self):
        from backend.samples import ReadLayout

        self.page.fastq_files = ["a_R1.fastq.gz", "b_R2.fastq.gz"]
        self.page.layout_choice.setCurrentIndex(1)
        self.assertIs(self.page.read_layout, ReadLayout.PAIRED)

    def test_single_end_rejects_two_files(self):
        from backend.samples import ReadLayout

        self.page.layout_choice.setCurrentIndex(0)
        self.assertIs(self.page.read_layout, ReadLayout.SINGLE)
        self.page.fastq_files = ["one.fastq.gz", "two.fastq.gz"]
        self.page.run_analysis()
        self.assertIn("exactly 1 FASTQ file", self.page.log.toPlainText())
        self.assertIsNone(self.page.process, "no process may start on a mismatch")

    def test_paired_end_rejects_one_file(self):
        self.page.layout_choice.setCurrentIndex(1)
        self.page.fastq_files = ["only.fastq.gz"]
        self.page.run_analysis()
        self.assertIn("exactly 2 FASTQ files", self.page.log.toPlainText())
        self.assertIsNone(self.page.process)

    def test_the_hint_states_the_expected_file_count(self):
        self.page.layout_choice.setCurrentIndex(0)
        self.assertIn("1 FASTQ file", self.page.layout_hint.text())
        self.page.layout_choice.setCurrentIndex(1)
        self.assertIn("2 FASTQ files", self.page.layout_hint.text())

    def test_command_construction_is_unchanged_for_single_end(self):
        reads = write_fastq(self.root / "in" / "sample.fastq.gz")
        self.page.fastq_files = [str(reads)]
        self.page.layout_choice.setCurrentIndex(0)
        captured = {}
        self.page.start_tool = lambda command, **kw: captured.setdefault("cmd", command)
        self.page.run_analysis()
        command = captured["cmd"]
        self.assertEqual(command[0], "fastp")
        self.assertIn("-i", command)
        self.assertNotIn("-I", command, "single-end must not pass a second mate")
        self.assertNotIn("-O", command)

    def test_command_construction_is_unchanged_for_paired_end(self):
        read1 = write_fastq(self.root / "in" / "s_R1.fastq.gz", mate="1")
        read2 = write_fastq(self.root / "in" / "s_R2.fastq.gz", mate="2")
        self.page.fastq_files = [str(read1), str(read2)]
        self.page.layout_choice.setCurrentIndex(1)
        captured = {}
        self.page.start_tool = lambda command, **kw: captured.setdefault("cmd", command)
        self.page.run_analysis()
        command = captured["cmd"]
        self.assertEqual(command[command.index("-i") + 1], str(read1))
        self.assertEqual(command[command.index("-I") + 1], str(read2))
        self.assertIn("-O", command)


if __name__ == "__main__":
    unittest.main()
