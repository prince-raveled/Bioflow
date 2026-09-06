"""Downloads must be visible while they run and verified once they finish.

These datasets are tens of gigabytes and take hours. The tests here cover the
two ways that goes wrong for a user: an install that looks like it has hung,
and one that reports success while the data is only partly there.
"""

from pathlib import Path
import tempfile
import unittest

import support  # noqa: F401  (puts app/ on the path)
from backend.config import ResourceState  # noqa: E402
from backend.setup import bootstrap  # noqa: E402
from backend.setup.executor import PlanExecutor  # noqa: E402
from backend.setup.manager import SetupManager  # noqa: E402
from backend.setup.plan import SetupPlan  # noqa: E402
from backend.setup.progress import (  # noqa: E402
    Phase,
    detect_phase,
    parse_progress,
)
from backend.setup.registry import DatabaseSpec, database_specs  # noqa: E402


class ProgressRecognitionTests(unittest.TestCase):
    """Progress lines are recognised by shape, not by their line ending."""

    def test_metaphlan_progress_is_recognised(self):
        # MetaPhlAn prints a whole line per update rather than redrawing with a
        # carriage return, so delimiter-based detection misses all of them.
        update = parse_progress("1068.22 MB 21.88 %   2.23 MB/sec 28 min 33 sec")
        self.assertIsNotNone(update)
        self.assertAlmostEqual(update.percent, 21.88)

    def test_a_progress_bar_is_recognised(self):
        self.assertIsNotNone(parse_progress("fastqc 45% ━━━━━━━━━ 5.1MB / 11.3MB"))

    def test_prose_mentioning_a_percentage_is_not_progress(self):
        self.assertIsNone(parse_progress("The pipeline finished 100% of its samples"))

    def test_a_sentence_without_a_rate_is_not_progress(self):
        self.assertIsNone(parse_progress("Downloading file of size: 4881.7773 MB"))

    def test_an_impossible_percentage_is_rejected(self):
        self.assertIsNone(parse_progress("error 900% MB/sec"))


class PhaseDetectionTests(unittest.TestCase):
    def test_each_phase_is_named(self):
        for line, expected in (
            ("Checking md5 of /db/x_bt2.tar", Phase.VERIFYING),
            ("Extracting the archive", Phase.EXTRACTING),
            ("Downloading http://example/x.tar", Phase.DOWNLOADING),
            ("Building the Bowtie2 index", Phase.BUILDING),
            ("Removing x.tar to reclaim space", Phase.CLEANING),
        ):
            self.assertIs(detect_phase(line), expected, line)

    def test_a_download_that_mentions_unpacking_is_still_a_download(self):
        # MetaPhlAn announces "Downloading and uncompressing bowtie2 indexes"
        # before it downloads anything; calling that extraction would lie.
        self.assertIs(
            detect_phase("Downloading and uncompressing bowtie2 indexes"),
            Phase.DOWNLOADING,
        )

    def test_the_echoed_command_is_not_a_phase(self):
        self.assertIsNone(detect_phase("$ micromamba run -n x metaphlan --install"))


class OutputRoutingTests(unittest.TestCase):
    """Progress goes to the indicator; the log keeps what happened."""

    def _executor(self, **kwargs):
        return PlanExecutor(SetupPlan(steps=[]), **kwargs)

    def test_progress_does_not_reach_the_log(self):
        logged, updates = [], []
        executor = self._executor(on_output=logged.append, on_progress=updates.append)
        for _ in range(500):
            executor._emit("500.00 MB 10.00 %   2.20 MB/sec 20 min 00 sec", transient=False)
        self.assertEqual(logged, [], "progress must not flood the log")
        self.assertTrue(updates)

    def test_progress_falls_back_to_the_log_without_an_indicator(self):
        logged = []
        executor = self._executor(on_output=logged.append)
        executor._emit("500.00 MB 10.00 %   2.20 MB/sec 20 min 00 sec", transient=False)
        self.assertEqual(len(logged), 1, "visibility must not depend on a GUI")

    def test_durable_lines_reach_the_log(self):
        logged = []
        executor = self._executor(on_output=logged.append, on_progress=lambda u: None)
        executor._emit("Downloading file of size: 33433.5645 MB", transient=False)
        self.assertIn("Downloading file of size: 33433.5645 MB", logged)

    def test_each_phase_is_announced_once(self):
        logged = []
        executor = self._executor(on_output=logged.append, on_progress=lambda u: None)
        for line in (
            "Downloading http://example/a.tar",
            "Downloading http://example/b.tar",
            "Checking md5 of a.tar",
        ):
            executor._emit(line, transient=False)
        banners = [entry for entry in logged if entry.startswith("-- ")]
        self.assertEqual(banners, ["-- Downloading --", "-- Verifying --"])

    def test_a_silent_tool_reports_that_it_is_still_working(self):
        logged = []
        executor = self._executor(on_output=logged.append, on_progress=lambda u: None)
        executor._emit("500.00 MB 10.00 %   2.20 MB/sec 20 min 00 sec", transient=False)
        # Unpacking a 33 GB archive prints nothing for minutes; silence must be
        # explained rather than looking like a hang.
        executor._last_output -= 120.0
        executor._last_notice -= 120.0
        executor._report_silence()
        self.assertTrue(
            any("Still working" in entry for entry in logged), logged
        )

    def test_silence_is_reported_after_a_durable_line_too(self):
        # The long silent untar follows the checksum line, not a progress line,
        # so tying the notice to progress alone would miss the worst stall.
        logged = []
        executor = self._executor(on_output=logged.append, on_progress=lambda u: None)
        executor._emit("Checking md5 of db.tar", transient=False)
        executor._last_output -= 300.0
        executor._last_notice -= 300.0
        executor._report_silence()
        self.assertTrue(any("Still working after 5m" in e for e in logged), logged)

    def test_silence_is_not_reported_before_any_output(self):
        logged = []
        executor = self._executor(on_output=logged.append, on_progress=lambda u: None)
        executor._last_output -= 600.0
        executor._last_notice -= 600.0
        executor._report_silence()
        self.assertEqual(logged, [])


class ArchiveReclamationTests(unittest.TestCase):
    """Downloaded archives are removed; installed data is not."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="bioflow-reclaim-"))
        self.addCleanup(self._remove_directory)
        self.logged: list[str] = []

    def _remove_directory(self):
        import shutil

        shutil.rmtree(self.directory, ignore_errors=True)

    def _write(self, name: str, size: int = 1024) -> Path:
        path = self.directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"0" * size)
        return path

    def test_archives_are_deleted_and_space_is_reported(self):
        archive = self._write("db_bt2.tar", 4096)
        freed = bootstrap.reclaim_archives(self.directory, self.logged.append)
        self.assertFalse(archive.exists())
        self.assertEqual(freed, 4096)
        self.assertTrue(any("Reclaimed" in entry for entry in self.logged))

    def test_partial_downloads_are_deleted(self):
        partial = self._write("db.tar.part")
        bootstrap.reclaim_archives(self.directory, self.logged.append)
        self.assertFalse(partial.exists())

    def test_sequence_files_survive(self):
        # ChocoPhlAn ships as thousands of .ffn.gz files. Treating a bare .gz
        # as an archive would delete the database it just downloaded.
        sequence = self._write("g__Escherichia.centroids.ffn.gz")
        bootstrap.reclaim_archives(self.directory, self.logged.append)
        self.assertTrue(sequence.exists())

    def test_installed_data_survives(self):
        index = self._write("mpa_index.1.bt2l")
        marker = self._write("mpa_index.pkl")
        bootstrap.reclaim_archives(self.directory, self.logged.append)
        self.assertTrue(index.exists())
        self.assertTrue(marker.exists())

    def test_a_required_file_is_never_removed(self):
        # A dataset that legitimately ships as a .tar must not be deleted by
        # the cleanup that follows its own download.
        required = self._write("payload.tar")
        bootstrap.reclaim_archives(
            self.directory, self.logged.append, keep=("payload.tar",)
        )
        self.assertTrue(required.exists())

    def test_a_required_glob_protects_a_matching_archive(self):
        # Required files are declared as patterns, so cleanup must match them
        # as patterns or it deletes the very data it was told to keep.
        required = self._write("uniref50_annotated.tar")
        bootstrap.reclaim_archives(
            self.directory, self.logged.append, keep=("*_annotated.tar",)
        )
        self.assertTrue(required.exists())

    def test_a_clean_directory_says_so(self):
        bootstrap.reclaim_archives(self.directory, self.logged.append)
        self.assertTrue(any("No leftover archives" in e for e in self.logged))

    def test_a_missing_directory_is_not_an_error(self):
        self.assertEqual(
            bootstrap.reclaim_archives(
                self.directory / "absent", self.logged.append
            ),
            0,
        )


class CompletenessTests(unittest.TestCase):
    """A marker file alone must not certify a partly unpacked dataset."""

    def setUp(self):
        support.isolate_bioflow_data_directory(self)
        self.manager = SetupManager()
        self.spec = database_specs()["metaphlan_chocophlan"]
        self.directory = self.manager.database_directory(self.spec)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _create(self, patterns):
        for pattern in patterns:
            (self.directory / pattern).write_bytes(b"0")

    def test_a_lone_marker_is_not_a_complete_install(self):
        self._create([self.spec.required_globs[0]])
        self.assertFalse(self.manager.managed_database_present(self.spec))

    def test_a_partial_install_reports_which_files_are_missing(self):
        self._create(self.spec.required_globs[:3])
        missing = self.manager.missing_database_files(self.spec)
        self.assertEqual(set(missing), set(self.spec.required_globs[3:]))

    def test_a_partial_install_is_incomplete_not_missing(self):
        # Reporting 38 GB of unpacked data as absent invites a re-download.
        self._create([self.spec.required_globs[0]])
        self.assertIs(
            self.manager.database_state(self.spec).state, ResourceState.INCOMPLETE
        )

    def test_an_untouched_directory_is_missing(self):
        self.assertIs(
            self.manager.database_state(self.spec).state, ResourceState.MISSING
        )

    def test_every_required_file_makes_it_complete(self):
        self._create(self.spec.required_globs)
        self.assertTrue(self.manager.managed_database_present(self.spec))
        self.assertEqual(self.manager.missing_database_files(self.spec), [])


class StatusWordingTests(unittest.TestCase):
    """An incomplete dataset says what is still absent."""

    def _status(self, missing):
        from backend.setup.manager import ComponentStatus

        return ComponentStatus(
            key="db:x", kind="database", title="t", description="d",
            state=ResourceState.INCOMPLETE, approximate_bytes=1,
            missing_files=tuple(missing),
        )

    def test_the_missing_file_is_named(self):
        self.assertIn("index.pkl", self._status(["index.pkl"]).describe_state())

    def test_a_long_list_is_summarised(self):
        text = self._status([f"f{n}" for n in range(7)]).describe_state()
        self.assertIn("and 4 more", text)

    def test_a_complete_dataset_reports_its_location(self):
        from backend.setup.manager import ComponentStatus

        status = ComponentStatus(
            key="db:x", kind="database", title="t", description="d",
            state=ResourceState.MANAGED, approximate_bytes=1,
            location=Path("/data/x"),
        )
        self.assertIn("/data/x", status.describe_state())


class EnvironmentCompletenessTests(unittest.TestCase):
    """An empty bin directory is not an installed environment."""

    def setUp(self):
        support.isolate_bioflow_data_directory(self)
        self.manager = SetupManager()
        self.binaries = self.manager.config.environment_prefix("qc") / "bin"

    def _install(self, tools):
        self.binaries.mkdir(parents=True, exist_ok=True)
        for tool in tools:
            (self.binaries / tool).write_bytes(b"")

    def test_an_absent_environment_is_missing(self):
        self.assertFalse(self.manager.environment_installed("qc"))

    def test_a_bare_bin_directory_is_not_installed(self):
        # An interrupted environment creation leaves this behind; calling it
        # installed defers the failure into the middle of an analysis run.
        self._install([])
        self.assertFalse(self.manager.environment_installed("qc"))

    def test_a_partial_environment_names_its_missing_tools(self):
        self._install(["fastqc"])
        missing = self.manager.missing_environment_tools("qc")
        self.assertNotIn("fastqc", missing)
        self.assertTrue(missing)

    def test_a_partial_environment_reports_incomplete(self):
        self._install(["fastqc"])
        status = self._status("env:qc")
        self.assertIs(status.state, ResourceState.INCOMPLETE)
        self.assertIn("still missing", status.describe_state())

    def test_a_full_environment_is_installed(self):
        from backend.setup.registry import environment_specs

        tools = {c[0] for c in environment_specs()["qc"].verify_commands}
        self._install(tools)
        self.assertTrue(self.manager.environment_installed("qc"))
        self.assertIs(self._status("env:qc").state, ResourceState.MANAGED)

    def _status(self, key):
        return next(c for c in self.manager.components() if c.key == key)


class VerificationStepTests(unittest.TestCase):
    """Every dataset install ends by proving the result is usable."""

    def setUp(self):
        support.isolate_bioflow_data_directory(self)
        self.manager = SetupManager()
        self.logged: list[str] = []

    def _steps(self, key):
        return self.manager._database_steps(database_specs()[key])

    def test_every_dataset_reclaims_then_verifies(self):
        for key in database_specs():
            titles = [step.title for step in self._steps(key)]
            self.assertTrue(titles[-2].startswith("Reclaim disk space"), key)
            self.assertTrue(titles[-1].startswith("Check that"), key)

    def test_verification_fails_when_files_are_missing(self):
        spec = database_specs()["metaphlan_chocophlan"]
        self.manager.database_directory(spec).mkdir(parents=True, exist_ok=True)
        verify = self._steps("metaphlan_chocophlan")[-1]
        with self.assertRaises(RuntimeError) as raised:
            verify.action(self.logged.append)
        self.assertIn(".pkl", str(raised.exception))

    def test_verification_fails_on_a_truncated_dataset(self):
        # Files present but nearly empty: the glob passes, the dataset is not
        # there. Size is what separates the two.
        spec = database_specs()["metaphlan_chocophlan"]
        directory = self.manager.database_directory(spec)
        directory.mkdir(parents=True, exist_ok=True)
        for pattern in spec.required_globs:
            (directory / pattern).write_bytes(b"0")
        verify = self._steps("metaphlan_chocophlan")[-1]
        with self.assertRaises(RuntimeError) as raised:
            verify.action(self.logged.append)
        self.assertIn("truncated", str(raised.exception))

    def test_verification_passes_for_a_full_dataset(self):
        spec = DatabaseSpec(
            key="humann_uniref50", title="t", description="d",
            environment_key="function", directory_parts=("humann", "uniref"),
            marker_globs=("*.dmnd",), approximate_bytes=1024,
            required_globs=("*.dmnd",),
        )
        directory = self.manager.database_directory(spec)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "uniref50.dmnd").write_bytes(b"0" * 1024)
        self.manager._verify_step(spec).action(self.logged.append)
        self.assertTrue(any("Verified" in entry for entry in self.logged))


class ReferenceCleanupTests(unittest.TestCase):
    """The GRCh38 FASTA goes only once the index that replaces it exists."""

    def setUp(self):
        support.isolate_bioflow_data_directory(self)
        self.manager = SetupManager()
        self.logged: list[str] = []
        self.reference = (
            self.manager.config.managed_grch38_directory
            / "GRCh38.primary_assembly.genome.fa.gz"
        )
        self.reference.parent.mkdir(parents=True, exist_ok=True)
        self.reference.write_bytes(b"0" * 2048)

    def _build_index(self):
        prefix = self.manager.config.managed_grch38_index_prefix
        for part in ("1", "2", "3", "4", "rev.1", "rev.2"):
            Path(f"{prefix}.{part}.bt2").write_bytes(b"0")

    def test_the_fasta_is_kept_while_the_index_is_incomplete(self):
        self.manager._discard_reference(self.reference, self.logged.append)
        self.assertTrue(self.reference.exists())

    def test_the_fasta_is_removed_once_the_index_is_complete(self):
        self._build_index()
        self.manager._discard_reference(self.reference, self.logged.append)
        self.assertFalse(self.reference.exists())
        self.assertTrue(any("reclaiming" in entry for entry in self.logged))

    def test_removing_an_absent_fasta_is_not_an_error(self):
        self._build_index()
        self.reference.unlink()
        self.manager._discard_reference(self.reference, self.logged.append)


if __name__ == "__main__":
    unittest.main()


class RealInstallLogTests(unittest.TestCase):
    """Phase detection checked against lines a real MetaPhlAn install emitted.

    Every string here was copied from the log of a completed 37.5 GB install.
    Two of them were misread before that log existed: "Decompressing" did not
    match a pattern written around the word "uncompress", so the extraction
    phase never appeared; and "Download complete." restarted the download phase
    after cleanup had already been announced.
    """

    def test_decompressing_is_extraction(self):
        self.assertIs(
            detect_phase(
                "Thu Sep  3 09:57:46 2026: Decompressing "
                "/db/mpa_vJan25_CHOCOPhlAnSGB_202503_SGB.fna.bz2 into /db/..._SGB.fna"
            ),
            Phase.EXTRACTING,
        )

    def test_a_completion_notice_does_not_reopen_a_phase(self):
        self.assertIsNone(detect_phase("Thu Sep  3 10:08:53 2026: Download complete."))
        self.assertIsNone(
            detect_phase("Thu Sep  3 10:09:39 2026: The database is installed (mpa_v)")
        )

    def test_removing_uncompressed_databases_is_cleanup_not_extraction(self):
        # Both verbs appear in this line; the earlier one describes the work.
        self.assertIs(
            detect_phase(
                "Thu Sep  3 10:08:52 2026: Removing uncompressed databases /db/mpa_v"
            ),
            Phase.CLEANING,
        )

    def test_downloading_and_uncompressing_reports_the_download_first(self):
        self.assertIs(
            detect_phase("Downloading and uncompressing bowtie2 indexes"),
            Phase.DOWNLOADING,
        )

    def test_joining_databases_is_build_work(self):
        self.assertIs(detect_phase("Joining FASTA databases"), Phase.BUILDING)

    def test_the_size_announcement_is_durable_not_a_redraw(self):
        # It carries a number but no rate or countdown, and states a fact worth
        # keeping in the log rather than a position that will be superseded.
        self.assertIsNone(parse_progress("Downloading file of size: 33433.5645 MB"))

    def test_a_metaphlan_redraw_is_recognised_as_progress(self):
        update = parse_progress("1068.22 MB 21.88 %   2.23 MB/sec 28 min 33 sec")
        self.assertIsNotNone(update)
        self.assertAlmostEqual(update.percent, 21.88)

    def test_the_whole_install_reduces_to_a_readable_sequence(self):
        transcript = [
            "Downloading and uncompressing bowtie2 indexes",
            "Downloading file of size: 33433.5645 MB",
            "1068.22 MB 21.88 %   2.23 MB/sec 28 min 33 sec",
            "Checking md5 of /db/mpa_v.tar",
            "Downloading and uncompressing additional files",
            "4881.77 MB 100.00 % 2.10 MB/sec  0 min  0 sec",
            "Checking md5 of /db/mpa_v.tar",
            "Decompressing /db/mpa_v_SGB.fna.bz2 into /db/mpa_v_SGB.fna",
            "Joining FASTA databases",
            "Removing uncompressed databases /db/mpa_v",
            "Download complete.",
            "The database is installed (mpa_vJan25_CHOCOPhlAnSGB_202503)",
        ]
        seen, last = [], None
        for line in transcript:
            if parse_progress(line):
                continue
            phase = detect_phase(line)
            if phase is not None and phase is not last:
                seen.append(phase)
                last = phase
        self.assertEqual(
            seen,
            [
                Phase.DOWNLOADING,
                Phase.VERIFYING,
                Phase.DOWNLOADING,
                Phase.VERIFYING,
                Phase.EXTRACTING,
                Phase.BUILDING,
                Phase.CLEANING,
            ],
            "the install must read as download, verify, extract, then tidy up",
        )


class ElapsedCounterTests(unittest.TestCase):
    """Micromamba redraws a bare elapsed-time counter while it solves.

    Observed on a from-nothing install: 82 of the first 130 lines were "[+] N.Ns"
    and nothing else. They carry no position and no rate, so the stall notice
    already tells the user everything they say.
    """

    def test_a_bare_elapsed_counter_is_a_redraw(self):
        for line in ("[+] 17.0s", "[+] 1.1s", "[+] 0.1s"):
            with self.subTest(line=line):
                self.assertIsNotNone(parse_progress(line))

    def test_a_counter_without_a_position_reports_no_percentage(self):
        update = parse_progress("[+] 17.0s")
        self.assertIsNone(update.percent)

    def test_a_completion_time_is_kept_in_the_log(self):
        # "Done (6.8 sec)" states an outcome; the counter only states liveness.
        self.assertIsNone(
            parse_progress("Fetch Shard Index for conda-forge/noarch  Done (6.8 sec)")
        )

    def test_prose_mentioning_seconds_is_not_swallowed(self):
        self.assertIsNone(parse_progress("Elapsed time: 19 sec was the total"))


class PlanExecutorStreamingTests(unittest.TestCase):
    """How the setup executor reads a tool's pseudo-terminal.

    A read returns whatever bytes have arrived, which can stop in the middle of
    a multi-byte character. The bar glyphs package managers draw progress with
    are three bytes each, so decoding each read on its own left replacement
    marks scattered through the log.
    """

    def setUp(self):
        import tempfile

        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-plan-stream-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def _run(self, body: str):
        import sys

        from backend.setup.executor import PlanExecutor
        from backend.setup.plan import CommandStep, SetupPlan

        tool = self.root / "tool.py"
        tool.write_text(body, encoding="utf-8")
        plan = SetupPlan()
        plan.add(CommandStep(title="probe", program=sys.executable, arguments=(str(tool),)))
        messages, progress = [], []
        succeeded, _message = PlanExecutor(
            plan, on_output=messages.append, on_progress=progress.append
        ).run()
        self.assertTrue(succeeded)
        return messages, progress

    def test_bar_glyphs_survive_a_read_boundary(self):
        messages, _progress = self._run(
            "import sys\n"
            "for i in range(300):\n"
            "    sys.stdout.write('\\rExtracting ' + '\\u2501'*60 + f' {i}%')\n"
            "    sys.stdout.flush()\n"
            "sys.stdout.write('\\nDone extracting\\n')\n"
        )
        joined = "\n".join(messages)
        self.assertNotIn(
            "�", joined, "a character split across two reads was decoded twice"
        )
        self.assertIn("Done extracting", joined)

    def test_thousands_of_redraws_do_not_flood_the_log(self):
        messages, progress = self._run(
            "import sys\n"
            "for i in range(2000):\n"
            "    sys.stdout.write(f'\\r{i} MB  {i/20:.2f} %   2.2 MB/sec 10 min 00 sec')\n"
            "    sys.stdout.flush()\n"
            "sys.stdout.write('\\nFinished\\n')\n"
        )
        self.assertLess(
            len(messages), 60, "progress redraws reached the log instead of the indicator"
        )
        self.assertIn("Finished", "\n".join(messages))

    def test_output_that_never_ends_a_line_is_still_shown(self):
        messages, _progress = self._run("import sys\nsys.stdout.write('X' * 40000)\n")
        self.assertTrue(
            any(message.startswith("X") for message in messages),
            "an unterminated run produced no output at all",
        )
