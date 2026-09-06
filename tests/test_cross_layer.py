"""Contradictions between individually-correct components.

Each test here covers a case where two layers answered the same question
differently. A defect at the boundary between two correct modules is still a
defect, and it is the kind a per-module test suite does not catch.
"""

from pathlib import Path
import json
import os
import tempfile
import unittest

import support  # noqa: F401  (puts app/ on the path)


class EnvironmentReadinessAgreementTests(unittest.TestCase):
    """Setup and the pipeline must agree on whether an environment is usable.

    An interrupted environment creation leaves a `bin` directory with no tools
    in it. The Setup page called that "incomplete" while the pipeline's resolver
    called it installed, so the run button enabled itself for an environment
    that could not run anything, and the analysis failed part-way through.
    """

    def setUp(self):
        from backend.config import reload_config

        self.data_root = support.isolate_bioflow_data_directory(self)
        self.config = reload_config()
        support.install_fake_micromamba(self.config)

    def _interrupted_environment(self, key: str) -> None:
        (self.config.environment_prefix(key) / "bin").mkdir(parents=True, exist_ok=True)

    def test_an_interrupted_environment_is_not_runnable(self):
        from backend.execution.environment import EnvironmentResolver
        from backend.setup.manager import SetupManager

        self._interrupted_environment("qc")
        manager = SetupManager(self.config)
        resolver = EnvironmentResolver(self.config)

        self.assertFalse(manager.environment_installed("qc"))
        self.assertIn(
            "env:qc",
            resolver.check("qc"),
            "the resolver must not accept an environment Setup calls incomplete",
        )

    def test_a_complete_environment_is_runnable(self):
        from backend.execution.environment import EnvironmentResolver
        from backend.setup.manager import SetupManager

        support.install_fake_environment(self.config, "qc")
        self.assertTrue(SetupManager(self.config).environment_installed("qc"))
        self.assertEqual(EnvironmentResolver(self.config).check("qc"), [])

    def test_both_layers_agree_for_every_environment(self):
        from backend.execution.environment import EnvironmentResolver
        from backend.setup.manager import SetupManager
        from backend.setup.registry import environment_specs

        manager = SetupManager(self.config)
        resolver = EnvironmentResolver(self.config)
        for key in environment_specs():
            with self.subTest(environment=key, state="interrupted"):
                self._interrupted_environment(key)
                # An environment is runnable exactly when the manager considers
                # it installed - the two layers may not disagree.
                self.assertEqual(
                    manager.environment_installed(key),
                    f"env:{key}" not in resolver.check(key),
                )


class DatabaseReadinessAgreementTests(unittest.TestCase):
    """Install planning and run-time gating ask different questions correctly.

    Planning asks "is BioFlow's own copy complete?"; run time asks "can an
    analysis use something right now?". They are allowed to differ only in the
    external-reference case, never on completeness.
    """

    def setUp(self):
        from backend.config import reload_config

        self.data_root = support.isolate_bioflow_data_directory(self)
        self.config = reload_config()

    def test_a_partially_unpacked_database_is_never_usable(self):
        from backend.setup.manager import SetupManager
        from backend.setup.registry import database_specs

        manager = SetupManager(self.config)
        spec = database_specs()["metaphlan_chocophlan"]
        directory = manager.database_directory(spec)
        directory.mkdir(parents=True, exist_ok=True)

        # The marker file alone: what an install interrupted during unpacking
        # leaves behind.
        (directory / f"{spec.required_globs[0]}").write_bytes(b"x" * 64)
        self.assertFalse(manager.managed_database_present(spec))
        self.assertFalse(
            manager.database_installed(spec),
            "run time must not accept a database installation cannot complete",
        )

    def test_a_partial_database_is_still_planned_for_installation(self):
        # Otherwise a half-finished multi-gigabyte download could never be
        # repaired from the interface.
        from backend.setup.manager import SetupManager
        from backend.setup.registry import database_specs

        manager = SetupManager(self.config)
        spec = database_specs()["metaphlan_chocophlan"]
        directory = manager.database_directory(spec)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{spec.required_globs[0]}").write_bytes(b"x" * 64)

        plan = manager.build_plan(["db:metaphlan_chocophlan"])
        self.assertFalse(plan.is_empty())
        self.assertIn(spec.title, plan.components)


class InterfaceMatchesTheReleaseTests(unittest.TestCase):
    """Nothing on screen may name a stage this release does not offer.

    The header strip listed its steps as a hard-coded tuple, so it went on
    advertising "Function" after functional profiling was withheld - contradicting
    the pipeline row directly beneath it on the same screen. Anything that
    describes the workflow is derived from the stage list now, and this test
    fails if a new hard-coded copy appears.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from PyQt6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("PyQt6 is not installed")
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        support.isolate_bioflow_data_directory(self)

    def _window(self):
        from gui.main_window import MainWindow

        window = MainWindow()
        self.addCleanup(window.close)
        return window

    @staticmethod
    def _workflow_strip(window):
        """The steps the header advertises.

        Read from the widget's own data rather than by scraping labels: the
        strip is painted, and a test that depends on how it is drawn would
        break on a purely visual change while missing the thing it exists to
        catch - the header naming a stage the release does not offer.
        """
        from gui.widgets.workflow_ribbon import WorkflowRibbon

        ribbons = window.findChildren(WorkflowRibbon)
        return list(ribbons[0].steps) if ribbons else []

    def test_the_header_does_not_advertise_a_withheld_stage(self):
        strip = self._workflow_strip(self._window())
        self.assertIn("QC", strip)
        self.assertIn("Report", strip)
        self.assertNotIn(
            "Function",
            strip,
            "the header names a stage the release does not offer",
        )

    def test_the_header_follows_the_stage_list(self):
        import os

        os.environ["BIOFLOW_ENABLE_FUNCTIONAL"] = "1"
        self.addCleanup(os.environ.pop, "BIOFLOW_ENABLE_FUNCTIONAL", None)
        self.assertIn(
            "Function",
            self._workflow_strip(self._window()),
            "enabling the stage did not bring its step back",
        )

    def test_every_offered_stage_has_a_label_for_the_strip(self):
        from backend.execution.pipeline import all_stages

        for stage in all_stages():
            with self.subTest(stage=stage.key):
                self.assertTrue(
                    stage.short_title,
                    "a stage with no short label would vanish from the header",
                )

    def test_an_option_belonging_to_a_withheld_stage_is_hidden(self):
        page = self._window().page_by_name["Run workflow"]
        self.assertFalse(
            page.protein_only.isVisible(),
            "the HUMAnN option is offered for a stage that cannot run",
        )
        self.assertNotIn("humann", page.stage_boxes)


class NavigationMatchesPagesTests(unittest.TestCase):
    """Sidebar entries and pages were two hard-coded lists of the same names.

    The window looks a page up by the text of the clicked item, guarded by a
    plain `if`. A name spelled differently in the two places therefore produced
    an entry that did nothing at all - no error, no log, no page.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from PyQt6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("PyQt6 is not installed")
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        support.isolate_bioflow_data_directory(self)

    def test_every_offered_page_exists(self):
        from gui.main_window import MainWindow
        from gui.navigation import page_names

        window = MainWindow()
        self.addCleanup(window.close)
        self.assertEqual(page_names(), list(window.page_by_name))

    def test_every_sidebar_leaf_opens_a_page(self):
        from PyQt6.QtCore import Qt
        from gui.main_window import MainWindow

        window = MainWindow()
        self.addCleanup(window.close)
        leaves = []
        tree = window.sidebar
        for index in range(tree.topLevelItemCount()):
            section = tree.topLevelItem(index)
            for child in range(section.childCount()):
                leaves.append(section.child(child).text(0))
        self.assertTrue(leaves)
        for name in leaves:
            with self.subTest(page=name):
                self.assertIn(
                    name, window.page_by_name, "this sidebar entry would do nothing"
                )

    def test_a_mismatch_is_refused_rather_than_silently_ignored(self):
        from gui.navigation import check_pages_match

        with self.assertRaises(RuntimeError):
            check_pages_match(["Setup & Resources"])

    def test_the_headings_are_not_pages(self):
        from gui.main_window import MainWindow
        from gui.navigation import NAVIGATION

        window = MainWindow()
        self.addCleanup(window.close)
        for heading, _pages in NAVIGATION:
            with self.subTest(heading=heading):
                self.assertNotIn(heading, window.page_by_name)


class ChipLabelsComeFromTheStagesTests(unittest.TestCase):
    """The pipeline row held its own table of stage labels.

    A second copy of "which stages exist", keyed by stage key, living in a
    widget - the same shape as the header defect, and free to drift the same way.
    """

    def test_every_stage_supplies_its_own_chip_label(self):
        from backend.execution.pipeline import all_stages

        for stage in all_stages():
            with self.subTest(stage=stage.key):
                self.assertTrue(stage.chip_title, "the widget would have to guess")

    def test_the_widget_keeps_no_table_of_stage_names(self):
        from gui.widgets.stage_flow import StageFlow

        self.assertFalse(
            hasattr(StageFlow, "SHORT_NAMES"),
            "a stage-keyed table in the interface is a second source of truth",
        )

    def test_the_row_shows_exactly_the_offered_stages(self):
        from backend.execution.pipeline import default_stages
        from gui.widgets.stage_flow import StageFlow

        labels = [StageFlow._short_title(stage) for stage in default_stages()]
        self.assertNotIn("HUMAnN", labels, "a withheld stage appears in the pipeline row")
        self.assertEqual(len(labels), len(default_stages()))


class EveryBackendStateIsPresentableTests(unittest.TestCase):
    """A state the backend can produce must have something to show for it.

    Both presentation tables enumerate an enum by hand, so a state added later
    would fall through to a default and appear as something it is not.
    """

    def test_every_stage_status_has_a_presentation(self):
        from backend.execution.record import StageStatus
        from gui.widgets.stage_flow import STATE_PRESENTATION

        for status in StageStatus:
            with self.subTest(status=status.value):
                self.assertIn(status, STATE_PRESENTATION)

    def test_every_resource_state_has_a_badge_and_a_label(self):
        from backend.config import ResourceState
        from gui.pages.setup_page import STATE_APPEARANCE

        for state in ResourceState:
            with self.subTest(state=state.value):
                self.assertIn(state.value, STATE_APPEARANCE)
                self.assertTrue(state.label)

    def test_no_presentation_describes_a_state_that_cannot_happen(self):
        from backend.config import ResourceState
        from backend.execution.record import StageStatus
        from gui.pages.setup_page import STATE_APPEARANCE
        from gui.widgets.stage_flow import STATE_PRESENTATION

        self.assertEqual([k for k in STATE_PRESENTATION if k not in list(StageStatus)], [])
        self.assertEqual(
            [k for k in STATE_APPEARANCE if k not in {s.value for s in ResourceState}], []
        )


class StageKeysResolveAgainstTheRegistryTests(unittest.TestCase):
    """A stage names its environment and databases as plain strings.

    An unknown database key is silently ignored by the readiness check - it
    reports nothing missing - so a mistyped key would let a stage run without
    the data it declared it needed, with no message anywhere. This test is what
    makes that impossible, so the check itself can stay simple.
    """

    def test_every_stage_names_an_environment_that_exists(self):
        from backend.execution.pipeline import all_stages
        from backend.setup.registry import environment_specs

        environments = environment_specs()
        for stage in all_stages():
            with self.subTest(stage=stage.key):
                self.assertIn(stage.environment_key, environments)

    def test_every_stage_names_databases_that_exist(self):
        from backend.execution.pipeline import all_stages
        from backend.setup.registry import database_specs

        databases = database_specs()
        for stage in all_stages():
            for key in stage.databases_for.__self__.required_databases if False else stage.required_databases:
                with self.subTest(stage=stage.key, database=key):
                    self.assertIn(key, databases)

    def test_every_database_names_an_environment_that_exists(self):
        from backend.setup.registry import database_specs, environment_specs

        environments = environment_specs()
        for key, spec in database_specs().items():
            with self.subTest(database=key):
                self.assertIn(spec.environment_key, environments)


class ThreadDefaultsFollowConfigurationTests(unittest.TestCase):
    """default_threads was honoured on one page and ignored on three others.

    Each tool page carried its own literal, and its slider's label was a second
    literal beside it, so the two could disagree until the slider was moved.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from PyQt6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("PyQt6 is not installed")
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        import json

        from backend.config import reload_config

        root = support.isolate_bioflow_data_directory(self)
        (root / "config.json").write_text(
            json.dumps({"format_version": 1, "default_threads": 7}), encoding="utf-8"
        )
        self.config = reload_config()

    def _pages(self):
        from gui.pages.fastp_page import FastPPage
        from gui.pages.fastqc_page import FastQCPage
        from gui.pages.host_removal_page import HostRemovalPage
        from gui.pages.pipeline_page import PipelinePage

        return {
            "FastQC": FastQCPage(),
            "fastp": FastPPage(),
            "Host Removal": HostRemovalPage(),
            "Run workflow": PipelinePage(),
        }

    def test_every_page_starts_at_the_configured_thread_count(self):
        self.assertEqual(self.config.default_threads, 7)
        for name, page in self._pages().items():
            with self.subTest(page=name):
                self.assertEqual(page.threads.value(), 7)

    def test_every_slider_label_matches_its_slider(self):
        for name, page in self._pages().items():
            with self.subTest(page=name):
                self.assertEqual(
                    page.thread_count.text(), f"{page.threads.value():02d}"
                )


class BatchHostRemovalUsesTheStageRuleTests(unittest.TestCase):
    """Exit zero is necessary but not sufficient - on both paths.

    The batch page counted a sample as succeeded on its exit status alone, while
    the pipeline stage running the identical command required the alignment
    summary and a readable output. Bowtie2 can exit zero having written nothing.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from PyQt6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("PyQt6 is not installed")
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        import gzip
        import tempfile

        support.isolate_bioflow_data_directory(self)
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-batch-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.log = self.root / "s_bowtie2.log"
        self.reads = self.root / "s_nohost.fastq.gz"
        self._gzip = gzip

    def _page(self):
        from gui.pages.host_removal_page import HostRemovalPage

        page = HostRemovalPage()
        page._current_job = ("s", self.log, [self.reads])
        return page

    def _write_reads(self, truncated=False):
        with self._gzip.open(self.reads, "wt") as handle:
            for index in range(300):
                handle.write(f"@r{index}\n{'ACGT' * 30}\n+\n{'I' * 120}\n")
        if truncated:
            raw = self.reads.read_bytes()
            self.reads.write_bytes(raw[: len(raw) // 2])

    def test_a_good_run_is_accepted(self):
        self.log.write_text("12.34% overall alignment rate\n", encoding="utf-8")
        self._write_reads()
        self.assertEqual(self._page()._output_problems(), "")

    def test_exit_zero_with_no_output_is_rejected(self):
        self.log.write_text("12.34% overall alignment rate\n", encoding="utf-8")
        self.assertIn("missing", self._page()._output_problems())

    def test_a_log_without_an_alignment_rate_is_rejected(self):
        self.log.write_text("nothing useful here\n", encoding="utf-8")
        self._write_reads()
        self.assertIn("alignment rate", self._page()._output_problems())

    def test_a_truncated_output_is_rejected(self):
        self.log.write_text("12.34% overall alignment rate\n", encoding="utf-8")
        self._write_reads(truncated=True)
        self.assertTrue(self._page()._output_problems())


class ReadLayoutIsCarriedAsStateTests(unittest.TestCase):
    """The layout must not be recovered by comparing display text.

    Host removal decided paired-vs-single with `currentText() == "Paired-end"`.
    Changing that label would have turned every run single-end silently.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from PyQt6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("PyQt6 is not installed")
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        support.isolate_bioflow_data_directory(self)

    def test_each_choice_carries_its_enum(self):
        from backend.samples import ReadLayout
        from gui.pages.host_removal_page import HostRemovalPage

        page = HostRemovalPage()
        found = []
        for index in range(page.layout_choice.count()):
            page.layout_choice.setCurrentIndex(index)
            self.assertIsInstance(page.chosen_layout(), ReadLayout)
            self.assertEqual(page.layout_choice.currentText(), page.chosen_layout().label)
            found.append(page.chosen_layout())
        self.assertEqual(set(found), set(ReadLayout))

    def test_no_page_compares_layout_by_its_label(self):
        pages = (Path(__file__).resolve().parent.parent / "app" / "gui" / "pages")
        offenders = [
            path.name
            for path in pages.glob("*.py")
            if 'currentText() == "Paired-end"' in path.read_text(encoding="utf-8")
            or 'currentText() == "Single-end"' in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [], "layout recovered from display text")


class OneDefinitionOfAFastqNameTests(unittest.TestCase):
    def test_nothing_repeats_the_extension_list(self):
        from backend.samples import FASTQ_EXTENSIONS

        literal = '(".fastq.gz", ".fq.gz", ".fastq", ".fq")'
        app = Path(__file__).resolve().parent.parent / "app"
        offenders = [
            str(path.relative_to(app))
            for path in app.rglob("*.py")
            if path.name != "samples.py" and literal in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [], "a second list of FASTQ extensions")
        self.assertEqual(FASTQ_EXTENSIONS, (".fastq.gz", ".fq.gz", ".fastq", ".fq"))


class PageSectionsMatchTheSidebarTests(unittest.TestCase):
    """A page's own heading must name the section the sidebar filed it under.

    Host Removal announced itself as "Quality control" because the shared base
    class wrote that heading for every page built on it. Clicking one section
    and arriving at a page claiming another is a small contradiction, but it is
    the same kind as the header advertising a stage the release does not ship.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from PyQt6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("PyQt6 is not installed")
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        support.isolate_bioflow_data_directory(self)

    def test_every_page_names_its_own_section(self):
        from gui.main_window import MainWindow
        from gui.navigation import NAVIGATION

        window = MainWindow()
        self.addCleanup(window.close)
        sections = {name: section for section, pages in NAVIGATION for name in pages}
        for name, page in window.page_by_name.items():
            eyebrow = getattr(page, "eyebrow", None)
            if eyebrow is None:
                continue
            with self.subTest(page=name):
                self.assertEqual(eyebrow.text(), sections[name])

    def test_taxonomic_profiling_is_offered_on_its_own(self):
        from gui.navigation import page_names

        self.assertIn(
            "MetaPhlAn",
            page_names(),
            "profiling should be runnable on its own, like the other tools",
        )


class ResultsFolderHoldsOnlyRealWorkTests(unittest.TestCase):
    """A results folder should describe what happened, not what might have.

    Every run created nine directories regardless. Two were always empty: a
    "reports" directory nothing in the codebase has ever written to, and
    "06_functional" for a stage this release withholds. A user opening their
    results found folders for work that could not have occurred.
    """

    def setUp(self):
        import tempfile

        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-dirs-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def _names(self):
        from backend.execution.workspace import Workspace

        return [d.name for d in Workspace(self.root / "run").all_directories()]

    def test_no_directory_is_made_for_a_withheld_stage(self):
        self.assertNotIn("06_functional", self._names())

    def test_enabling_the_stage_brings_its_directory_back(self):
        import support

        support.enable_functional_profiling(self)
        names = self._names()
        self.assertIn("06_functional", names)
        # And in workflow order, between taxonomy and the report.
        self.assertLess(names.index("05_taxonomy"), names.index("06_functional"))
        self.assertLess(names.index("06_functional"), names.index("07_multiqc"))

    def test_the_dead_reports_directory_is_gone(self):
        self.assertNotIn("reports", self._names())

    def test_nothing_references_a_reports_directory_any_more(self):
        app = Path(__file__).resolve().parent.parent / "app"
        offenders = [
            str(path.relative_to(app))
            for path in app.rglob("*.py")
            if "workspace.reports" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])

    def test_every_directory_created_is_one_a_stage_writes_into(self):
        from backend.execution.pipeline import default_stages

        names = set(self._names())
        # logs is written by the runner rather than by a stage.
        names.discard("logs")
        used = set()
        for stage in default_stages():
            used.add(stage.__class__.__name__)
        self.assertTrue(names, "a run must still create somewhere to write")
        self.assertNotIn("reports", names)


class StandalonePageFolderTests(unittest.TestCase):
    """Profiling one sample should not lay out a whole pipeline.

    The page created the full nine-directory workspace and used one of them,
    leaving seven empty numbered folders in whatever directory the user chose.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from PyQt6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("PyQt6 is not installed")
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        import gzip
        import tempfile

        support.isolate_bioflow_data_directory(self)
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-page-dirs-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.reads = self.root / "a_nohost.fastq.gz"
        with gzip.open(self.reads, "wt") as handle:
            handle.write("@r\nACGT\n+\nIIII\n")

    def test_only_the_directory_it_writes_into_is_created(self):
        from unittest import mock

        from gui.pages.metaphlan_page import MetaPhlAnPage

        page = MetaPhlAnPage()
        self.addCleanup(page.shutdown)
        output = self.root / "results"
        page.fastq_files = [str(self.reads)]
        page.output_directory = output

        with mock.patch.object(page, "start_tool"), \
             mock.patch("gui.pages.metaphlan_page.SetupManager") as manager:
            manager.return_value.database_installed.return_value = True
            page.run_analysis()

        made = sorted(p.name for p in output.rglob("*") if p.is_dir())
        self.assertEqual(made, ["05_taxonomy"])
