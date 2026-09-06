"""Choosing where analysis runs, from the interface.

One control, on the page that already owns everything else about the machine's
setup. Native is the default and needs nothing installed; container execution is
for people who want the tools frozen, and choosing it should not require
learning anything about containers.

The wording says "container" rather than "Docker" deliberately. The image is
OCI and runs under Podman or Docker, and naming one would imply the other does
not work - which on Fedora, where Podman is what ships, would be backwards.
"""

from pathlib import Path
from unittest import mock
import os
import unittest

import support  # noqa: F401  (puts app/ on the path)

# Before PyQt6 is imported: an interface is built here, and there is no display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - PyQt6 is a hard requirement in practice
    QApplication = None

from backend.config import get_config, reload_config  # noqa: E402
from backend.execution import container  # noqa: E402
from backend.execution.container import ContainerResolver  # noqa: E402


class QtTestCase(unittest.TestCase):
    """One QApplication for the process, as the other interface tests do."""

    application = None

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class BackendControlTests(QtTestCase):
    """Against an isolated data directory: this writes settings."""

    def setUp(self):
        self.root = Path(support.isolate_bioflow_data_directory(self))
        from gui.pages.setup_page import SetupPage

        self.page = SetupPage()
        self.addCleanup(self.page.deleteLater)

    def options(self):
        choice = self.page.execution_choice
        return [choice.itemData(index) for index in range(choice.count())]

    def choose(self, backend):
        choice = self.page.execution_choice
        choice.setCurrentIndex(choice.findData(backend))

    # ------------------------------------------------------------------
    def test_both_backends_are_offered(self):
        self.assertEqual(self.options(), ["native", "container"])

    def test_it_opens_on_native(self):
        self.assertEqual(self.page.execution_choice.currentData(), "native")

    def test_the_labels_do_not_name_a_particular_runtime(self):
        choice = self.page.execution_choice
        labels = " ".join(choice.itemText(i) for i in range(choice.count())).lower()
        self.assertNotIn("docker", labels)
        self.assertNotIn("podman", labels)
        self.assertIn("container", labels)

    def test_choosing_a_backend_persists_it(self):
        self.choose("container")
        reload_config()
        self.assertEqual(get_config().execution_backend, "container")

    def test_switching_back_persists_too(self):
        self.choose("container")
        self.choose("native")
        reload_config()
        self.assertEqual(get_config().execution_backend, "native")

    def test_the_image_control_is_only_live_for_containers(self):
        self.choose("native")
        self.assertFalse(self.page.choose_image.isEnabled())
        self.choose("container")
        self.assertTrue(self.page.choose_image.isEnabled())

    def test_a_refresh_does_not_resave_or_announce_a_change(self):
        """Refreshing must not look like the user changed something.

        The row is refreshed on every setup change, and a combo box that
        re-entered its own handler would save on each one and log a change
        nobody made.
        """
        self.choose("container")
        with mock.patch.object(type(self.page.config), "set_execution_backend") as saved:
            self.page.refresh()
        saved.assert_not_called()

    # ------------------------------------------------------------------
    def test_it_says_so_when_no_runtime_is_installed(self):
        self.choose("container")
        with mock.patch("gui.pages.setup_page.detect_runtime", return_value=None):
            self.page.refresh()
        detail = self.page.execution_label.text().lower()
        self.assertIn("no container runtime", detail)
        self.assertIn("podman", detail, "the message should name what to install")

    def test_it_says_so_when_the_image_is_missing(self):
        self.choose("container")
        with mock.patch("gui.pages.setup_page.detect_runtime", return_value="podman"), \
             mock.patch("gui.pages.setup_page.ContainerResolver.image_present", return_value=False):
            self.page.refresh()
        detail = self.page.execution_label.text()
        self.assertIn("build.sh", detail, "the message should say how to get the image")

    def test_it_confirms_when_everything_is_ready(self):
        self.choose("container")
        with mock.patch("gui.pages.setup_page.detect_runtime", return_value="podman"), \
             mock.patch("gui.pages.setup_page.ContainerResolver.image_present", return_value=True):
            self.page.refresh()
        self.assertIn("Image available", self.page.execution_label.text())

    def test_native_mentions_that_nothing_else_is_needed(self):
        # The reassurance that matters most: the default asks nothing of you.
        self.choose("native")
        self.assertIn("Nothing else needs installing", self.page.execution_label.text())

    def test_both_backends_say_databases_come_from_this_machine(self):
        # They are mounted either way, so the component list below applies to
        # both and a person should not have to wonder.
        for backend in ("native", "container"):
            with self.subTest(backend=backend):
                self.choose(backend)
                self.assertIn("Reference databases", self.page.execution_label.text())


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class ScopeTests(QtTestCase):
    """The interface must not grow past the released pipeline."""

    WITHHELD = ("humann", "functional", "sparcc", "diversity", "scfa",
                "network", "interpret", "pathway")

    def setUp(self):
        support.isolate_bioflow_data_directory(self)

    def test_navigation_offers_only_the_released_pipeline(self):
        from gui.navigation import NAVIGATION, page_names

        text = (" ".join(page_names()) + " " +
                " ".join(section for section, _pages in NAVIGATION)).lower()
        for term in self.WITHHELD:
            with self.subTest(term=term):
                self.assertNotIn(term, text)

    def test_the_setup_page_does_not_advertise_withheld_work(self):
        from gui.pages.setup_page import SetupPage

        page = SetupPage()
        self.addCleanup(page.deleteLater)
        text = page.execution_label.text().lower()
        for term in self.WITHHELD:
            with self.subTest(term=term):
                self.assertNotIn(term, text)

    def test_the_released_stages_are_unchanged(self):
        from backend.execution.pipeline import default_stages

        self.assertEqual(
            [stage.key for stage in default_stages()],
            ["fastqc_raw", "fastp", "fastqc_trimmed", "host_removal", "metaphlan", "multiqc"],
        )


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class StandalonePageBackendTests(QtTestCase):
    """A tool page follows the same setting as the workflow.

    Someone who has chosen container execution should get it everywhere, not
    only where the pipeline runs; and the two must choose it the same way, or
    they will drift.
    """

    def setUp(self):
        support.isolate_bioflow_data_directory(self)

    def test_a_tool_page_resolves_through_the_configured_backend(self):
        from backend.execution.environment import EnvironmentResolver
        from gui.pages.fastqc_page import FastQCPage

        page = FastQCPage()
        self.addCleanup(page.deleteLater)
        with mock.patch(
            "gui.pages.qc_tool_page.resolver_for",
            return_value=mock.Mock(resolve=mock.Mock(return_value=("x", []))),
        ) as chosen:
            page._resolve_command(["fastqc", "--version"])
        chosen.assert_called_once()

    def test_it_uses_the_same_factory_as_the_executor(self):
        import inspect

        from backend.execution import pipeline
        from gui.pages import qc_tool_page

        self.assertIn("resolver_for", inspect.getsource(qc_tool_page))
        self.assertIn("resolver_for", inspect.getsource(pipeline))


if __name__ == "__main__":
    unittest.main()
