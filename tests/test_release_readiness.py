"""What somebody meets when they install this on a machine that has nothing.

Two things are being protected here. The first is that container execution
stays optional: the default path must need nothing but what BioFlow installs
for itself, and a machine with no container runtime must not be told it has a
problem. The second is that the documentation describes this application rather
than a plausible one - every component key, environment variable and script it
names is checked against the code.
"""

from pathlib import Path
from unittest import mock
import re
import unittest

import support  # noqa: F401  (puts app/ on the path)

from backend.config import BioFlowConfig, get_config  # noqa: E402
from backend.execution.container import ContainerResolver  # noqa: E402
from backend.setup import preflight  # noqa: E402
from backend.setup.manager import SetupManager  # noqa: E402


REPOSITORY = Path(__file__).resolve().parent.parent
DOCUMENT = REPOSITORY / "docs" / "INSTALL.md"


class ContainerPreflightTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(support.isolate_bioflow_data_directory(self))

    def config(self, backend="native") -> BioFlowConfig:
        config = get_config()
        config.execution_backend = backend
        return config

    def test_no_runtime_is_not_a_problem_for_a_native_install(self):
        """The default path must not look broken on an ordinary machine.

        Most machines have no container runtime, and most people never want
        one. Reporting that as a failure would be telling them to fix
        something that is not wrong.
        """
        with mock.patch("backend.execution.container.detect_runtime", return_value=None):
            check = preflight.container_check(self.config("native"))
        self.assertTrue(check.passed)
        self.assertIn("needs nothing further", check.detail)

    def test_no_runtime_is_a_problem_once_containers_are_selected(self):
        with mock.patch("backend.execution.container.detect_runtime", return_value=None):
            check = preflight.container_check(self.config("container"))
        self.assertFalse(check.passed)
        self.assertIn("Podman", check.detail, "the message should name what to install")

    def test_a_missing_image_says_how_to_get_it(self):
        with mock.patch("backend.execution.container.detect_runtime", return_value="podman"), \
             mock.patch.object(ContainerResolver, "image_present", return_value=False):
            check = preflight.container_check(self.config("container"))
        self.assertFalse(check.passed)
        self.assertIn("build.sh", check.detail)

    def test_a_missing_image_is_only_a_note_when_running_natively(self):
        with mock.patch("backend.execution.container.detect_runtime", return_value="podman"), \
             mock.patch.object(ContainerResolver, "image_present", return_value=False):
            check = preflight.container_check(self.config("native"))
        self.assertTrue(check.passed)

    def test_a_ready_container_setup_is_reported(self):
        with mock.patch("backend.execution.container.detect_runtime", return_value="podman"), \
             mock.patch.object(ContainerResolver, "image_present", return_value=True):
            check = preflight.container_check(self.config("container"))
        self.assertTrue(check.passed)
        self.assertIn("podman", check.detail)

    def test_it_never_blocks_setup(self):
        """Setup installs the databases the native path needs.

        Blocking it because a container runtime is missing would stop somebody
        installing the very thing that lets them work without one.
        """
        for backend in ("native", "container"):
            for runtime in (None, "podman"):
                with self.subTest(backend=backend, runtime=runtime), \
                     mock.patch("backend.execution.container.detect_runtime", return_value=runtime), \
                     mock.patch.object(ContainerResolver, "image_present", return_value=False):
                    self.assertFalse(preflight.container_check(self.config(backend)).blocking)

    def test_the_check_appears_in_the_full_preflight(self):
        names = [check.name for check in preflight.run_preflight(self.config())]
        self.assertIn("Container execution", names)

    def test_preflight_still_passes_with_no_container_support(self):
        with mock.patch("backend.execution.container.detect_runtime", return_value=None):
            checks = preflight.run_preflight(self.config("native"))
        self.assertEqual(preflight.blocking_failures(checks), [])


@unittest.skipUnless(DOCUMENT.is_file(), "docs/INSTALL.md is absent")
class DocumentationTests(unittest.TestCase):
    """The documentation must describe this application, not a plausible one."""

    @classmethod
    def setUpClass(cls):
        cls.text = DOCUMENT.read_text(encoding="utf-8")

    def setUp(self):
        support.isolate_bioflow_data_directory(self)

    def test_every_component_key_it_names_exists(self):
        named = set(re.findall(r"`((?:env|db):[a-z0-9_]+)`", self.text))
        self.assertTrue(named, "the document names no component keys")
        real = {component.key for component in SetupManager(get_config()).components()}
        self.assertEqual(named - real, set(), "the document names components that do not exist")

    def test_every_environment_variable_it_names_is_read(self):
        named = set(re.findall(r"`(BIOFLOW_[A-Z0-9_]+)`", self.text))
        self.assertTrue(named)
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (REPOSITORY / "app").rglob("*.py")
        )
        for variable in sorted(named):
            with self.subTest(variable=variable):
                self.assertIn(variable, source)

    def test_every_script_it_names_exists(self):
        for relative in re.findall(r"`?((?:scripts|docker)/[a-z0-9_.]+\.sh)`?", self.text):
            with self.subTest(script=relative):
                self.assertTrue((REPOSITORY / relative).is_file(), relative)

    def test_it_states_the_released_pipeline(self):
        for tool in ("FastQC", "fastp", "host removal", "MetaPhlAn", "MultiQC"):
            with self.subTest(tool=tool):
                self.assertIn(tool, self.text)

    def test_it_does_not_advertise_withheld_work(self):
        # The interface offers six stages. Documentation promising more would
        # be describing a product that does not exist yet.
        for term in ("HUMAnN", "functional profiling", "SparCC", "diversity analysis"):
            with self.subTest(term=term):
                self.assertNotIn(term.lower(), self.text.lower())

    def test_it_says_native_execution_is_the_default(self):
        self.assertRegex(self.text, r"(?i)runs on this machine by default")

    def test_it_says_databases_are_not_in_the_image(self):
        self.assertRegex(self.text, r"(?i)mounted read-only")

    def test_it_records_the_memory_threshold_that_governs_a_run(self):
        # The single number that decides whether an analysis finishes or is
        # killed on a small machine.
        self.assertIn("24 GB", self.text)

    def test_it_warns_about_swap(self):
        self.assertIn("zram", self.text)


class LauncherTests(unittest.TestCase):
    """The scripts a person actually types."""

    def script(self, name) -> str:
        return (REPOSITORY / "scripts" / name).read_text(encoding="utf-8")

    def test_the_launcher_and_installer_agree_on_where_python_lives(self):
        # They disagreed once, and the launcher then reported the application
        # as "not set up" immediately after the installer said it was ready.
        for name in ("install_bioflow_linux.sh", "run_bioflow_linux.sh"):
            with self.subTest(script=name):
                self.assertIn("/python", self.script(name))

    def test_the_installer_does_not_require_a_container_runtime(self):
        text = self.script("install_bioflow_linux.sh")
        self.assertIn("No container runtime found", text)
        # It reports, it does not install or exit.
        self.assertNotRegex(text, r"(?m)^\s*(sudo\s+)?(dnf|apt|apt-get)\s+install.*\b(podman|docker)\b")

    def test_the_installer_points_at_the_image_build_script(self):
        self.assertIn("docker/build.sh", self.script("install_bioflow_linux.sh"))

    def test_the_launcher_names_the_installer_when_nothing_is_set_up(self):
        self.assertIn("install_bioflow_linux.sh", self.script("run_bioflow_linux.sh"))


if __name__ == "__main__":
    unittest.main()
