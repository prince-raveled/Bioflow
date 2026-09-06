"""Configuration persistence and GRCh38 resource resolution.

Covers the managed / external / development distinction, the six-file index
rule, and the guarantee that installation always targets BioFlow's own store.
No real tool runs here.
"""

from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

from support import clear_bioflow_environment  # noqa: E402
from backend.config import (  # noqa: E402
    BOWTIE2_INDEX_PARTS,
    DEVELOPMENT_GRCH38_VARIABLE,
    EXTERNAL_METAPHLAN_VARIABLE,
    BioFlowConfig,
    ResourceState,
    bowtie2_index_is_complete,
    bowtie2_index_is_partial,
    bowtie2_index_prefix_from_file,
    reload_config,
)
from backend.setup.manager import SetupManager  # noqa: E402
from backend.setup.registry import database_specs  # noqa: E402


APP_DIRECTORY = str(Path(__file__).resolve().parents[1] / "app")


def build_index(prefix: Path, extension: str = "bt2", parts=BOWTIE2_INDEX_PARTS) -> Path:
    """Create the files of a Bowtie2 index (or a subset, to test incompleteness)."""
    prefix.parent.mkdir(parents=True, exist_ok=True)
    for part in parts:
        Path(f"{prefix}.{part}.{extension}").write_text("index", encoding="utf-8")
    return prefix


class ResourceTestCase(unittest.TestCase):
    def setUp(self):
        clear_bioflow_environment()
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-resource-")
        self.root = Path(self._temporary.name)
        os.environ["BIOFLOW_DATA_DIR"] = str(self.root)

    def tearDown(self):
        clear_bioflow_environment()
        self._temporary.cleanup()

    def config(self) -> BioFlowConfig:
        return reload_config()


# ----------------------------------------------------------------------
# Six-file validation
# ----------------------------------------------------------------------
class IndexValidationTests(ResourceTestCase):
    def test_a_complete_index_validates(self):
        prefix = build_index(self.root / "db" / "GRCh38_index")
        self.assertTrue(bowtie2_index_is_complete(prefix))
        self.assertFalse(bowtie2_index_is_partial(prefix))

    def test_five_of_six_files_is_incomplete(self):
        prefix = build_index(self.root / "db" / "GRCh38_index", parts=BOWTIE2_INDEX_PARTS[:5])
        self.assertFalse(bowtie2_index_is_complete(prefix))
        self.assertTrue(bowtie2_index_is_partial(prefix))

    def test_each_missing_file_breaks_validation(self):
        for omitted in BOWTIE2_INDEX_PARTS:
            with self.subTest(missing=omitted):
                kept = tuple(p for p in BOWTIE2_INDEX_PARTS if p != omitted)
                prefix = build_index(self.root / omitted / "GRCh38_index", parts=kept)
                self.assertFalse(bowtie2_index_is_complete(prefix))

    def test_large_index_extension_validates(self):
        prefix = build_index(self.root / "large" / "GRCh38_index", extension="bt2l")
        self.assertTrue(bowtie2_index_is_complete(prefix))

    def test_nothing_present_is_neither_complete_nor_partial(self):
        prefix = self.root / "empty" / "GRCh38_index"
        self.assertFalse(bowtie2_index_is_complete(prefix))
        self.assertFalse(bowtie2_index_is_partial(prefix))

    def test_prefix_is_derived_from_any_index_file(self):
        for name in ("X.1.bt2", "X.rev.2.bt2", "X.4.bt2l", "X.rev.1.bt2"):
            with self.subTest(file=name):
                self.assertEqual(
                    bowtie2_index_prefix_from_file(Path("/db") / name), Path("/db/X")
                )

    def test_a_non_index_file_yields_no_prefix(self):
        self.assertIsNone(bowtie2_index_prefix_from_file(Path("/db/readme.txt")))


# ----------------------------------------------------------------------
# Configuration persistence
# ----------------------------------------------------------------------
class ConfigurationPersistenceTests(ResourceTestCase):
    def test_config_is_written_on_first_use(self):
        config = self.config()
        self.assertTrue(config.settings_file.is_file())
        stored = json.loads(config.settings_file.read_text(encoding="utf-8"))
        self.assertEqual(stored["database_root"], str(config.database_root))
        self.assertIn("references", stored)

    def test_defaults_are_preserved_when_no_file_exists(self):
        config = self.config()
        self.assertEqual(config.default_threads, 4)
        self.assertEqual(config.environment_names["qc"], "bioflow-qc")
        self.assertIsNone(config.external_grch38_index)

    def test_saving_twice_does_not_rewrite_the_file(self):
        config = self.config()
        self.assertFalse(config.save(), "an unchanged configuration should not be rewritten")

    def test_saving_after_a_change_does_write(self):
        config = self.config()
        config.default_threads = 12
        self.assertTrue(config.save())
        self.assertEqual(self.config().default_threads, 12)

    def test_an_unreadable_config_falls_back_to_defaults(self):
        config = self.config()
        config.settings_file.write_text("{ this is not json", encoding="utf-8")
        self.assertEqual(self.config().default_threads, 4)

    def test_settings_survive_a_completely_fresh_process(self):
        # The strongest form of "survives a restart": a separate interpreter.
        config = self.config()
        prefix = build_index(self.root / "outside" / "MyIndex")
        config.set_external_grch38_index(prefix)

        result = subprocess.run(
            [sys.executable, "-c",
             "from backend.config import get_config;"
             "c = get_config();"
             "print(c.external_grch38_index);"
             "print(c.resolve_grch38_index().state.value)"],
            cwd=APP_DIRECTORY,
            env={**os.environ, "BIOFLOW_DATA_DIR": str(self.root), "PYTHONPATH": APP_DIRECTORY},
            capture_output=True, text=True, check=True,
        )
        lines = result.stdout.strip().splitlines()
        self.assertEqual(lines[0], str(prefix))
        self.assertEqual(lines[1], "external")


# ----------------------------------------------------------------------
# Resolution: managed, external, development
# ----------------------------------------------------------------------
class ResolutionTests(ResourceTestCase):
    def test_a_clean_machine_reports_missing(self):
        resolved = self.config().resolve_grch38_index()
        self.assertIs(resolved.state, ResourceState.MISSING)
        self.assertIsNone(resolved.prefix)
        self.assertFalse(resolved.usable)

    def test_a_managed_index_is_reported_as_managed(self):
        config = self.config()
        build_index(config.managed_grch38_index_prefix)
        resolved = config.resolve_grch38_index()
        self.assertIs(resolved.state, ResourceState.MANAGED)
        self.assertEqual(resolved.prefix, config.managed_grch38_index_prefix)

    def test_an_explicit_external_index_is_reported_as_external(self):
        config = self.config()
        prefix = build_index(self.root / "elsewhere" / "GRCh38_index")
        config.set_external_grch38_index(prefix)
        resolved = self.config().resolve_grch38_index()
        self.assertIs(resolved.state, ResourceState.EXTERNAL)
        self.assertEqual(resolved.prefix, prefix)
        self.assertNotEqual(resolved.prefix, resolved.managed_prefix)

    def test_an_incomplete_index_is_reported_as_incomplete_not_missing(self):
        config = self.config()
        build_index(config.managed_grch38_index_prefix, parts=BOWTIE2_INDEX_PARTS[:3])
        resolved = config.resolve_grch38_index()
        self.assertIs(resolved.state, ResourceState.INCOMPLETE)
        self.assertFalse(resolved.usable)

    def test_an_incomplete_external_index_cannot_be_configured(self):
        config = self.config()
        prefix = build_index(self.root / "half" / "GRCh38_index", parts=BOWTIE2_INDEX_PARTS[:4])
        with self.assertRaises(ValueError):
            config.set_external_grch38_index(prefix)
        self.assertIsNone(self.config().external_grch38_index)

    def test_clearing_the_external_index_returns_to_managed(self):
        config = self.config()
        external = build_index(self.root / "elsewhere" / "GRCh38_index")
        build_index(config.managed_grch38_index_prefix)
        config.set_external_grch38_index(external)
        self.assertIs(self.config().resolve_grch38_index().state, ResourceState.EXTERNAL)
        self.config().set_external_grch38_index(None)
        self.assertIs(self.config().resolve_grch38_index().state, ResourceState.MANAGED)

    def test_external_wins_over_managed_when_both_exist(self):
        config = self.config()
        build_index(config.managed_grch38_index_prefix)
        external = build_index(self.root / "elsewhere" / "GRCh38_index")
        config.set_external_grch38_index(external)
        self.assertEqual(self.config().resolve_grch38_index().prefix, external)

    def test_a_stale_external_path_falls_back_to_managed(self):
        config = self.config()
        external = build_index(self.root / "gone" / "GRCh38_index")
        config.set_external_grch38_index(external)
        for part in BOWTIE2_INDEX_PARTS:
            Path(f"{external}.{part}.bt2").unlink()
        build_index(config.managed_grch38_index_prefix)
        self.assertIs(self.config().resolve_grch38_index().state, ResourceState.MANAGED)


# ----------------------------------------------------------------------
# The development override must never contaminate install or status
# ----------------------------------------------------------------------
class DevelopmentOverrideTests(ResourceTestCase):
    def test_the_override_resolves_but_is_labelled_development(self):
        prefix = build_index(self.root / "fixture" / "GRCh38_index")
        os.environ[DEVELOPMENT_GRCH38_VARIABLE] = str(prefix)
        resolved = self.config().resolve_grch38_index()
        self.assertIs(resolved.state, ResourceState.DEVELOPMENT)
        self.assertEqual(resolved.prefix, prefix)

    def test_the_override_never_reports_a_managed_resource(self):
        prefix = build_index(self.root / "fixture" / "GRCh38_index")
        os.environ[DEVELOPMENT_GRCH38_VARIABLE] = str(prefix)
        manager = SetupManager(self.config())
        row = [c for c in manager.components() if c.key == "db:grch38"][0]
        self.assertIs(row.state, ResourceState.DEVELOPMENT)
        self.assertFalse(row.is_managed)
        # The location shown is the one in use, not BioFlow's empty store.
        self.assertEqual(row.location, prefix)
        self.assertNotEqual(row.location, row.managed_location)
        self.assertIn("Development override", row.describe_state())

    def test_the_override_never_changes_the_install_target(self):
        prefix = build_index(self.root / "fixture" / "GRCh38_index")
        os.environ[DEVELOPMENT_GRCH38_VARIABLE] = str(prefix)
        config = self.config()
        manager = SetupManager(config)
        plan = manager.build_plan(["db:grch38"])
        build = [s for s in plan.steps if "Bowtie2 index" in s.title][0]
        self.assertEqual(build.arguments[-1], str(config.managed_grch38_index_prefix))
        self.assertNotIn(str(prefix), " ".join(build.arguments))

    def test_the_override_is_never_persisted_as_configuration(self):
        prefix = build_index(self.root / "fixture" / "GRCh38_index")
        os.environ[DEVELOPMENT_GRCH38_VARIABLE] = str(prefix)
        config = self.config()
        config.save()
        stored = json.loads(config.settings_file.read_text(encoding="utf-8"))
        self.assertIsNone(stored["references"]["grch38_index_prefix"])
        self.assertIsNone(config.external_grch38_index)


# ----------------------------------------------------------------------
# Installation always targets the managed store
# ----------------------------------------------------------------------
class InstallTargetTests(ResourceTestCase):
    def _build_step(self, config):
        plan = SetupManager(config).build_plan(["db:grch38"])
        return [s for s in plan.steps if "Bowtie2 index" in s.title][0]

    def test_install_target_is_the_managed_location(self):
        config = self.config()
        self.assertEqual(
            self._build_step(config).arguments[-1],
            str(config.managed_grch38_index_prefix),
        )

    def test_an_external_reference_does_not_move_the_install(self):
        config = self.config()
        external = build_index(self.root / "elsewhere" / "GRCh38_index")
        config.set_external_grch38_index(external)
        step = self._build_step(self.config())
        self.assertEqual(step.arguments[-1], str(config.managed_grch38_index_prefix))
        self.assertNotIn(str(external), " ".join(step.arguments))

    def test_no_installer_step_writes_outside_the_managed_store(self):
        config = self.config()
        external = build_index(self.root / "elsewhere" / "GRCh38_index")
        config.set_external_grch38_index(external)
        os.environ[DEVELOPMENT_GRCH38_VARIABLE] = str(external)
        plan = SetupManager(self.config()).build_plan(["db:grch38"])
        for step in plan.steps:
            arguments = " ".join(getattr(step, "arguments", ()) or ())
            self.assertNotIn(
                str(external), f"{step.title} {arguments}",
                f"step '{step.title}' references an external path",
            )

    def test_the_managed_directory_is_never_overridable(self):
        os.environ[DEVELOPMENT_GRCH38_VARIABLE] = "/somewhere/else/Index"
        config = self.config()
        self.assertEqual(
            config.managed_grch38_index_prefix,
            config.database_root / "human" / "hg38" / "GRCh38_index",
        )


# ----------------------------------------------------------------------
# Setup status for every state
# ----------------------------------------------------------------------
class StatusReportingTests(ResourceTestCase):
    def _grch38_row(self):
        return [c for c in SetupManager(self.config()).components() if c.key == "db:grch38"][0]

    def test_missing_state(self):
        row = self._grch38_row()
        self.assertIs(row.state, ResourceState.MISSING)
        self.assertFalse(row.installed)
        self.assertEqual(row.describe_state(), "Not installed")

    def test_incomplete_state(self):
        config = self.config()
        build_index(config.managed_grch38_index_prefix, parts=BOWTIE2_INDEX_PARTS[:2])
        row = self._grch38_row()
        self.assertIs(row.state, ResourceState.INCOMPLETE)
        self.assertFalse(row.installed)

    def test_managed_state(self):
        build_index(self.config().managed_grch38_index_prefix)
        row = self._grch38_row()
        self.assertIs(row.state, ResourceState.MANAGED)
        self.assertTrue(row.installed)
        self.assertEqual(row.location, row.managed_location)
        self.assertTrue(row.describe_state().startswith("Managed / "))

    def test_external_state(self):
        config = self.config()
        external = build_index(self.root / "elsewhere" / "GRCh38_index")
        config.set_external_grch38_index(external)
        row = self._grch38_row()
        self.assertIs(row.state, ResourceState.EXTERNAL)
        self.assertTrue(row.installed)
        self.assertEqual(row.location, external)
        self.assertTrue(row.describe_state().startswith("External / "))

    def test_other_databases_still_report_managed_or_missing(self):
        specs = database_specs()
        manager = SetupManager(self.config())
        self.assertIs(manager.database_state(specs["metaphlan_chocophlan"]).state,
                      ResourceState.MISSING)
        directory = manager.database_directory(specs["metaphlan_chocophlan"])
        directory.mkdir(parents=True, exist_ok=True)
        # A part-unpacked dataset is reported as incomplete rather than as
        # ready to use, and equally not as absent: it is tens of gigabytes.
        (directory / "mpa_test.pkl").write_text("", encoding="utf-8")
        self.assertIs(manager.database_state(specs["metaphlan_chocophlan"]).state,
                      ResourceState.INCOMPLETE)
        for pattern in specs["metaphlan_chocophlan"].required_globs:
            (directory / pattern).write_text("", encoding="utf-8")
        self.assertIs(manager.database_state(specs["metaphlan_chocophlan"]).state,
                      ResourceState.MANAGED)



class MetaPhlAnLocationTests(ResourceTestCase):
    """Pointing MetaPhlAn at a database BioFlow did not install.

    GRCh38 has had an override since the beginning. MetaPhlAn had none, so its
    ~51 GB database could only ever live under BioFlow's own database root -
    which a read-only mount, a shared filesystem or a container cannot always
    satisfy, and which copying is not a realistic answer to.
    """

    def test_the_managed_location_is_the_default(self):
        config = self.config()
        self.assertEqual(
            config.metaphlan_database_directory,
            config.database_directory("metaphlan"),
        )

    def test_the_override_redirects_the_database(self):
        elsewhere = self.root / "shared" / "metaphlan"
        elsewhere.mkdir(parents=True)
        os.environ[EXTERNAL_METAPHLAN_VARIABLE] = str(elsewhere)
        self.assertEqual(self.config().metaphlan_database_directory, elsewhere)

    def test_the_override_expands_a_home_relative_path(self):
        os.environ[EXTERNAL_METAPHLAN_VARIABLE] = "~/somewhere/metaphlan"
        resolved = self.config().metaphlan_database_directory
        self.assertTrue(resolved.is_absolute())
        self.assertNotIn("~", str(resolved))

    def test_an_empty_override_is_not_an_override(self):
        os.environ[EXTERNAL_METAPHLAN_VARIABLE] = ""
        config = self.config()
        self.assertEqual(
            config.metaphlan_database_directory,
            config.database_directory("metaphlan"),
        )

    def test_an_incomplete_override_is_still_reported_incomplete(self):
        # Pointing elsewhere must never be a way past the completeness check:
        # the seven required files are demanded of whatever path is in use.
        elsewhere = self.root / "partial" / "metaphlan"
        elsewhere.mkdir(parents=True)
        (elsewhere / "mpa_vJan25_CHOCOPhlAnSGB_202503.pkl").write_bytes(b"x")
        os.environ[EXTERNAL_METAPHLAN_VARIABLE] = str(elsewhere)
        manager = SetupManager(self.config())
        row = [c for c in manager.components() if c.key == "db:metaphlan_chocophlan"][0]
        self.assertIsNot(row.state, ResourceState.MANAGED)

if __name__ == "__main__":
    unittest.main()
