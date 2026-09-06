"""REAL execution tests: these invoke genuine tools from BioFlow's own runtime.

Unlike every other test file, nothing here is mocked. Each test is skipped
unless the managed environment it needs is actually installed, so the suite
still passes on a machine that has not run Setup yet.
"""

from pathlib import Path
import tempfile
import os
import unittest

import support  # noqa: F401  (puts app/ on the path)
from backend.config import bowtie2_index_is_complete, get_config, reload_config  # noqa: E402
from backend.execution.pipeline import PipelineExecutor, stages_by_key  # noqa: E402
from backend.execution.record import StageStatus  # noqa: E402
from backend.execution.stage import RunContext, RunOptions  # noqa: E402
from backend.execution.workspace import Workspace  # noqa: E402
from backend.project import Project  # noqa: E402
from backend.samples import ReadLayout, Sample  # noqa: E402
from support import write_fastq  # noqa: E402


def qc_environment_available() -> bool:
    config = reload_config()
    if not (config.micromamba_binary.is_file() and config.environment_is_installed("qc")):
        return False
    return all(
        (config.environment_prefix("qc") / "bin" / tool).exists()
        for tool in ("fastqc", "fastp", "multiqc")
    )


QC_STAGES = ("fastqc_raw", "fastp", "fastqc_trimmed", "multiqc")


@unittest.skipUnless(
    qc_environment_available(),
    "BioFlow's managed quality-control environment is not installed",
)
class RealQualityControlTests(unittest.TestCase):
    """Runs FastQC, fastp and MultiQC for real through BioFlow's micromamba."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-real-")
        self.root = Path(self._temporary.name)
        self.registry = stages_by_key()

    def tearDown(self):
        self._temporary.cleanup()

    def _stages(self):
        return [self.registry[key] for key in QC_STAGES]

    def test_single_end_runs_end_to_end_with_real_tools(self):
        reads = write_fastq(self.root / "input" / "demo.fastq.gz", records=500)
        project, problems = Project.from_files(
            "real-single", self.root / "run", [reads],
            layout=ReadLayout.SINGLE, options=RunOptions(threads=2),
        )
        self.assertEqual(problems, [])
        self.assertEqual(project.validate_inputs(), [])

        outcome = PipelineExecutor(
            project.context(), self._stages(), on_log=lambda _m: None
        ).run(project.samples)

        self.assertTrue(outcome.succeeded, outcome.message)
        for record in outcome.record.stages:
            self.assertIs(record.status, StageStatus.COMPLETED, record.message)
        workspace = project.workspace
        self.assertTrue(workspace.trimmed_reads(project.samples[0])[0].is_file())
        self.assertTrue(workspace.multiqc_report().is_file())

    def test_paired_end_runs_end_to_end_and_keeps_mates_separate(self):
        read1 = write_fastq(self.root / "input" / "demo_R1.fastq.gz", records=500, mate="1")
        read2 = write_fastq(self.root / "input" / "demo_R2.fastq.gz", records=500, mate="2")
        project, problems = Project.from_files(
            "real-paired", self.root / "run", [read1, read2], options=RunOptions(threads=2)
        )
        self.assertEqual(problems, [])
        self.assertIs(project.layout, ReadLayout.PAIRED)

        outcome = PipelineExecutor(
            project.context(), self._stages(), on_log=lambda _m: None
        ).run(project.samples)

        self.assertTrue(outcome.succeeded, outcome.message)
        trimmed = project.workspace.trimmed_reads(project.samples[0])
        self.assertEqual(len(trimmed), 2)
        for path in trimmed:
            self.assertTrue(path.is_file(), path)
            self.assertGreater(path.stat().st_size, 0)

    def test_resume_skips_completed_stages_on_a_real_rerun(self):
        reads = write_fastq(self.root / "input" / "demo.fastq.gz", records=200)
        project, _ = Project.from_files(
            "real-resume", self.root / "run", [reads],
            layout=ReadLayout.SINGLE, options=RunOptions(threads=2),
        )
        stages = self._stages()
        self.assertTrue(
            PipelineExecutor(project.context(), stages, on_log=lambda _m: None)
            .run(project.samples).succeeded
        )
        outcome = PipelineExecutor(
            project.context(), stages, on_log=lambda _m: None
        ).run(project.samples)
        self.assertTrue(outcome.succeeded)
        for record in outcome.record.stages:
            self.assertIs(record.status, StageStatus.SKIPPED, record.stage_key)

    def test_a_corrupt_input_makes_a_real_tool_fail_the_pipeline(self):
        broken = self.root / "input" / "broken.fastq.gz"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_bytes(b"\x1f\x8b\x08\x00 not a real deflate stream")
        project, _ = Project.from_files(
            "real-broken", self.root / "run", [broken], layout=ReadLayout.SINGLE
        )
        self.assertTrue(project.validate_inputs(), "the validator should reject this file")

        outcome = PipelineExecutor(
            project.context(), self._stages(), on_log=lambda _m: None
        ).run(project.samples)
        self.assertFalse(outcome.succeeded)
        self.assertIs(outcome.record.find("fastqc_raw", "broken").status, StageStatus.FAILED)
        self.assertIs(outcome.record.find("fastp", "broken").status, StageStatus.BLOCKED)


@unittest.skipUnless(
    qc_environment_available(), "BioFlow's managed environment is not installed"
)
class RealEnvironmentResolutionTests(unittest.TestCase):
    def test_commands_resolve_to_bioflows_own_micromamba(self):
        from backend.execution.environment import EnvironmentResolver

        resolver = EnvironmentResolver(get_config())
        program, arguments = resolver.resolve("qc", ["fastqc", "--version"])
        self.assertEqual(program, str(get_config().micromamba_binary))
        self.assertIn("bioflow-qc", arguments)
        self.assertNotIn("conda", program)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(
    qc_environment_available(), "BioFlow's managed environment is not installed"
)
class RealGuiDrivenRunTests(unittest.TestCase):
    """Drive a real analysis through the workflow page, as a user would."""

    application = None

    @classmethod
    def setUpClass(cls):
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        import os

        from backend.config import reload_config

        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-gui-real-")
        self.root = Path(self._temporary.name)

        # This test needs the real installation - real tools, the real GRCh38
        # index - which is the whole point of it. It does not need, and must
        # not have, the developer's real run history: driving the workflow page
        # records a run the way pressing the button does, and every suite run
        # was appending a row to the history a person actually reads.
        self._saved_history = os.environ.get("BIOFLOW_HISTORY_DB")
        os.environ["BIOFLOW_HISTORY_DB"] = str(self.root / "history.sqlite3")
        reload_config()

    def tearDown(self):
        import os

        from backend.config import reload_config

        if self._saved_history is None:
            os.environ.pop("BIOFLOW_HISTORY_DB", None)
        else:
            os.environ["BIOFLOW_HISTORY_DB"] = self._saved_history
        reload_config()
        self._temporary.cleanup()

    def test_a_paired_end_analysis_runs_from_the_page(self):
        import time

        from backend.execution.record import PipelineRecord
        from gui.pages.pipeline_page import PipelinePage

        read1 = write_fastq(self.root / "in" / "demo_R1.fastq.gz", records=300, mate="1")
        read2 = write_fastq(self.root / "in" / "demo_R2.fastq.gz", records=300, mate="2")

        page = PipelinePage()
        self.addCleanup(page.shutdown)
        page.output_directory = self.root / "results"
        page.selected_files = [read1, read2]
        page._rebuild_project()

        self.assertIs(page.project.layout, ReadLayout.PAIRED)
        for key, box in page.stage_boxes.items():
            box.setChecked(key in QC_STAGES)
        page.refresh_readiness()
        self.assertTrue(page.run_button.isEnabled(), page.status_label.text())

        page.start_run()
        deadline = time.time() + 240
        while page.is_running and time.time() < deadline:
            self.application.processEvents()
            time.sleep(0.05)
        self.application.processEvents()

        self.assertFalse(page.is_running, "the run did not finish in time")
        record = PipelineRecord.load(page.project.workspace.checkpoint_file)
        self.assertIsNotNone(record)
        self.assertEqual(len(record.stages), len(QC_STAGES))
        for entry in record.stages:
            self.assertIs(entry.status, StageStatus.COMPLETED, entry.message)
        self.assertTrue(page.project.workspace.multiqc_report().is_file())


def grch38_available() -> bool:
    """True when any GRCh38 index resolves - managed, external, or override."""
    return reload_config().resolve_grch38_index().usable


def hostrem_environment_available() -> bool:
    config = reload_config()
    return (
        config.micromamba_binary.is_file()
        and config.environment_is_installed("hostrem")
        and (config.environment_prefix("hostrem") / "bin" / "bowtie2").exists()
    )


@unittest.skipUnless(
    qc_environment_available() and hostrem_environment_available() and grch38_available(),
    "the managed host-removal environment or a resolvable GRCh38 index is missing",
)
class RealHostRemovalTests(unittest.TestCase):
    """Runs Bowtie2 for real against whichever GRCh38 index BioFlow resolves.

    Deliberately index-agnostic: it works with a managed installation or an
    explicitly configured external one, so it never depends on one machine's
    filesystem layout.
    """

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-hostrem-real-")
        self.root = Path(self._temporary.name)

    def tearDown(self):
        self._temporary.cleanup()

    def test_host_removal_uses_the_resolved_index_and_produces_reads(self):
        from backend.config import get_config
        from backend.execution.pipeline import PipelineExecutor
        from backend.execution.stages.host_removal import HostRemovalStage

        config = get_config()
        resolved = config.resolve_grch38_index()

        reads = write_fastq(self.root / "input" / "hostrem.fastq.gz", records=400)
        project, _ = Project.from_files(
            "real-hostrem", self.root / "run", [reads],
            layout=ReadLayout.SINGLE, options=RunOptions(threads=2),
        )
        registry = stages_by_key()
        stages = [registry[key] for key in ("fastp", "host_removal")]

        # The command must carry exactly the prefix the resolver reported.
        sample = project.samples[0]
        command = registry["host_removal"].commands(sample, project.context())[0].command
        self.assertEqual(command[command.index("-x") + 1], str(resolved.prefix))

        outcome = PipelineExecutor(
            project.context(), stages, on_log=lambda _m: None
        ).run(project.samples)
        self.assertTrue(outcome.succeeded, outcome.message)

        workspace = project.workspace
        self.assertTrue(workspace.host_removed_reads(sample)[0].is_file())
        rate = HostRemovalStage.alignment_rate(workspace.bowtie2_log(sample))
        self.assertIsNotNone(rate, "Bowtie2 did not report an alignment rate")

    def test_paired_end_output_is_named_exactly_as_metaphlan_expects(self):
        """The handoff from host removal to taxonomic profiling, run for real.

        Bowtie2 does not take the two output names: it takes one --un-conc-gz
        template and substitutes the mate number into a "%". Whether that lands
        on the names the next stage reads is a property of Bowtie2's behaviour,
        not of anything this codebase can assert on its own, so it is checked
        here against the real aligner. Only single-end was covered before.
        """
        import gzip

        workspace, sample, context = self._paired_workspace()

        for target in workspace.trimmed_reads(sample):
            with gzip.open(target, "wt") as handle:
                for index in range(400):
                    handle.write(f"@read{index}\n{'ACGTTGCA' * 18}\n+\n{'I' * 144}\n")

        stage = stages_by_key()["host_removal"]
        outcome = PipelineExecutor(context, [stage], on_log=lambda _m: None).run([sample])
        self.assertTrue(outcome.succeeded, outcome.message)

        declared = set(stage.outputs(sample, context))
        written = {path for path in workspace.host_removed.iterdir()}
        self.assertEqual(
            declared,
            written,
            "Bowtie2 wrote different files from the ones the stage declared",
        )
        for path in stages_by_key()["metaphlan"].inputs(sample, context):
            self.assertTrue(
                path.is_file(),
                f"taxonomic profiling would look for {path.name}, which was not written",
            )

    def _paired_workspace(self):
        resolved = get_config().resolve_grch38_index()
        workspace = Workspace(self.root / "paired")
        workspace.create()
        sample = Sample(
            "pairsample",
            ReadLayout.PAIRED,
            self.root / "pairsample_R1.fastq.gz",
            self.root / "pairsample_R2.fastq.gz",
        )
        context = RunContext(
            workspace=workspace,
            options=RunOptions(threads=2),
            host_index_prefix=resolved.prefix,
        )
        return workspace, sample, context

    def test_running_never_writes_into_the_managed_store_when_external(self):
        from backend.config import ResourceState, get_config

        config = get_config()
        resolved = config.resolve_grch38_index()
        if resolved.state is ResourceState.MANAGED:
            self.skipTest("the resolved index is the managed one")
        managed = config.managed_grch38_index_prefix
        self.assertFalse(
            bowtie2_index_is_complete(managed),
            "an external reference must not have populated the managed store",
        )


#: Resident memory `bowtie2-align-l` needs for the ChocoPhlAn index. Measured
#: from an OOM kill that reported 9,786,628 kB of anonymous memory, rounded up.
METAPHLAN_MEMORY_BYTES = 10 * 1024 ** 3


def available_memory_bytes() -> int:
    """Memory actually free for a new process, or 0 when it cannot be read.

    Deliberately MemAvailable rather than MemTotal: a machine with enough RAM
    installed can still be unable to spare it, and an alignment that is killed
    part-way through takes unrelated processes down with it.
    """
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return 0
    return 0


def metaphlan_ready() -> bool:
    """True when both the taxonomy environment and its database are installed."""
    from backend.setup.manager import SetupManager
    from backend.setup.registry import database_specs

    config = reload_config()
    if not (config.micromamba_binary.is_file() and config.environment_is_installed("taxonomy")):
        return False
    return SetupManager(config).managed_database_present(
        database_specs()["metaphlan_chocophlan"]
    )


def metaphlan_has_memory() -> bool:
    """True when there is room to align without provoking the OOM killer.

    Honours BIOFLOW_SKIP_HEAVY_TESTS so a developer sharing the machine with an
    editor can run the suite without a 10 GB alignment evicting it.
    """
    if os.environ.get("BIOFLOW_SKIP_HEAVY_TESTS"):
        return False
    available = available_memory_bytes()
    return available == 0 or available >= METAPHLAN_MEMORY_BYTES


@unittest.skipUnless(
    metaphlan_ready(),
    "the MetaPhlAn environment or its marker database is not installed",
)
@unittest.skipUnless(
    metaphlan_has_memory(),
    "less than 10 GB of memory is available; profiling would be killed",
)
class RealMetaPhlAnTests(unittest.TestCase):
    """Profiles reads for real, offline, against the installed marker database.

    Skipped unless the ~33 GB database is present, so the suite still runs on a
    machine that has not installed it.
    """

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-mpa-real-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def _project(self, files, layout):
        project, problems = Project.from_files(
            "real-metaphlan", self.root / "run", files,
            layout=layout, options=RunOptions(threads=4),
        )
        self.assertEqual(problems, [])
        return project

    def test_single_end_profiling_produces_a_usable_profile(self):
        from backend.execution.pipeline import PipelineExecutor
        from backend.execution.stage import RunOptions as Options  # noqa: F401

        reads = write_fastq(self.root / "input" / "sample.fastq.gz", records=2000)
        project = self._project([reads], ReadLayout.SINGLE)
        sample = project.samples[0]

        # Host removal is not installed everywhere, so feed MetaPhlAn directly
        # by placing the reads where the stage expects to find them.
        workspace = project.workspace
        workspace.create()
        target = workspace.host_removed_reads(sample)[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(reads.read_bytes())

        stage = stages_by_key()["metaphlan"]
        outcome = PipelineExecutor(
            project.context(), [stage], on_log=lambda _m: None
        ).run([sample])

        self.assertTrue(outcome.succeeded, outcome.message)
        profile = workspace.taxonomic_profile(sample)
        self.assertTrue(profile.is_file())
        text = profile.read_text(encoding="utf-8")
        # MetaPhlAn always writes its provenance header naming the index used.
        self.assertIn("mpa_vJan25", text)

    def test_the_command_runs_offline_against_the_managed_database(self):
        from backend.config import get_config

        config = get_config()
        sample = Sample("s", ReadLayout.SINGLE, self.root / "s.fastq.gz")
        project = Project(name="x", root=self.root / "run")
        command = stages_by_key()["metaphlan"].commands(sample, project.context())[0].command
        self.assertIn("--offline", command)
        self.assertEqual(
            command[command.index("--db_dir") + 1], str(config.metaphlan_database_directory)
        )
