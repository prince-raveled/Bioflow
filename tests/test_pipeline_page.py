"""The workflow page: input handling, layout override, and readiness gating."""

from pathlib import Path
import hashlib
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
class PipelinePageTests(unittest.TestCase):
    application = None

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        # The page loads configuration on construction, so it must be pointed at
        # a private data directory *before* it is built. Without this it falls
        # back to the developer's real BioFlow directory.
        self.data_root = isolate_bioflow_data_directory(self)

        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-page-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

        from gui.pages.pipeline_page import PipelinePage

        self.page = PipelinePage()
        self.addCleanup(self.page.shutdown)
        self.page.output_directory = self.root / "results"

    def _select(self, *paths: Path):
        self.page.selected_files = list(paths)
        self.page._rebuild_project()

    def test_paired_files_are_detected_and_shown_as_one_sample(self):
        read1 = write_fastq(self.root / "in" / "demo_R1.fastq.gz", mate="1")
        read2 = write_fastq(self.root / "in" / "demo_R2.fastq.gz", mate="2")
        self._select(read1, read2)
        self.assertEqual(len(self.page.project.samples), 1)
        self.assertTrue(self.page.project.samples[0].is_paired)
        self.assertEqual(self.page.sample_table.topLevelItemCount(), 1)
        self.assertEqual(self.page.sample_table.topLevelItem(0).text(1), "Paired-end")

    def test_single_files_are_detected_as_separate_samples(self):
        one = write_fastq(self.root / "in" / "a.fastq.gz")
        two = write_fastq(self.root / "in" / "b.fastq.gz")
        self._select(one, two)
        self.assertEqual(len(self.page.project.samples), 2)
        self.assertEqual(self.page.sample_table.topLevelItemCount(), 2)

    def test_the_user_can_override_detection_to_single_end(self):
        read1 = write_fastq(self.root / "in" / "demo_R1.fastq.gz", mate="1")
        read2 = write_fastq(self.root / "in" / "demo_R2.fastq.gz", mate="2")
        self._select(read1, read2)
        self.assertTrue(self.page.project.samples[0].is_paired)

        # Index 1 is "Single-end" in LAYOUT_CHOICES.
        self.page.layout_choice.setCurrentIndex(1)
        self.assertEqual(len(self.page.project.samples), 2)
        self.assertTrue(all(not s.is_paired for s in self.page.project.samples))
        self.assertEqual({s.name for s in self.page.project.samples}, {"demo_R1", "demo_R2"})

    def test_forcing_paired_on_an_unpaired_file_is_refused_with_a_reason(self):
        lone = write_fastq(self.root / "in" / "lonely_R1.fastq.gz", mate="1")
        self._select(lone)
        self.page.layout_choice.setCurrentIndex(2)  # Paired-end
        self.assertEqual(self.page.project.samples, [])
        self.assertFalse(self.page.run_button.isEnabled())

    def test_a_corrupt_input_blocks_the_run_and_explains_why(self):
        broken = self.root / "in" / "broken.fastq.gz"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_bytes(b"\x1f\x8b\x08\x00 rubbish")
        self._select(broken)
        self.assertFalse(self.page.run_button.isEnabled())
        self.assertIn("Input problems", self.page.status_label.text())

    def test_missing_backends_block_the_run_and_say_where_to_fix_it(self):
        reads = write_fastq(self.root / "in" / "demo.fastq.gz")
        self._select(reads)
        # Every stage is selected by default, including ones needing databases.
        self.assertFalse(self.page.run_button.isEnabled())
        message = self.page.status_label.text()
        # The wording is free to change; pointing at the remedy is not. Naming
        # the components alone left a first run looking broken rather than
        # unconfigured.
        self.assertIn("Setup & Resources", message)
        self.assertIn("install", message.lower())

    def test_deselecting_every_stage_is_refused(self):
        reads = write_fastq(self.root / "in" / "demo.fastq.gz")
        self._select(reads)
        for box in self.page.stage_boxes.values():
            box.setChecked(False)
        self.assertFalse(self.page.run_button.isEnabled())
        self.assertIn("at least one stage", self.page.status_label.text())

    def test_the_page_is_not_running_before_a_run_starts(self):
        self.assertFalse(self.page.is_running)


if __name__ == "__main__":
    unittest.main()


def real_data_root() -> Path:
    """The data directory BioFlow would use with no test isolation in force.

    Derived from the application's own resolver rather than a written-out path,
    so this works on any machine and hard-codes nothing.
    """
    saved = os.environ.pop("BIOFLOW_DATA_DIR", None)
    try:
        from backend.config import default_data_root

        return default_data_root()
    finally:
        if saved is not None:
            os.environ["BIOFLOW_DATA_DIR"] = saved


def digest(path: Path) -> str | None:
    """A content hash, or None when the file does not exist."""
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class PipelinePageIsolationTests(unittest.TestCase):
    """Guards that these tests never touch a developer's real BioFlow state."""

    application = None

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        # Captured before isolation so the comparison targets the real file.
        self.real_root = real_data_root()
        self.real_config = self.real_root / "config.json"
        self.config_before = digest(self.real_config)

        self.data_root = isolate_bioflow_data_directory(self)
        from gui.pages.pipeline_page import PipelinePage

        self.page = PipelinePage()
        self.addCleanup(self.page.shutdown)

    def test_the_page_uses_the_isolated_data_directory(self):
        self.assertEqual(self.page.config.data_root, self.data_root)

    def test_the_page_does_not_use_the_real_data_directory(self):
        self.assertNotEqual(self.page.config.data_root, self.real_root)
        self.assertFalse(
            str(self.page.config.data_root).startswith(str(self.real_root)),
            "the page's data directory must not live inside the real one",
        )

    def test_real_configuration_values_do_not_leak_in(self):
        # A developer may have an external GRCh38 reference configured; an
        # isolated run must not see it.
        self.assertIsNone(self.page.config.external_grch38_index)
        self.assertEqual(
            self.page.config.database_root, self.data_root / "databases"
        )

    def test_constructing_the_page_leaves_the_real_config_file_untouched(self):
        # Exercise the paths that read and write configuration.
        self.page.selected_files = []
        self.page._rebuild_project()
        self.page.refresh_readiness()
        self.assertEqual(
            digest(self.real_config),
            self.config_before,
            "constructing the workflow page must not modify the real config.json",
        )

    def test_isolation_writes_only_inside_the_temporary_directory(self):
        # First use writes config.json; it must land in the temporary root.
        written = self.data_root / "config.json"
        self.assertTrue(written.is_file(), "isolated config.json should be created here")
        self.assertEqual(digest(self.real_config), self.config_before)

    def test_environment_variable_points_at_the_temporary_directory(self):
        self.assertEqual(os.environ["BIOFLOW_DATA_DIR"], str(self.data_root))


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class StageSelectionDefaultsTests(unittest.TestCase):
    """A stage the machine cannot run must not block the stages it can.

    Requirements are checked across the whole selection, so one ticked stage
    with a missing backend disables the run button for every other stage. With
    functional profiling left out of this release, ticking it by default meant
    a fully installed taxonomic pipeline refused to start.
    """

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        from backend.config import get_config
        import support

        isolate_bioflow_data_directory(self)
        # Functional profiling is the stage this release withholds, and the one
        # whose absent environment caused the blocking behaviour under test, so
        # it is enabled here to keep exercising that path.
        support.enable_functional_profiling(self)
        self.config = get_config()

    def _page(self):
        from gui.pages.pipeline_page import PipelinePage

        return PipelinePage()

    def test_a_stage_without_its_environment_starts_unticked(self):
        # Nothing is installed in an isolated data directory, but the fallback
        # only applies when *no* stage is installed, so install one.
        import support

        support.install_fake_environment(self.config, "qc")
        page = self._page()
        self.assertTrue(page.stage_boxes["fastqc_raw"].isChecked())
        self.assertFalse(
            page.stage_boxes["humann"].isChecked(),
            "a stage whose environment is absent would block the whole run",
        )

    def test_an_unticked_stage_explains_itself(self):
        import support

        support.install_fake_environment(self.config, "qc")
        page = self._page()
        tooltip = page.stage_boxes["humann"].toolTip()
        self.assertIn("not installed", tooltip)
        self.assertIn("Setup", tooltip)

    def test_a_bare_machine_still_shows_the_whole_workflow(self):
        # With nothing installed there is no useful subset to offer; ticking
        # everything keeps the intended pipeline visible and lets the readiness
        # line send the user to Setup.
        page = self._page()
        self.assertTrue(all(box.isChecked() for box in page.stage_boxes.values()))

    def test_installing_a_stage_later_ticks_it(self):
        import support

        support.install_fake_environment(self.config, "qc")
        self.assertFalse(self._page().stage_boxes["humann"].isChecked())
        support.install_fake_environment(self.config, "function")
        self.assertTrue(
            self._page().stage_boxes["humann"].isChecked(),
            "the default must follow what is installed, not a hard-coded release list",
        )


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class MissingBackendMessageTests(unittest.TestCase):
    """What the page says when a run cannot start yet.

    Enumerating every absent component made a first run look broken rather than
    unconfigured: on a bare machine the list is the whole catalogue, rendered as
    one unbroken line under a greyed-out button.
    """

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        from backend.config import get_config

        isolate_bioflow_data_directory(self)
        self.config = get_config()

    def _page_with_input(self):
        from gui.pages.pipeline_page import PipelinePage

        page = PipelinePage()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        reads = write_fastq(root / "sample.fastq.gz", records=20)
        page.selected_files = [reads]
        page.output_directory = root / "out"
        page._rebuild_project()
        return page

    def test_a_bare_machine_is_given_one_instruction(self):
        page = self._page_with_input()
        message = page.status_label.text()
        self.assertIn("Setup & Resources", message)
        self.assertNotIn(",", message, "a catalogue of components reads as a fault report")
        self.assertFalse(page.run_button.isEnabled())

    def test_a_partly_installed_machine_names_what_is_missing(self):
        import support

        support.install_fake_micromamba(self.config)
        page = self._page_with_input()
        message = page.status_label.text()
        self.assertIn("Install", message)
        self.assertIn("Setup & Resources", message)

    def test_the_list_is_capped_rather_than_unbounded(self):
        import support

        support.install_fake_micromamba(self.config)
        page = self._page_with_input()
        # Two named, the rest counted: the point is what to do, not an inventory.
        self.assertLessEqual(page.status_label.text().count(","), 2)


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class ExcludedInputTests(unittest.TestCase):
    """Files that were selected but did not become samples.

    Two files sharing a basename would overwrite each other's outputs, so one is
    excluded. That is logged, but the readiness line said only "Ready", which
    reads as though everything selected is about to be analysed.
    """

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        from backend.config import BOWTIE2_INDEX_PARTS, reload_config
        from backend.setup.registry import database_specs
        import support

        isolate_bioflow_data_directory(self)
        self.config = reload_config()
        support.install_fake_micromamba(self.config)
        for key in ("qc", "hostrem", "taxonomy"):
            support.install_fake_environment(self.config, key)
        prefix = self.config.managed_grch38_index_prefix
        prefix.parent.mkdir(parents=True, exist_ok=True)
        for part in BOWTIE2_INDEX_PARTS:
            Path(f"{prefix}.{part}.bt2").write_bytes(b"x" * 64)
        spec = database_specs()["metaphlan_chocophlan"]
        directory = self.config.database_directory(*spec.directory_parts)
        directory.mkdir(parents=True, exist_ok=True)
        for pattern in spec.required_globs:
            (directory / pattern).write_bytes(b"x" * 64)

        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-excluded-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def _page_with(self, files):
        from gui.pages.pipeline_page import PipelinePage

        page = PipelinePage()
        page.selected_files = files
        page.output_directory = self.root / "out"
        page._rebuild_project()
        return page

    def test_an_ordinary_selection_says_only_that_it_is_ready(self):
        files = [write_fastq(self.root / "c" / f"s{i}.fastq.gz", records=20) for i in (1, 2)]
        page = self._page_with(files)
        self.assertEqual(len(page.project.samples), 2)
        self.assertTrue(page.run_button.isEnabled())
        self.assertNotIn("excluded", page.status_label.text())

    def test_an_excluded_file_is_named_in_the_readiness_line(self):
        files = [
            write_fastq(self.root / "d1" / "a.fastq.gz", records=20),
            write_fastq(self.root / "d2" / "a.fastq.gz", records=20),
        ]
        page = self._page_with(files)
        self.assertEqual(len(page.project.samples), 1, "the duplicate name was not rejected")
        message = page.status_label.text()
        self.assertIn("Ready", message)
        self.assertIn("excluded", message)
        self.assertTrue(
            page.run_button.isEnabled(),
            "one unusable file among many must not block the whole run",
        )
