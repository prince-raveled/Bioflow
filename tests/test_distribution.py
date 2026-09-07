"""What someone gets when they clone this repository and try to use it.

Both of these were found by cloning the published repository and following its
own instructions, which is the only way to find them: everything works when run
from a checkout that has been lived in for weeks.
"""

from pathlib import Path
import stat
import subprocess
import sys
import unittest

import support  # noqa: F401  (puts app/ on the path)

from backend.config import get_config  # noqa: E402
from backend.release import component_is_available  # noqa: E402
from backend.setup.manager import SetupManager  # noqa: E402


REPOSITORY = Path(__file__).resolve().parent.parent

#: Everything a person is told to run. The README's first install step is one
#: of these, so a missing bit here is a first-run failure.
EXECUTABLES = (
    "scripts/install_bioflow_linux.sh",
    "scripts/run_bioflow_linux.sh",
    "scripts/setup_dev_env.sh",
    "scripts/check_metaphlan.sh",
    "docker/build.sh",
)


def tracked_mode(relative: str) -> str:
    """The file mode git records, which is what a fresh clone gets.

    Not the mode on this disk: a script can be executable here because of how
    it was created while git has it as 100644, and then it is only broken for
    everyone else.
    """
    result = subprocess.run(
        ["git", "ls-files", "-s", "--", relative],
        cwd=REPOSITORY, capture_output=True, text=True, timeout=60,
    )
    return result.stdout.split()[0] if result.stdout.strip() else ""


class ExecutableBitTests(unittest.TestCase):
    """A cloned script must run without being chmod'ed first."""

    def test_every_script_is_executable_in_git(self):
        for relative in EXECUTABLES:
            with self.subTest(script=relative):
                self.assertEqual(
                    tracked_mode(relative), "100755",
                    f"{relative} is not executable in a fresh clone",
                )

    def test_every_script_is_executable_on_disk(self):
        for relative in EXECUTABLES:
            with self.subTest(script=relative):
                mode = (REPOSITORY / relative).stat().st_mode
                self.assertTrue(mode & stat.S_IXUSR, relative)

    def test_every_script_has_an_interpreter_line(self):
        for relative in EXECUTABLES:
            with self.subTest(script=relative):
                first = (REPOSITORY / relative).read_text(encoding="utf-8").splitlines()[0]
                self.assertTrue(first.startswith("#!"), f"{relative}: {first!r}")

    def test_the_scripts_the_readme_tells_you_to_run_exist(self):
        readme = (REPOSITORY / "README.md").read_text(encoding="utf-8")
        for relative in ("scripts/install_bioflow_linux.sh", "scripts/run_bioflow_linux.sh"):
            with self.subTest(script=relative):
                self.assertIn(relative, readme)
                self.assertTrue((REPOSITORY / relative).is_file())


class ReleaseGateTests(unittest.TestCase):
    """The gate has to hold everywhere a component can be installed.

    It was applied where components are listed but not where they are
    installed, so the interface never offered HUMAnN while the headless CLI
    would happily download and build it - a large download for a tool the
    application will not run.
    """

    def setUp(self):
        support.isolate_bioflow_data_directory(self)
        self.manager = SetupManager(get_config())

    def test_a_withheld_component_is_not_listed(self):
        keys = {component.key for component in self.manager.components()}
        self.assertNotIn("env:function", keys)
        self.assertNotIn("db:humann_uniref50", keys)

    def test_a_withheld_component_produces_no_plan(self):
        self.assertTrue(self.manager.build_plan(["env:function"]).is_empty())
        self.assertTrue(self.manager.build_plan(["db:humann_uniref50"]).is_empty())

    def test_a_withheld_database_cannot_pull_in_its_environment(self):
        # The gate is applied after dependency expansion for this reason.
        plan = self.manager.build_plan(["db:humann_chocophlan"])
        self.assertTrue(plan.is_empty(), plan.components)

    def test_a_released_component_still_plans(self):
        self.assertFalse(self.manager.build_plan(["env:qc"]).is_empty())

    def test_a_mixed_request_keeps_the_released_half(self):
        plan = self.manager.build_plan(["env:qc", "env:function"])
        self.assertFalse(plan.is_empty())
        self.assertNotIn("Functional profiling", plan.components)

    def test_withheld_keys_are_reported_for_the_caller(self):
        self.assertEqual(self.manager.withheld_keys(["env:qc", "env:function"]), ["env:function"])

    def test_available_keys_drops_only_the_withheld(self):
        available = self.manager.available_keys(["env:qc", "env:function"])
        self.assertIn("env:qc", available)
        self.assertNotIn("env:function", available)

    def test_the_gate_and_the_listing_agree(self):
        for component in self.manager.components():
            with self.subTest(key=component.key):
                self.assertTrue(component_is_available(component.key))


class SetupCommandLineTests(unittest.TestCase):
    """The headless entry point, which takes whatever key it is given."""

    def setUp(self):
        self.root = support.isolate_bioflow_data_directory(self)

    def run_cli(self, *arguments):
        return subprocess.run(
            [sys.executable, "-m", "backend.setup.cli", "--dry-run", *arguments],
            cwd=REPOSITORY / "app", capture_output=True, text=True, timeout=300,
            env={**__import__("os").environ, "BIOFLOW_DATA_DIR": str(self.root)},
        )

    def test_a_withheld_component_is_refused_and_explained(self):
        result = self.run_cli("--install", "env:function")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Not part of this release", result.stderr)
        self.assertIn("env:function", result.stderr)

    def test_an_unknown_component_lists_what_is_available(self):
        result = self.run_cli("--install", "env:nonsense")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Unknown component", result.stderr)
        self.assertIn("env:qc", result.stderr)

    def test_a_mixed_request_proceeds_with_what_it_can(self):
        result = self.run_cli("--install", "env:qc", "env:function")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Not part of this release", result.stderr)
        self.assertIn("Quality control", result.stdout)
        self.assertNotIn("humann", result.stdout.lower())

    def test_a_valid_request_still_plans(self):
        result = self.run_cli("--install", "env:qc")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Plan:", result.stdout)

    def test_status_lists_only_released_components(self):
        result = self.run_cli("--status")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("humann", result.stdout.lower())
        self.assertIn("env:qc", result.stdout)


if __name__ == "__main__":
    unittest.main()
