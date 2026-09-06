"""Construction tests for the Setup page, including its install-state branches."""

from pathlib import Path
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import support  # noqa: E402  (shared test helpers)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - desktop dependency is optional for CI
    QApplication = None


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class SetupPageTests(unittest.TestCase):
    application = None

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        for variable in (
            "BIOFLOW_MICROMAMBA",
            "BIOFLOW_MAMBA_ROOT_PREFIX",
            "BIOFLOW_GRCH38_INDEX",
        ):
            os.environ.pop(variable, None)
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-page-test-")
        os.environ["BIOFLOW_DATA_DIR"] = self._temporary.name
        self.root = Path(self._temporary.name)

    def tearDown(self):
        os.environ.pop("BIOFLOW_DATA_DIR", None)
        self._temporary.cleanup()

    def _build_page(self):
        from backend.config import reload_config
        from gui.pages.setup_page import SetupPage

        reload_config()
        return SetupPage()

    def _install_fake_micromamba(self):
        binary = self.root / "bin" / "micromamba"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o755)

    def test_page_builds_on_a_fresh_machine(self):
        from backend.setup.manager import SetupManager

        page = self._build_page()
        # Derived rather than pinned: the page shows what the release offers, so
        # a hard-coded count only records what the release happened to be.
        self.assertEqual(len(page.rows), len(SetupManager(page.config).components()))
        self.assertNotIn("env:function", page.rows, "withheld from this release")
        self.assertEqual(page.rows["micromamba"].status_label.text(), "Not installed")
        self.assertTrue(page.selected_keys())

    def test_page_builds_when_the_runtime_is_already_installed(self):
        # A checkbox toggled while rows are constructed used to reach the summary
        # label before it existed, crashing every launch after the first install.
        self._install_fake_micromamba()
        page = self._build_page()
        self.assertEqual(page.rows["micromamba"].status_label.text(), "Managed")
        self.assertNotIn("micromamba", page.selected_keys())

    def test_installed_components_cannot_be_reselected(self):
        from backend.config import reload_config

        support.install_fake_environment(reload_config(), "qc")
        page = self._build_page()
        self.assertEqual(page.rows["env:qc"].status_label.text(), "Managed")
        self.assertFalse(page.rows["env:qc"].checkbox.isEnabled())
        self.assertNotIn("env:qc", page.selected_keys())

    def test_summary_reports_the_selection_size(self):
        page = self._build_page()
        self.assertIn("to install", page.summary_label.text())
        self.assertIn("free", page.summary_label.text())


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class SetupShutdownTests(unittest.TestCase):
    """A reference download can run for hours, so closing must not orphan it."""

    application = None

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-shutdown-test-")
        os.environ["BIOFLOW_DATA_DIR"] = self._temporary.name

    def tearDown(self):
        os.environ.pop("BIOFLOW_DATA_DIR", None)
        self._temporary.cleanup()

    def test_idle_page_reports_not_running_and_shuts_down_safely(self):
        from backend.config import reload_config
        from gui.pages.setup_page import SetupPage

        reload_config()
        page = SetupPage()
        self.assertFalse(page.is_running)
        page.shutdown()  # Must be a no-op rather than an error.
        self.assertIsNone(page.thread)

    def test_a_running_plan_is_stopped_by_shutdown(self):
        from PyQt6.QtCore import QThread

        from backend.config import reload_config
        from backend.setup.plan import CommandStep, SetupPlan
        from gui.pages.setup_page import SetupPage, SetupWorker

        reload_config()
        page = SetupPage()
        plan = SetupPlan(
            steps=[CommandStep("Sleep", "sleep", ("60",))],
            components=["test"],
        )
        page.worker = SetupWorker(plan)
        page.thread = QThread(page)
        page.worker.moveToThread(page.thread)
        page.thread.started.connect(page.worker.run)
        page.thread.start()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.application.processEvents()
            with page.worker.executor._process_lock:
                child = page.worker.executor._process
            if child is not None:
                break
        self.assertIsNotNone(child, "the sleep command never started")
        self.assertTrue(page.is_running)

        page.shutdown()
        self.assertFalse(page.is_running)
        self.assertIsNone(page.thread)
        # The child must be reaped, not left running detached.
        self.assertIsNotNone(child.poll(), "the sleep command was orphaned")
