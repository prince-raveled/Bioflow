"""Clean-machine install robustness: disk honesty, checksums, and log noise.

Covers the findings from a fresh-install report: a filesystem that overstates
free space, a reference host whose TLS fails, and tool chatter that reads as an
error. Nothing here touches the network or the user's real configuration.
"""

from pathlib import Path
import os
import tempfile
import unittest
from unittest import mock

import support  # noqa: F401  (puts app/ on the path)
from backend.execution.runner import is_harmless_tool_noise  # noqa: E402
from backend.setup import bootstrap  # noqa: E402
from backend.setup.preflight import (  # noqa: E402
    IMPLAUSIBLE_FREE_BYTES,
    filesystem_type,
    free_space_is_trustworthy,
    running_under_wsl,
)


class DiskHonestyTests(unittest.TestCase):
    """A figure BioFlow cannot stand behind must not be asserted."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-disk-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def test_an_ordinary_local_disk_is_trusted(self):
        # Sized against the actual volume: a figure larger than the filesystem's
        # own capacity is incoherent and is meant to be rejected.
        import shutil

        plausible = shutil.disk_usage(self.root).total // 4
        self.assertTrue(free_space_is_trustworthy(plausible, self.root))

    def test_more_free_space_than_capacity_is_rejected(self):
        import shutil

        impossible = shutil.disk_usage(self.root).total * 2
        self.assertFalse(free_space_is_trustworthy(impossible, self.root))

    def test_an_implausibly_large_figure_is_not_trusted(self):
        self.assertFalse(free_space_is_trustworthy(IMPLAUSIBLE_FREE_BYTES, self.root))

    def test_wsl_is_not_trusted_even_for_a_plausible_figure(self):
        # The reported case: 950 GB claimed against a ~97 GB backing disk.
        with mock.patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu"}):
            self.assertTrue(running_under_wsl())
            self.assertFalse(free_space_is_trustworthy(950 * 1024 ** 3, self.root))

    def test_a_network_filesystem_is_not_trusted(self):
        with mock.patch("backend.setup.preflight.filesystem_type", return_value="nfs4"):
            self.assertFalse(free_space_is_trustworthy(1024 ** 3, self.root))

    def test_a_passthrough_filesystem_is_not_trusted(self):
        for kind in ("9p", "drvfs", "virtiofs", "cifs", "overlay"):
            with self.subTest(filesystem=kind):
                with mock.patch("backend.setup.preflight.filesystem_type", return_value=kind):
                    self.assertFalse(free_space_is_trustworthy(1024 ** 3, self.root))

    def test_filesystem_type_resolves_a_real_path(self):
        # Should name something rather than raise, whatever the machine.
        self.assertIsInstance(filesystem_type(self.root), str)

    def test_the_preflight_says_so_rather_than_asserting_a_number(self):
        from backend.config import BioFlowConfig, DEFAULT_ENVIRONMENT_NAMES
        from backend.setup.preflight import run_preflight

        config = BioFlowConfig(
            data_root=self.root,
            database_root=self.root / "databases",
            environment_names=dict(DEFAULT_ENVIRONMENT_NAMES),
        )
        with mock.patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu"}):
            disk = [c for c in run_preflight(config, 6 * 1024 ** 3) if c.name == "Disk space"][0]
        self.assertIn("could not confirm", disk.detail)
        self.assertTrue(disk.blocking, "the check must remain a gate")


class ReferenceSourceTests(unittest.TestCase):
    """The reference download survives one transport failing."""

    def test_both_sources_point_at_the_same_file(self):
        paths = {url.split("://", 1)[1] for url in bootstrap.GRCH38_SOURCES}
        self.assertEqual(len(paths), 1, "a fallback must not change which file is fetched")

    def test_the_fallback_is_a_different_scheme(self):
        schemes = [url.split("://", 1)[0] for url in bootstrap.GRCH38_SOURCES]
        self.assertEqual(schemes, ["https", "http"])

    def test_the_checksum_is_read_from_the_release_not_pinned(self):
        source = Path(bootstrap.__file__).read_text(encoding="utf-8")
        self.assertIn("MD5SUMS", source)
        self.assertNotIn("13ad5c68bcd3e940c3cbcb050628b48e", source,
                         "a pinned checksum would reject a correct file after a re-release")

    def test_published_md5_parses_a_release_listing(self):
        listing = (
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  gencode.v43.annotation.gtf.gz\n"
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb  GRCh38.primary_assembly.genome.fa.gz\n"
        )
        with mock.patch("urllib.request.urlopen", mock.mock_open(read_data=listing.encode())):
            found = bootstrap.published_md5("GRCh38.primary_assembly.genome.fa.gz", lambda _m: None)
        self.assertEqual(found, "b" * 32)

    def test_a_missing_checksum_listing_is_tolerated(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("no network")):
            self.assertIsNone(bootstrap.published_md5("anything.gz", lambda _m: None))

    def test_file_md5_matches_hashlib(self):
        import hashlib

        target = Path(tempfile.mkdtemp()) / "payload.bin"
        target.write_bytes(b"bioflow" * 5000)
        self.addCleanup(lambda: target.unlink(missing_ok=True))
        self.assertEqual(bootstrap.file_md5(target), hashlib.md5(target.read_bytes()).hexdigest())


class ToolNoiseTests(unittest.TestCase):
    """The bowtie2 dispatch notice is hidden from users, everywhere."""

    def test_the_bowtie2_dispatch_notice_is_filtered(self):
        self.assertTrue(
            is_harmless_tool_noise("[WARNING] Failed to launch x86-64-v3 version, staying with default")
        )

    def test_surrounding_whitespace_does_not_defeat_the_filter(self):
        self.assertTrue(
            is_harmless_tool_noise("   [WARNING] Failed to launch x86-64-v3 version, staying with default  ")
        )

    def test_real_problems_are_never_filtered(self):
        for line in (
            "Error: could not open index",
            "[WARNING] reads file is empty",
            "(ERR): bowtie2-align exited with value 1",
            "sh: -c: line 1: syntax error near unexpected token `('",
        ):
            with self.subTest(line=line):
                self.assertFalse(is_harmless_tool_noise(line))

    def test_both_consumers_share_one_definition(self):
        from gui.pages.qc_tool_page import QCToolPage

        noisy = "[WARNING] Failed to launch x86-64-v3 version, staying with default"
        cleaned = QCToolPage._clean_tool_output(f"before\n{noisy}\nafter")
        self.assertEqual(cleaned, "before\nafter")


if __name__ == "__main__":
    unittest.main()


class DatabaseSizingTests(unittest.TestCase):
    """Transfer size and disk footprint are different questions.

    Both figures were wrong at different times and in opposite directions, so
    they are pinned to a measured install here. MetaPhlAn pulls two archives
    totalling 37.5 GB and leaves 50.5 GB on disk once the marker bundle's
    .bz2 payloads are decompressed in place.
    """

    def setUp(self):
        from backend.setup.registry import database_specs

        self.specs = database_specs()

    def test_metaphlan_sizes_match_what_the_installer_actually_does(self):
        # Measured from a completed install: `metaphlan --install` fetches the
        # index tar (33,433 MB) and then the marker bundle (4,886 MB), deleting
        # each archive once unpacked. An earlier guess put the transfer at 5 GB
        # by probing the wrong URL; this pins the figures to observed behaviour.
        spec = self.specs["metaphlan_chocophlan"]
        self.assertGreaterEqual(spec.transfer_size(), 30 * 1024 ** 3,
                                "the index tar is a ~33 GB download, not a small bundle")
        self.assertGreater(spec.peak_disk(), spec.transfer_size(),
                           "the archive and its contents coexist while unpacking")

    def test_grch38_transfer_is_the_assembly_not_the_index(self):
        spec = self.specs["grch38"]
        # The index is built locally, so it must not be counted as a download.
        self.assertLess(spec.transfer_size(), 2 * 1024 ** 3)
        self.assertGreater(spec.approximate_bytes, spec.transfer_size())

    def test_peak_disk_covers_archive_and_contents_together(self):
        for key, spec in self.specs.items():
            with self.subTest(database=key):
                self.assertGreaterEqual(spec.peak_disk(), spec.approximate_bytes)

    def test_transfer_size_defaults_to_the_footprint_when_unmeasured(self):
        from backend.setup.registry import DatabaseSpec

        spec = DatabaseSpec(
            key="x", title="x", description="x", environment_key="qc",
            directory_parts=("x",), marker_globs=("*",), approximate_bytes=1000,
        )
        self.assertEqual(spec.transfer_size(), 1000)
        self.assertEqual(spec.peak_disk(), 2000)

    def test_the_preflight_gates_on_peak_not_on_the_footprint(self):
        from backend.setup.manager import SetupManager
        from support import isolate_bioflow_data_directory

        # The estimators report what remains to be fetched, so on a machine
        # where this database is already installed both figures are legitimately
        # zero. Point at an empty data directory to ask the question the test
        # means to ask.
        isolate_bioflow_data_directory(self)
        manager = SetupManager()
        selection = ["db:metaphlan_chocophlan"]
        self.assertGreater(
            manager.estimated_peak_bytes(selection),
            manager.estimated_download_bytes(selection),
            "peak disk must exceed the transfer for a bundle that unpacks",
        )


class MemoryRequirementTests(unittest.TestCase):
    """Disk and memory are independent ways for an install to be unusable.

    A machine can have room for all 50 GB of the MetaPhlAn database and still
    be unable to align against it. That happened here: the kernel killed
    bowtie2-align-l at 9.8 GB resident while the generic 8 GB check passed.
    """

    def test_the_requirement_comes_from_the_selection(self):
        from backend.setup.manager import SetupManager

        manager = SetupManager()
        self.assertEqual(manager.required_memory_bytes([]), 0)
        self.assertGreaterEqual(
            manager.required_memory_bytes(["db:metaphlan_chocophlan"]),
            10 * 1024 ** 3,
        )

    def test_components_run_one_at_a_time_so_the_maximum_governs(self):
        from backend.setup.manager import SetupManager

        manager = SetupManager()
        every = ["db:" + key for key in database_specs_keys()]
        self.assertEqual(
            manager.required_memory_bytes(every),
            max(manager.required_memory_bytes(["db:" + k]) for k in database_specs_keys()),
            "summing would demand memory no single stage ever needs at once",
        )

    def test_a_small_machine_is_told_which_component_it_cannot_run(self):
        from backend.setup import preflight
        from backend.setup.manager import SetupManager

        manager = SetupManager()
        needed = manager.required_memory_bytes(["db:metaphlan_chocophlan"])
        with mock.patch.object(preflight, "total_memory_bytes", return_value=8 * 1024 ** 3):
            checks = preflight.run_preflight(
                manager.config, 0, required_memory_bytes=needed
            )
        memory = [c for c in checks if c.name == "Memory"][0]
        self.assertFalse(memory.passed)
        self.assertIn("10.0 GB", memory.detail)

    def test_an_unreadable_meminfo_does_not_fail_the_check(self):
        from backend.setup import preflight

        with mock.patch.object(Path, "read_text", side_effect=OSError("nope")):
            self.assertEqual(preflight.available_memory_bytes(), 0)


class KilledCommandTests(unittest.TestCase):
    """A signalled process prints nothing, so the status is the only evidence."""

    def test_an_oom_kill_is_explained_rather_than_numbered(self):
        from backend.execution.runner import describe_exit

        message = describe_exit(-9)
        self.assertIn("SIGKILL", message)
        self.assertIn("memory", message.lower())
        self.assertNotEqual(message, "Command failed with exit code -9.")

    def test_ordinary_failures_keep_their_exit_code(self):
        from backend.execution.runner import describe_exit

        self.assertEqual(describe_exit(1), "Command failed with exit code 1.")

    def test_an_absent_status_is_not_reported_as_a_signal(self):
        from backend.execution.runner import describe_exit

        self.assertNotIn("killed", describe_exit(None).lower())


class ReclaimSafetyTests(unittest.TestCase):
    """Reclaiming space must not delete the data it was meant to keep.

    HUMAnN reads its ChocoPhlAn sequences straight out of *.ffn.gz, so a
    compressed file here is as likely to be the database as a spent archive.
    """

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-reclaim-")
        self.directory = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def _write(self, name, size=1024):
        path = self.directory / name
        path.write_bytes(b"x" * size)
        return path

    def test_a_spent_archive_is_removed(self):
        from backend.setup import bootstrap

        archive = self._write("markers.tar")
        bootstrap.reclaim_archives(self.directory, lambda _m: None)
        self.assertFalse(archive.exists())

    def test_a_compressed_database_file_is_not_an_archive(self):
        from backend.setup import bootstrap

        database = self._write("g__Bacteria.centroids.ffn.gz")
        bootstrap.reclaim_archives(self.directory, lambda _m: None)
        self.assertTrue(
            database.exists(),
            "deleting *.ffn.gz would destroy the HUMAnN nucleotide database",
        )

    def test_required_files_survive_even_with_an_archive_suffix(self):
        from backend.setup import bootstrap

        required = self._write("chocophlan.tar")
        bootstrap.reclaim_archives(
            self.directory, lambda _m: None, keep=("chocophlan.tar",)
        )
        self.assertTrue(required.exists())

    def test_a_partial_download_is_cleared(self):
        from backend.setup import bootstrap

        partial = self._write("index.tar.part")
        freed = bootstrap.reclaim_archives(self.directory, lambda _m: None)
        self.assertFalse(partial.exists())
        self.assertEqual(freed, 1024)


def database_specs_keys():
    from backend.setup.registry import database_specs

    return list(database_specs())


class SwapHeadroomTests(unittest.TestCase):
    """RAM alone does not decide whether a large component survives.

    A 15 GB machine passed the memory check and then had MetaPhlAn killed at
    6.8 GB, twice. The same machine, same command, completed once a 16 GB swap
    file was added. The difference was never visible to BioFlow, because nothing
    looked at swap - and the 8 GB of zram already present did not help, since it
    is compressed RAM and a marker table compresses poorly.
    """

    GIGABYTE = 1024 ** 3

    def _swap_check(self, ram_gb, swap_gb, zram_gb, required_gb=10):
        from backend.setup import preflight
        from backend.setup.manager import SetupManager

        manager = SetupManager()
        with mock.patch.object(preflight, "total_memory_bytes",
                               return_value=ram_gb * self.GIGABYTE), \
             mock.patch.object(preflight, "swap_bytes",
                               return_value=(swap_gb * self.GIGABYTE,
                                             zram_gb * self.GIGABYTE)):
            checks = preflight.run_preflight(
                manager.config, 0, required_memory_bytes=required_gb * self.GIGABYTE
            )
        return next(check for check in checks if check.name == "Swap")

    def test_zram_alone_does_not_count_as_headroom(self):
        # The configuration that failed.
        check = self._swap_check(ram_gb=15, swap_gb=8, zram_gb=8)
        self.assertFalse(check.passed)
        self.assertIn("zram", check.detail)

    def test_a_disk_swapfile_covers_the_requirement(self):
        # The configuration that worked: same zram, plus 16 GB on disk.
        check = self._swap_check(ram_gb=15, swap_gb=24, zram_gb=8)
        self.assertTrue(check.passed)

    def test_no_swap_at_all_is_reported(self):
        check = self._swap_check(ram_gb=15, swap_gb=0, zram_gb=0)
        self.assertFalse(check.passed)
        self.assertIn("swap file", check.detail)

    def test_a_large_machine_is_not_asked_for_swap(self):
        # Plenty of RAM to spare after the component takes its share.
        for ram in (32, 64, 128):
            with self.subTest(ram=ram):
                self.assertTrue(self._swap_check(ram_gb=ram, swap_gb=0, zram_gb=0).passed)

    def test_the_advice_names_a_size(self):
        check = self._swap_check(ram_gb=15, swap_gb=0, zram_gb=0)
        self.assertIn("10.0 GB", check.detail)

    def test_swap_is_not_checked_when_nothing_large_is_selected(self):
        from backend.setup import preflight
        from backend.setup.manager import SetupManager

        names = [c.name for c in preflight.run_preflight(SetupManager().config, 0)]
        self.assertNotIn("Swap", names, "nothing selected needs the warning")

    def test_zram_is_told_apart_from_disk_swap(self):
        from backend.setup.preflight import swap_bytes

        total, compressed = swap_bytes()
        self.assertGreaterEqual(total, compressed)
        self.assertGreaterEqual(compressed, 0)


class SwapInstructionsTests(unittest.TestCase):
    """Telling a user to "add swap" is not enough to act on.

    The commands differ by filesystem, and a btrfs swap file cannot be made with
    fallocate at all - it has to be nocow, uncompressed and unsnapshotted, which
    only btrfs' own tool arranges. The persistence line matters just as much: a
    swap file added by hand vanishes at the next reboot, and that is exactly how
    a machine that had run this workflow successfully stopped being able to.
    """

    GIGABYTE = 1024 ** 3

    def _instructions(self, filesystem):
        from backend.setup import preflight

        with mock.patch.object(preflight, "filesystem_type", return_value=filesystem):
            return preflight.swap_file_instructions(10 * self.GIGABYTE, Path("/"))

    def test_btrfs_uses_its_own_tool(self):
        text = self._instructions("btrfs")
        self.assertIn("btrfs filesystem mkswapfile", text)
        self.assertNotIn("fallocate", text)

    def test_other_filesystems_use_fallocate_and_mkswap(self):
        for filesystem in ("ext4", "xfs", ""):
            with self.subTest(filesystem=filesystem):
                text = self._instructions(filesystem)
                self.assertIn("fallocate", text)
                self.assertIn("mkswap", text)
                self.assertNotIn("btrfs", text)

    def test_the_file_is_made_private(self):
        # mkswap refuses a world-readable file, and it would leak memory contents.
        self.assertIn("chmod 600", self._instructions("ext4"))

    def test_it_is_made_to_survive_a_reboot(self):
        for filesystem in ("btrfs", "ext4"):
            with self.subTest(filesystem=filesystem):
                self.assertIn("/etc/fstab", self._instructions(filesystem))

    def test_the_size_is_rounded_up_to_whole_gigabytes(self):
        from backend.setup import preflight

        with mock.patch.object(preflight, "filesystem_type", return_value="ext4"):
            text = preflight.swap_file_instructions(
                int(9.3 * self.GIGABYTE), Path("/")
            )
        self.assertIn("10G", text, "a short swap file would not cover the requirement")

    def test_the_advice_reaches_the_failing_check(self):
        from backend.setup import preflight
        from backend.setup.manager import SetupManager

        manager = SetupManager()
        with mock.patch.object(preflight, "total_memory_bytes", return_value=15 * self.GIGABYTE), \
             mock.patch.object(preflight, "swap_bytes",
                               return_value=(8 * self.GIGABYTE, 8 * self.GIGABYTE)):
            check = next(
                c for c in preflight.run_preflight(
                    manager.config, 0, required_memory_bytes=10 * self.GIGABYTE
                ) if c.name == "Swap"
            )
        self.assertFalse(check.passed)
        self.assertIn("swapon", check.detail)
        self.assertIn("/etc/fstab", check.detail)


class HostAssumptionsTests(unittest.TestCase):
    """Everything read from the host must degrade rather than crash.

    A container can restrict /proc, and none of these readings is important
    enough to stop an install over.
    """

    def test_every_proc_reading_survives_an_unreadable_proc(self):
        from backend.execution.stages import taxonomy
        from backend.setup import preflight
        from gui import dialogs

        with mock.patch.object(Path, "read_text", side_effect=OSError("restricted")):
            self.assertEqual(preflight.swap_bytes(), (0, 0))
            self.assertFalse(preflight.running_under_wsl())
            self.assertEqual(preflight.filesystem_type(Path("/")), "")
            self.assertEqual(taxonomy.available_memory_bytes(), 0)
            self.assertEqual(taxonomy.out_of_memory_penalty(), 0)
            self.assertFalse(dialogs.running_under_wsl())

    def test_unknown_memory_chooses_the_survivable_path(self):
        from backend.execution.stages.taxonomy import should_memory_map

        # Slow and finishing beats fast and killed.
        self.assertTrue(should_memory_map(0))
