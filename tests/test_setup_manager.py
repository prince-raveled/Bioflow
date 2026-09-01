"""Tests for BioFlow's backend provisioning engine."""

from pathlib import Path
import os
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from backend.config import BioFlowConfig, DEFAULT_ENVIRONMENT_NAMES  # noqa: E402
from backend.setup.manager import SetupManager  # noqa: E402
from backend.setup.plan import ActionStep, CommandStep  # noqa: E402
from backend.setup.registry import DATABASES, ENVIRONMENTS  # noqa: E402


def make_config(root: Path) -> BioFlowConfig:
    return BioFlowConfig(
        data_root=root,
        database_root=root / "databases",
        environment_names=dict(DEFAULT_ENVIRONMENT_NAMES),
    )


class SetupManagerTests(unittest.TestCase):
    def setUp(self):
        # Never touch the user's real backend directory while testing.
        for variable in (
            "BIOFLOW_MICROMAMBA",
            "BIOFLOW_MAMBA_ROOT_PREFIX",
            "BIOFLOW_GRCH38_INDEX",
        ):
            os.environ.pop(variable, None)
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-test-")
        self.root = Path(self._temporary.name)
        self.manager = SetupManager(make_config(self.root))

    def tearDown(self):
        self._temporary.cleanup()

    def test_nothing_is_installed_in_a_fresh_directory(self):
        self.assertFalse(self.manager.micromamba_installed())
        self.assertTrue(all(not item.installed for item in self.manager.components()))

    def test_every_registry_entry_appears_once(self):
        keys = [component.key for component in self.manager.components()]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(keys), 1 + len(ENVIRONMENTS) + len(DATABASES))

    def test_micromamba_is_detected_once_it_is_executable(self):
        binary = self.manager.config.micromamba_binary
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o755)
        self.assertTrue(self.manager.micromamba_installed())

    def test_environment_is_detected_from_its_bin_directory(self):
        self.assertFalse(self.manager.environment_installed("qc"))
        (self.manager.config.environment_prefix("qc") / "bin").mkdir(parents=True)
        self.assertTrue(self.manager.environment_installed("qc"))

    def test_database_is_detected_from_its_marker_files(self):
        specs = {spec.key: spec for spec in DATABASES}
        metaphlan = specs["metaphlan_chocophlan"]
        self.assertFalse(self.manager.database_installed(metaphlan))
        directory = self.manager.database_directory(metaphlan)
        directory.mkdir(parents=True)
        (directory / "mpa_vJan25_CHOCOPhlAnSGB_202503.pkl").write_text("", encoding="utf-8")
        self.assertTrue(self.manager.database_installed(metaphlan))

    def test_a_complete_grch38_index_is_detected(self):
        specs = {spec.key: spec for spec in DATABASES}
        grch38 = specs["grch38"]
        self.assertFalse(self.manager.database_installed(grch38))
        prefix = self.manager.config.grch38_index_prefix
        prefix.parent.mkdir(parents=True, exist_ok=True)
        for part in ("1", "2", "3", "4", "rev.1", "rev.2"):
            Path(f"{prefix}.{part}.bt2").write_text("", encoding="utf-8")
        self.assertTrue(self.manager.database_installed(grch38))

    def test_a_partial_grch38_index_is_not_treated_as_installed(self):
        # A half-downloaded index would make Bowtie2 fail at run time; the
        # dependency gate must catch it before an analysis starts.
        specs = {spec.key: spec for spec in DATABASES}
        prefix = self.manager.config.grch38_index_prefix
        prefix.parent.mkdir(parents=True, exist_ok=True)
        for part in ("1", "2", "3"):
            Path(f"{prefix}.{part}.bt2").write_text("", encoding="utf-8")
        self.assertFalse(self.manager.database_installed(specs["grch38"]))

    def test_a_relocated_grch38_index_is_found(self):
        specs = {spec.key: spec for spec in DATABASES}
        elsewhere = self.root / "custom" / "hg38" / "MyIndex"
        elsewhere.parent.mkdir(parents=True, exist_ok=True)
        for part in ("1", "2", "3", "4", "rev.1", "rev.2"):
            Path(f"{elsewhere}.{part}.bt2l").write_text("", encoding="utf-8")
        os.environ["BIOFLOW_GRCH38_INDEX"] = str(elsewhere)
        try:
            self.assertTrue(self.manager.database_installed(specs["grch38"]))
        finally:
            os.environ.pop("BIOFLOW_GRCH38_INDEX", None)

    def test_selecting_a_database_pulls_in_the_environment_it_needs(self):
        plan = self.manager.build_plan(["db:grch38"])
        titles = [step.title for step in plan.steps]
        self.assertIn("Install the Micromamba runtime", titles)
        self.assertTrue(any("host removal environment" in title for title in titles))
        self.assertTrue(any("Bowtie2 index" in title for title in titles))

    def test_an_already_installed_environment_is_not_reinstalled(self):
        (self.manager.config.environment_prefix("hostrem") / "bin").mkdir(parents=True)
        plan = self.manager.build_plan(["db:grch38"])
        self.assertFalse(
            any("host removal environment" in step.title for step in plan.steps)
        )

    def test_plan_order_is_runtime_then_environment_then_database(self):
        plan = self.manager.build_plan(["db:metaphlan_chocophlan"])
        titles = [step.title for step in plan.steps]
        runtime = titles.index("Install the Micromamba runtime")
        environment = next(
            index for index, title in enumerate(titles) if "environment (" in title
        )
        database = next(
            index for index, title in enumerate(titles) if "marker database" in title
        )
        self.assertLess(runtime, environment)
        self.assertLess(environment, database)

    def test_empty_selection_produces_an_empty_plan(self):
        self.assertTrue(self.manager.build_plan([]).is_empty())

    def test_every_declared_database_can_build_its_steps(self):
        for spec in DATABASES:
            with self.subTest(database=spec.key):
                steps = self.manager._database_steps(spec)
                self.assertTrue(steps)
                for step in steps:
                    self.assertIsInstance(step, (CommandStep, ActionStep))

    def test_environment_commands_run_inside_bioflow_not_system_conda(self):
        plan = self.manager.build_plan(["env:qc"])
        commands = [step for step in plan.steps if isinstance(step, CommandStep)]
        self.assertTrue(commands)
        for step in commands:
            self.assertEqual(step.program, str(self.manager.config.micromamba_binary))
            self.assertIn("-r", step.arguments)
            self.assertIn(str(self.manager.config.micromamba_root), step.arguments)

    def test_estimated_size_ignores_installed_components(self):
        before = self.manager.estimated_bytes(["env:qc"])
        (self.manager.config.environment_prefix("qc") / "bin").mkdir(parents=True)
        self.assertGreater(before, 0)
        self.assertEqual(self.manager.estimated_bytes(["env:qc"]), 0)

    def test_environment_names_stay_configurable(self):
        config = make_config(self.root)
        config.environment_names["qc"] = "custom-qc"
        manager = SetupManager(config)
        plan = manager.build_plan(["env:qc"])
        creation = plan.steps[1]
        self.assertIn("custom-qc", creation.arguments)


if __name__ == "__main__":
    unittest.main()
