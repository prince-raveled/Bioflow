"""Running MetaPhlAn on a machine that cannot hold its index in memory.

Bowtie2 was measured holding 9.0-9.6 GB of anonymous memory aligning against
the 33 GB ChocoPhlAn index on a 14 GB machine, and the kernel killed it every
time. Neither fewer threads nor more swap helped, because the cost is the index
itself. Memory-mapping it brought the same alignment down to 5.9 GB and the run
completed. MetaPhlAn has no way to pass that option to Bowtie2, only a path to
the executable, so BioFlow writes a small script and points it there.
"""

from pathlib import Path
import subprocess
import tempfile
import unittest

import support  # noqa: F401  (puts app/ on the path)

from backend.execution.pipeline import stages_by_key  # noqa: E402
from backend.execution.stage import RunContext, RunOptions  # noqa: E402
from backend.execution.stages.taxonomy import (  # noqa: E402
    MAXIMUM_THREADS,
    write_memory_mapped_shim,
)
from backend.execution.workspace import Workspace  # noqa: E402
from backend.samples import ReadLayout, Sample  # noqa: E402


def context(root: Path, **options) -> RunContext:
    return RunContext(
        workspace=Workspace(root / "run"),
        options=RunOptions(**options),
        metaphlan_database=root / "db",
        metaphlan_index="mpa_vJan25_CHOCOPhlAnSGB_202503",
        bowtie2_memory_mapped_shim=root / "bin" / "bowtie2-mm",
        micromamba_binary=root / "bin" / "micromamba",
        micromamba_root=root / "micromamba-root",
        taxonomy_environment="bioflow-taxonomy",
    )


SINGLE = Sample("s", ReadLayout.SINGLE, Path("/in/a.fastq.gz"))
PAIRED = Sample("p", ReadLayout.PAIRED, Path("/in/a_R1.fastq.gz"), Path("/in/a_R2.fastq.gz"))


class ShimTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-shim-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.shim = self.root / "bin" / "bowtie2-mm"

    def _write(self, environment="bioflow-taxonomy"):
        return write_memory_mapped_shim(
            self.shim, self.root / "bin" / "micromamba", self.root / "mroot", environment
        )

    def test_the_shim_adds_the_memory_mapping_option(self):
        self.assertIn("--mm", self._write().read_text(encoding="utf-8"))

    def test_it_is_executable(self):
        self.assertTrue(self._write().stat().st_mode & 0o111)

    def test_it_goes_through_micromamba(self):
        # Bowtie2 is a Perl script that needs its environment activated; running
        # the executable straight out of the prefix fails on Sys::Hostname.
        text = self._write().read_text(encoding="utf-8")
        self.assertIn("micromamba", text)
        self.assertIn("run -r", text)

    def test_a_moved_environment_rewrites_it(self):
        self._write()
        self._write(environment="somewhere-else")
        self.assertIn("somewhere-else", self.shim.read_text(encoding="utf-8"))

    def test_an_unchanged_shim_is_left_alone(self):
        self._write()
        before = self.shim.stat().st_mtime_ns
        self._write()
        self.assertEqual(self.shim.stat().st_mtime_ns, before)

    def test_it_holds_no_personal_path(self):
        self.assertNotIn("/home/", self._write().read_text(encoding="utf-8"))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-mpa-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.stage = stages_by_key()["metaphlan"]

    def _command(self, sample, **options):
        return self.stage.commands(sample, context(self.root, **options))[0].command

    # -- memory ------------------------------------------------------------
    def test_bowtie2_is_always_the_memory_mapped_shim(self):
        for sample in (SINGLE, PAIRED):
            with self.subTest(layout=sample.layout.value):
                command = self._command(sample)
                self.assertIn("--bowtie2_exe", command)
                self.assertTrue(
                    command[command.index("--bowtie2_exe") + 1].endswith("bowtie2-mm")
                )

    def test_threads_are_capped_however_many_are_requested(self):
        command = self._command(SINGLE, threads=32)
        self.assertEqual(int(command[command.index("--nproc") + 1]), MAXIMUM_THREADS)

    def test_a_lower_thread_setting_is_respected(self):
        command = self._command(SINGLE, threads=1)
        self.assertEqual(command[command.index("--nproc") + 1], "1")

    def test_the_thread_count_does_not_depend_on_the_machine(self):
        # It is part of the checkpoint fingerprint: a value that moved between
        # runs would invalidate results that are still good.
        first = self._command(SINGLE, threads=4)
        second = self._command(SINGLE, threads=4)
        self.assertEqual(first, second)

    # -- paired input ------------------------------------------------------
    def test_paired_reads_default_to_profiling_every_read(self):
        command = self._command(PAIRED)
        self.assertNotIn("-1", command)
        self.assertNotIn("--subsampling_paired", command)
        joined = [part for part in command if "," in part]
        self.assertEqual(len(joined), 1, "both mates should arrive as one value")
        self.assertIn("_R1", joined[0])
        self.assertIn("_R2", joined[0])

    def test_asking_for_a_pair_count_switches_to_the_paired_flags(self):
        command = self._command(PAIRED, metaphlan_subsample_pairs=100_000)
        self.assertIn("-1", command)
        self.assertIn("-2", command)
        self.assertIn("_R1", command[command.index("-1") + 1])
        self.assertIn("_R2", command[command.index("-2") + 1])
        self.assertEqual(command[command.index("--subsampling_paired") + 1], "100000")

    def test_the_mates_are_never_silently_merged(self):
        for options in ({}, {"metaphlan_subsample_pairs": 100_000}):
            with self.subTest(options=options):
                command = self._command(PAIRED, **options)
                text = " ".join(command)
                self.assertIn("_R1", text)
                self.assertIn("_R2", text)

    def test_single_end_never_uses_the_paired_flags(self):
        command = self._command(SINGLE, metaphlan_subsample_pairs=100_000)
        self.assertNotIn("-1", command)
        self.assertNotIn("--subsampling_paired", command)

    # -- the 4.2.5 defect --------------------------------------------------
    def test_the_mapping_output_is_always_named(self):
        """Which is what keeps MetaPhlAn 4.2.5 out of its broken branch.

        init_mapout() only inspects the input path when --mapout was not given,
        and it calls os.stat() on the whole comma-joined value, which is not a
        file. Naming the output means that branch is never reached, so BioFlow
        needs no patch to the installed package.
        """
        for sample in (SINGLE, PAIRED):
            for options in ({}, {"metaphlan_subsample_pairs": 1000}):
                with self.subTest(layout=sample.layout.value, options=options):
                    self.assertIn("--mapout", self._command(sample, **options))

    def test_the_database_is_used_offline(self):
        self.assertIn("--offline", self._command(SINGLE))


class RealShimTests(unittest.TestCase):
    """Runs the generated shim against the installed Bowtie2."""

    @classmethod
    def setUpClass(cls):
        from backend.config import get_config

        cls.config = get_config()
        if not cls.config.micromamba_binary.is_file():
            raise unittest.SkipTest("BioFlow's Micromamba is not installed")
        if not cls.config.environment_prefix("taxonomy").is_dir():
            raise unittest.SkipTest("the taxonomy environment is not installed")

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-realshim-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.shim = write_memory_mapped_shim(
            self.root / "bowtie2-mm",
            self.config.micromamba_binary,
            self.config.micromamba_root,
            self.config.environment_name("taxonomy"),
        )

    def test_the_shim_runs_the_real_bowtie2(self):
        result = subprocess.run(
            [str(self.shim), "--version"], capture_output=True, text=True, timeout=300
        )
        self.assertEqual(result.returncode, 0, result.stderr[:400])
        self.assertIn("bowtie2", result.stdout.lower())

    def test_the_option_reaches_bowtie2(self):
        traced = subprocess.run(
            ["bash", "-x", str(self.shim), "--version"],
            capture_output=True, text=True, timeout=300,
        )
        invocation = [line for line in traced.stderr.splitlines() if line.startswith("+")]
        self.assertTrue(
            any("--mm" in line for line in invocation),
            "the shim did not pass --mm through to Bowtie2",
        )


class StandalonePageTests(unittest.TestCase):
    """Profiling offered on its own, the way the other tools are.

    The page exists so reads that were prepared elsewhere can be profiled
    without running the whole workflow. What it must not do is decide for itself
    how MetaPhlAn is invoked: the memory-mapped Bowtie2, the paired forms and
    the thread cap all have to be the ones the workflow uses, or the same sample
    would profile differently depending on which button was pressed.
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
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-mpa-page-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def _page(self):
        from gui.pages.metaphlan_page import MetaPhlAnPage

        page = MetaPhlAnPage()
        self.addCleanup(page.shutdown)
        return page

    def _command_for(self, page, files, layout, subsample=None):
        """The command the page would run, without running it."""
        from backend.config import METAPHLAN_INDEX, get_config
        from backend.execution.stage import RunContext, RunOptions
        from backend.execution.stages.taxonomy import MetaPhlAnStage
        from backend.execution.workspace import Workspace

        page.fastq_files = [str(path) for path in files]
        page.layout_choice.setCurrentIndex(
            [page.layout_choice.itemData(i) for i in range(page.layout_choice.count())].index(layout)
        )
        config = get_config()
        sample = page._sample()
        context = RunContext(
            workspace=Workspace(self.root / "out"),
            options=RunOptions(threads=8, metaphlan_subsample_pairs=subsample),
            metaphlan_database=config.metaphlan_database_directory,
            metaphlan_index=METAPHLAN_INDEX,
            bowtie2_memory_mapped_shim=config.bowtie2_memory_mapped_shim,
            micromamba_binary=config.micromamba_binary,
            micromamba_root=config.micromamba_root,
            taxonomy_environment=config.environment_name("taxonomy"),
        )
        stage = MetaPhlAnStage()
        return stage.profile_command(
            reads=sample.reads(),
            profile=context.workspace.taxonomic_profile(sample),
            mapout=context.workspace.taxonomic_map(sample),
            context=context,
            paired=sample.is_paired,
        ).command

    # ------------------------------------------------------------------
    def test_it_offers_the_same_choices_as_the_workflow(self):
        from gui.pages import pipeline_page

        page = self._page()
        offered = [
            (page.subsample_choice.itemText(i), page.subsample_choice.itemData(i))
            for i in range(page.subsample_choice.count())
        ]
        self.assertEqual(offered, list(pipeline_page.SUBSAMPLE_CHOICES))
        self.assertIsNone(page.subsample_choice.currentData(), "All reads is the default")

    def test_it_uses_the_shared_command_builder(self):
        page = self._page()
        command = self._command_for(page, [self.root / "a.fastq.gz"], ReadLayout.SINGLE)
        self.assertIn("--bowtie2_exe", command)
        self.assertIn("--mapout", command)
        self.assertEqual(command[command.index("--nproc") + 1], str(MAXIMUM_THREADS))

    def test_paired_selection_profiles_both_mates(self):
        page = self._page()
        command = self._command_for(
            page, [self.root / "a_R1.fastq.gz", self.root / "a_R2.fastq.gz"], ReadLayout.PAIRED
        )
        text = " ".join(command)
        self.assertIn("_R1", text)
        self.assertIn("_R2", text)

    def test_choosing_a_pair_count_uses_the_paired_flags(self):
        page = self._page()
        command = self._command_for(
            page,
            [self.root / "a_R1.fastq.gz", self.root / "a_R2.fastq.gz"],
            ReadLayout.PAIRED,
            subsample=500_000,
        )
        self.assertEqual(command[command.index("--subsampling_paired") + 1], "500000")

    def test_it_refuses_a_paired_run_with_one_file(self):
        page = self._page()
        page.fastq_files = [str(self.root / "only.fastq.gz")]
        page.layout_choice.setCurrentIndex(1)     # Paired-end
        page.output_directory = self.root / "out"
        page.run_analysis()
        self.assertIn("exactly 2 file(s)", page.log.toPlainText())

    def test_it_refuses_to_run_without_the_database(self):
        page = self._page()
        page.fastq_files = [str(self.root / "a.fastq.gz")]
        page.output_directory = self.root / "out"
        page.run_analysis()
        self.assertIn("not installed", page.log.toPlainText())

    def test_it_reports_the_database_state_before_anything_runs(self):
        page = self._page()
        self.assertTrue(page.database_label.text())
        self.assertIn("Setup & Resources", page.database_label.text())


class MemoryGuardTests(unittest.TestCase):
    """Saying why a run will be killed, before it spends a minute dying.

    MetaPhlAn loads its marker table in silence for the first minute or so. A
    kill during that window leaves nothing on screen at all, which is how a run
    that never had enough memory - or that was merely marked as the kernel's
    preferred victim - reads as an unexplained crash.
    """

    def test_a_machine_short_of_memory_is_warned(self):
        from unittest import mock

        from backend.execution.stages import taxonomy

        with mock.patch.object(taxonomy, "available_memory_bytes", return_value=2 * 1024 ** 3), \
             mock.patch.object(taxonomy, "out_of_memory_penalty", return_value=0):
            warnings = taxonomy.memory_warnings()
        self.assertTrue(warnings)
        self.assertIn("free", warnings[0])

    def test_an_out_of_memory_penalty_is_reported(self):
        from unittest import mock

        from backend.execution.stages import taxonomy

        # An editor's terminal sets this on everything it starts; the kernel
        # then kills the analysis ahead of anything else using the same memory.
        with mock.patch.object(taxonomy, "available_memory_bytes", return_value=32 * 1024 ** 3), \
             mock.patch.object(taxonomy, "out_of_memory_penalty", return_value=100):
            warnings = taxonomy.memory_warnings()
        self.assertEqual(len(warnings), 1)
        self.assertIn("oom_score_adj=100", warnings[0])
        self.assertIn("ordinary terminal", warnings[0])

    def test_a_healthy_machine_is_not_nagged(self):
        from unittest import mock

        from backend.execution.stages import taxonomy

        with mock.patch.object(taxonomy, "available_memory_bytes", return_value=32 * 1024 ** 3), \
             mock.patch.object(taxonomy, "out_of_memory_penalty", return_value=0):
            self.assertEqual(taxonomy.memory_warnings(), [])

    def test_an_unreadable_penalty_is_treated_as_none(self):
        from unittest import mock

        from backend.execution.stages import taxonomy

        with mock.patch.object(Path, "read_text", side_effect=OSError("nope")):
            self.assertEqual(taxonomy.out_of_memory_penalty(), 0)
            self.assertEqual(taxonomy.available_memory_bytes(), 0)

    def test_the_warnings_reach_the_run_log(self):
        from unittest import mock

        from backend.execution.stages import taxonomy

        messages: list[str] = []
        stage = taxonomy.MetaPhlAnStage()
        with mock.patch.object(taxonomy, "memory_warnings", return_value=["something is wrong"]), \
             mock.patch.object(taxonomy, "write_memory_mapped_shim", return_value=Path("/tmp/shim")):
            stage.prepare(SINGLE, context(Path("/tmp")), messages.append)
        self.assertTrue(any("something is wrong" in message for message in messages))


class ProfilingScaleTests(unittest.TestCase):
    """Warning before a run that would take days rather than hours.

    Profiling every read is the honest default for a test-sized sample and a
    trap for a full-depth one. On the machine this was built against, ~200,000
    reads took 88 minutes: Bowtie2 moving at about 38 reads a second, because
    the 33 GB index cannot stay in a ~5 GB page cache. A full-depth sample at
    that rate runs for days. Choosing a pair count makes the time constant.
    """

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-scale-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def _sparse(self, megabytes: int) -> Path:
        # Sparse: reports the size without occupying the disk, which matters
        # because /tmp is often a RAM-backed filesystem.
        path = self.root / f"reads-{megabytes}.fastq.gz"
        with path.open("wb") as handle:
            handle.truncate(megabytes * 1024 ** 2)
        return path

    def test_a_test_sized_sample_is_not_warned_about(self):
        from backend.execution.stages.taxonomy import profiling_scale_warning

        self.assertEqual(profiling_scale_warning([self._sparse(16)], None), "")

    def test_a_full_depth_sample_is_warned_about(self):
        from backend.execution.stages.taxonomy import profiling_scale_warning

        warning = profiling_scale_warning([self._sparse(6144)], None)
        self.assertTrue(warning)
        self.assertIn("hours", warning)
        self.assertIn("pair count", warning)

    def test_choosing_a_pair_count_silences_it(self):
        # With a limit the run takes the same time whatever the input size, so
        # there is nothing to warn about.
        from backend.execution.stages.taxonomy import profiling_scale_warning

        self.assertEqual(profiling_scale_warning([self._sparse(6144)], 100_000), "")

    def test_both_mates_count_towards_the_size(self):
        from backend.execution.stages.taxonomy import profiling_scale_warning

        halves = [self._sparse(300), self._sparse(301)]
        self.assertTrue(profiling_scale_warning(halves, None))

    def test_a_missing_input_is_not_an_error(self):
        from backend.execution.stages.taxonomy import profiling_scale_warning

        self.assertEqual(profiling_scale_warning([self.root / "absent.gz"], None), "")


class UnusableResultsFolderTests(unittest.TestCase):
    """A results folder that cannot be created is an ordinary mistake.

    It raised out of run() as a bare FileNotFoundError, which the interface
    could only report as "the analysis stopped unexpectedly" plus an errno.
    """

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-folder-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.reads = self.root / "a.fastq.gz"
        import gzip

        with gzip.open(self.reads, "wt") as handle:
            for index in range(40):
                handle.write(f"@r{index}\n{'ACGT' * 30}\n+\n{'I' * 120}\n")

    def _run_into(self, folder):
        from backend.execution.pipeline import PipelineExecutor, stages_by_key
        from backend.execution.stage import RunContext, RunOptions
        from backend.execution.workspace import Workspace

        sample = Sample("a", ReadLayout.SINGLE, self.reads)
        return PipelineExecutor(
            RunContext(workspace=Workspace(Path(folder)), options=RunOptions(threads=1)),
            [stages_by_key()["fastp"]],
            on_log=lambda _m: None,
        ).run([sample])

    def test_an_uncreatable_folder_is_reported_not_raised(self):
        outcome = self._run_into("/proc/cannot/write/here")
        self.assertFalse(outcome.succeeded)
        self.assertIn("could not be created", outcome.message)
        self.assertIn("Choose a different folder", outcome.message)

    def test_a_file_where_a_folder_belongs_is_reported(self):
        outcome = self._run_into(self.reads / "beneath-a-file")
        self.assertFalse(outcome.succeeded)
        self.assertIn("could not be created", outcome.message)

    def test_the_failure_is_recorded_like_any_other(self):
        outcome = self._run_into("/proc/cannot/write/here")
        self.assertEqual(outcome.record.status.value, "failed")
        self.assertEqual(outcome.record.stages, [], "no stage should have been attempted")


class MemoryMappingIsConditionalTests(unittest.TestCase):
    """Memory-mapping is a rescue, not an optimisation.

    It is what makes a 33 GB index usable on a machine that cannot hold it, and
    it costs enormously anywhere else. Measured on 14 GB: 37 reads a second,
    because nearly every index probe faults in from disk. Applying it to a
    machine with room to load the index would impose that penalty for nothing -
    which matters most for a container host, where memory is usually ample.
    """

    GIGABYTE = 1024 ** 3

    def _command(self, ram_gb, threads=8):
        from unittest import mock

        from backend.execution.stages import taxonomy

        with mock.patch.object(taxonomy, "total_memory_bytes",
                               return_value=ram_gb * self.GIGABYTE):
            return stages_by_key()["metaphlan"].commands(
                SINGLE, context(Path("/tmp"), threads=threads)
            )[0].command

    def test_a_small_machine_memory_maps(self):
        self.assertIn("--bowtie2_exe", self._command(14))

    def test_a_large_machine_does_not(self):
        for ram in (24, 32, 64, 128):
            with self.subTest(ram=ram):
                self.assertNotIn("--bowtie2_exe", self._command(ram))

    def test_the_threshold_is_where_the_index_stops_fitting(self):
        from backend.execution.stages.taxonomy import should_memory_map

        from unittest import mock
        from backend.execution.stages import taxonomy

        for ram, expected in ((23, True), (24, False)):
            with self.subTest(ram=ram), \
                 mock.patch.object(taxonomy, "total_memory_bytes",
                                   return_value=ram * self.GIGABYTE):
                self.assertIs(should_memory_map(), expected)

    def test_unknown_memory_takes_the_cautious_path(self):
        from backend.execution.stages.taxonomy import should_memory_map

        # Being slow is recoverable; being killed part-way through is not.
        self.assertTrue(should_memory_map(0))

    def test_threads_are_capped_only_while_mapping(self):
        mapped = self._command(14, threads=8)
        loaded = self._command(64, threads=8)
        self.assertEqual(mapped[mapped.index("--nproc") + 1], str(MAXIMUM_THREADS))
        self.assertEqual(loaded[loaded.index("--nproc") + 1], "8",
                         "threads contend only for a page cache that is too small")

    def test_the_decision_does_not_move_between_runs(self):
        # It is part of the command, and therefore of the checkpoint
        # fingerprint. Installed RAM is stable; free memory is not.
        first, second = self._command(14), self._command(14)
        self.assertEqual(first, second)

    def test_the_choice_is_explained_in_the_log(self):
        from unittest import mock

        from backend.execution.stages import taxonomy

        for ram, expected in ((14, "memory-map"), (64, "load the index")):
            messages: list[str] = []
            with self.subTest(ram=ram), \
                 mock.patch.object(taxonomy, "total_memory_bytes",
                                   return_value=ram * self.GIGABYTE), \
                 mock.patch.object(taxonomy, "write_memory_mapped_shim",
                                   return_value=Path("/tmp/shim")):
                taxonomy.MetaPhlAnStage().prepare(
                    SINGLE, context(Path("/tmp")), messages.append
                )
            self.assertTrue(any(expected in message for message in messages), messages)


class OutputNamingTests(unittest.TestCase):
    """The standalone page names its results the way the rest of BioFlow does.

    Using Path.stem removed only ".gz", so profiling a host-removed file
    produced "SRR10692360_nohost_R1.fastq_profile.txt" - the extension left in
    the middle of the name, and the results named after read one rather than
    after the sample.
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

    def _name_for(self, files, paired):
        from gui.pages.metaphlan_page import MetaPhlAnPage

        page = MetaPhlAnPage()
        self.addCleanup(page.shutdown)
        page.fastq_files = files
        page.layout_choice.setCurrentIndex(1 if paired else 0)
        return page._sample().name

    def test_a_paired_pair_is_named_once_without_the_mate_marker(self):
        name = self._name_for(
            ["/x/SRR10692360_nohost_R1.fastq.gz", "/x/SRR10692360_nohost_R2.fastq.gz"], True
        )
        self.assertEqual(name, "SRR10692360_nohost")

    def test_no_extension_survives_in_the_middle_of_a_name(self):
        for files, paired in (
            (["/x/a_nohost_R1.fastq.gz", "/x/a_nohost_R2.fastq.gz"], True),
            (["/x/a_nohost.fastq.gz"], False),
            (["/x/a.fq"], False),
        ):
            with self.subTest(files=files):
                self.assertNotIn(".fastq", self._name_for(files, paired))
                self.assertNotIn(".fq", self._name_for(files, paired))

    def test_it_matches_what_the_workflow_would_call_the_same_sample(self):
        from backend.samples import detect_samples

        paths = [Path("/x/SRR10692360_nohost_R1.fastq.gz"),
                 Path("/x/SRR10692360_nohost_R2.fastq.gz")]
        workflow = detect_samples(paths, ReadLayout.PAIRED).samples[0].name
        self.assertEqual(self._name_for([str(p) for p in paths], True), workflow)
