"""What this process may use, as opposed to what the machine has fitted.

BioFlow's memory policy was measured on bare metal, where `/proc/meminfo`
answers both "how much memory is there" and "how much may I use". A cgroup
separates the two: the kernel keeps reporting the host's memory to a process
confined to a fraction of it. A policy that believes the larger number decides
to load a 33 GB index into a cgroup that will kill it for trying, which is the
same death the memory-mapping threshold exists to prevent.

Every ceiling here must also hold still. `should_memory_map()` decides part of
MetaPhlAn's command line and therefore part of the checkpoint fingerprint, so a
reading that drifted between two stages of one run would rewrite the command and
invalidate results that are still good.
"""

from pathlib import Path
from unittest import mock
import os
import tempfile
import unittest

import support  # noqa: F401  (puts app/ on the path)

from backend import resources  # noqa: E402


GIGABYTE = 1024 ** 3
#: cgroup v1 spells "no limit" as the largest page-aligned counter value.
PAGE_COUNTER_MAX = 9223372036854771712


class _Hierarchy:
    """A synthetic /sys/fs/cgroup and /proc, so no real limits are involved."""

    def __init__(self, root: Path):
        self.root = root
        self.cgroup = root / "cgroup"
        self.cgroup.mkdir(parents=True, exist_ok=True)
        self.meminfo = root / "meminfo"
        self.self_cgroup = root / "self_cgroup"
        self.set_installed_memory(16 * GIGABYTE)
        self.set_own_path("/")

    def set_installed_memory(self, total: int) -> None:
        self.meminfo.write_text(
            f"MemTotal:       {total // 1024} kB\n"
            f"MemAvailable:   {total // 2048} kB\n",
            encoding="utf-8",
        )

    def set_own_path(self, path: str) -> None:
        self.self_cgroup.write_text(f"0::{path}\n", encoding="utf-8")

    def write(self, relative: str, name: str, value: str) -> None:
        directory = self.cgroup / relative.strip("/") if relative.strip("/") else self.cgroup
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(f"{value}\n", encoding="utf-8")

    def patched(self):
        return mock.patch.multiple(
            resources,
            CGROUP_ROOT=self.cgroup,
            PROC_MEMINFO=self.meminfo,
            PROC_SELF_CGROUP=self.self_cgroup,
        )


class _HierarchyTestCase(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-cgroup-")
        self.addCleanup(self._temporary.cleanup)
        self.fs = _Hierarchy(Path(self._temporary.name))


class MemoryCeilingTests(_HierarchyTestCase):
    def test_no_cgroup_limit_reports_installed_memory(self):
        # The ordinary desktop run. This is the case that must keep behaving
        # exactly as it did before cgroups were consulted at all.
        self.fs.set_installed_memory(14 * GIGABYTE)
        with self.fs.patched():
            self.assertEqual(resources.memory_limit_bytes(), 14 * GIGABYTE)

    def test_cgroup_v2_max_means_unlimited(self):
        self.fs.write("/", "memory.max", "max")
        self.fs.set_installed_memory(64 * GIGABYTE)
        with self.fs.patched():
            self.assertEqual(resources.cgroup_memory_limit_bytes(), 0)
            self.assertEqual(resources.memory_limit_bytes(), 64 * GIGABYTE)

    def test_cgroup_v2_limit_wins_over_installed_memory(self):
        # The container case the audit flagged: a host with 64 GB, a container
        # allowed 8. Believing /proc/meminfo here is what gets MetaPhlAn killed.
        self.fs.set_installed_memory(64 * GIGABYTE)
        self.fs.write("/", "memory.max", str(8 * GIGABYTE))
        with self.fs.patched():
            self.assertEqual(resources.memory_limit_bytes(), 8 * GIGABYTE)

    def test_the_tightest_ancestor_limit_applies(self):
        # A limit on an ancestor binds a descendant that sets none of its own,
        # so the effective ceiling is the smallest on the chain, not the nearest.
        self.fs.set_own_path("/user.slice/app.scope")
        self.fs.write("/", "memory.max", "max")
        self.fs.write("/user.slice", "memory.max", str(4 * GIGABYTE))
        self.fs.write("/user.slice/app.scope", "memory.max", str(12 * GIGABYTE))
        with self.fs.patched():
            self.assertEqual(resources.memory_limit_bytes(), 4 * GIGABYTE)

    def test_cgroup_v1_limit_is_read(self):
        self.fs.set_installed_memory(64 * GIGABYTE)
        self.fs.write("/memory", "memory.limit_in_bytes", str(6 * GIGABYTE))
        with self.fs.patched():
            self.assertEqual(resources.memory_limit_bytes(), 6 * GIGABYTE)

    def test_cgroup_v1_sentinel_is_not_a_limit(self):
        # v1 has no word for "unlimited"; it writes the largest value the
        # counter holds. Treating that as a ceiling would report 8 exabytes.
        self.fs.set_installed_memory(16 * GIGABYTE)
        self.fs.write("/memory", "memory.limit_in_bytes", str(PAGE_COUNTER_MAX))
        with self.fs.patched():
            self.assertEqual(resources.cgroup_memory_limit_bytes(), 0)
            self.assertEqual(resources.memory_limit_bytes(), 16 * GIGABYTE)

    def test_v2_takes_precedence_over_a_stale_v1_file(self):
        self.fs.set_installed_memory(64 * GIGABYTE)
        self.fs.write("/", "memory.max", str(8 * GIGABYTE))
        self.fs.write("/memory", "memory.limit_in_bytes", str(2 * GIGABYTE))
        with self.fs.patched():
            self.assertEqual(resources.memory_limit_bytes(), 8 * GIGABYTE)

    def test_unreadable_everything_reports_zero(self):
        # Zero is the "unknown" signal should_memory_map() already treats as a
        # reason to take the cautious path.
        missing = Path(self._temporary.name) / "absent"
        with mock.patch.multiple(
            resources,
            CGROUP_ROOT=missing,
            PROC_MEMINFO=missing / "meminfo",
            PROC_SELF_CGROUP=missing / "cgroup",
        ):
            self.assertEqual(resources.memory_limit_bytes(), 0)

    def test_a_corrupt_limit_file_is_ignored_rather_than_fatal(self):
        self.fs.set_installed_memory(16 * GIGABYTE)
        self.fs.write("/", "memory.max", "not-a-number")
        with self.fs.patched():
            self.assertEqual(resources.memory_limit_bytes(), 16 * GIGABYTE)

    def test_the_ceiling_does_not_move_between_reads(self):
        # It is part of the command and therefore of the fingerprint.
        self.fs.write("/", "memory.max", str(8 * GIGABYTE))
        with self.fs.patched():
            readings = {resources.memory_limit_bytes() for _ in range(5)}
        self.assertEqual(len(readings), 1)


class AvailableMemoryTests(_HierarchyTestCase):
    """Free memory now, as opposed to the ceiling.

    This is the one figure here that is allowed to move, because it answers a
    question about the present. It feeds warnings only, never the memory-mapping
    decision, which is part of the command and therefore of the fingerprint.
    """

    def test_without_a_cgroup_it_reports_what_the_kernel_says(self):
        with self.fs.patched():
            self.assertEqual(
                resources.available_memory_bytes(),
                resources.meminfo_bytes("MemAvailable"),
            )

    def test_inside_a_cgroup_the_tighter_figure_wins(self):
        # The host has plenty free; the container has almost none. Reporting
        # the host's figure is what lets a run start that cannot finish.
        self.fs.set_installed_memory(64 * GIGABYTE)
        self.fs.write("/", "memory.max", str(8 * GIGABYTE))
        self.fs.write("/", "memory.current", str(7 * GIGABYTE))
        with self.fs.patched():
            self.assertEqual(resources.available_memory_bytes(), 1 * GIGABYTE)

    def test_a_roomy_cgroup_does_not_inflate_the_host_figure(self):
        self.fs.set_installed_memory(16 * GIGABYTE)
        self.fs.write("/", "memory.max", str(64 * GIGABYTE))
        self.fs.write("/", "memory.current", str(1 * GIGABYTE))
        with self.fs.patched():
            self.assertEqual(
                resources.available_memory_bytes(),
                resources.meminfo_bytes("MemAvailable"),
            )

    def test_headroom_never_goes_negative(self):
        self.fs.write("/", "memory.max", str(4 * GIGABYTE))
        self.fs.write("/", "memory.current", str(6 * GIGABYTE))
        with self.fs.patched():
            self.assertEqual(resources.cgroup_memory_headroom_bytes(), 0)

    def test_no_cgroup_means_no_headroom_figure(self):
        with self.fs.patched():
            self.assertEqual(resources.cgroup_memory_headroom_bytes(), 0)

    def test_it_does_not_feed_the_mapping_decision(self):
        # The ceiling must be indifferent to how much is free right now.
        self.fs.set_installed_memory(64 * GIGABYTE)
        with self.fs.patched():
            self.fs.write("/", "memory.current", str(63 * GIGABYTE))
            first = resources.memory_limit_bytes()
            self.fs.write("/", "memory.current", str(1 * GIGABYTE))
            self.assertEqual(resources.memory_limit_bytes(), first)


class SingleSourceTests(unittest.TestCase):
    """Every resource reading comes from one place.

    Preflight and the taxonomy stage each used to carry their own copy of these
    - two implementations of "how much memory is there", two of "how much is
    free", and os.cpu_count() in two more places. None of them saw a cgroup, and
    two of them could disagree.
    """

    def test_the_taxonomy_stage_delegates(self):
        from backend.execution.stages import taxonomy

        self.assertEqual(taxonomy.total_memory_bytes(), resources.memory_limit_bytes())
        self.assertEqual(
            taxonomy.available_memory_bytes(), resources.available_memory_bytes()
        )

    def test_preflight_delegates(self):
        from backend.setup import preflight

        self.assertEqual(preflight.total_memory_bytes(), resources.memory_limit_bytes())

    def test_preflight_and_the_stage_agree(self):
        from backend.execution.stages import taxonomy
        from backend.setup import preflight

        self.assertEqual(preflight.total_memory_bytes(), taxonomy.total_memory_bytes())


class CpuCeilingTests(_HierarchyTestCase):
    def test_no_quota_falls_back_to_affinity(self):
        with self.fs.patched():
            self.assertEqual(resources.cgroup_cpu_quota(), 0)
            with mock.patch.object(os, "sched_getaffinity", return_value={0, 1, 2, 3}):
                self.assertEqual(resources.usable_cpus(), 4)

    def test_cgroup_v2_quota_is_read(self):
        self.fs.write("/", "cpu.max", "200000 100000")
        with self.fs.patched():
            self.assertEqual(resources.cgroup_cpu_quota(), 2)

    def test_cgroup_v2_max_means_unlimited(self):
        self.fs.write("/", "cpu.max", "max 100000")
        with self.fs.patched():
            self.assertEqual(resources.cgroup_cpu_quota(), 0)

    def test_a_fractional_quota_rounds_down(self):
        # Half a CPU cannot run a second thread at speed, and overestimating
        # threads is what collapsed the page cache, not underestimating them.
        self.fs.write("/", "cpu.max", "150000 100000")
        with self.fs.patched():
            self.assertEqual(resources.cgroup_cpu_quota(), 1)

    def test_a_sub_single_quota_still_reports_one(self):
        self.fs.write("/", "cpu.max", "50000 100000")
        with self.fs.patched():
            self.assertEqual(resources.cgroup_cpu_quota(), 1)
            self.assertGreaterEqual(resources.usable_cpus(), 1)

    def test_cgroup_v1_quota_is_read(self):
        self.fs.write("/cpu", "cpu.cfs_quota_us", "400000")
        self.fs.write("/cpu", "cpu.cfs_period_us", "100000")
        with self.fs.patched():
            self.assertEqual(resources.cgroup_cpu_quota(), 4)

    def test_cgroup_v1_negative_quota_means_unlimited(self):
        self.fs.write("/cpu", "cpu.cfs_quota_us", "-1")
        self.fs.write("/cpu", "cpu.cfs_period_us", "100000")
        with self.fs.patched():
            self.assertEqual(resources.cgroup_cpu_quota(), 0)

    def test_the_quota_caps_affinity(self):
        self.fs.write("/", "cpu.max", "200000 100000")
        with self.fs.patched(), \
             mock.patch.object(os, "sched_getaffinity", return_value=set(range(16))):
            self.assertEqual(resources.usable_cpus(), 2)

    def test_affinity_caps_a_generous_quota(self):
        # A cpuset or `taskset` narrows what the process may schedule on even
        # where the quota is wide. os.cpu_count() sees neither.
        self.fs.write("/", "cpu.max", "1600000 100000")
        with self.fs.patched(), \
             mock.patch.object(os, "sched_getaffinity", return_value={0, 1}):
            self.assertEqual(resources.usable_cpus(), 2)

    def test_never_reports_fewer_than_one(self):
        with self.fs.patched(), \
             mock.patch.object(os, "sched_getaffinity", return_value=set()):
            self.assertGreaterEqual(resources.usable_cpus(), 1)


class RealMachineTests(unittest.TestCase):
    """Against the actual host, with nothing patched."""

    def test_it_reports_something_plausible(self):
        memory = resources.memory_limit_bytes()
        self.assertGreater(memory, 0, "this machine's memory could not be read")
        self.assertGreater(resources.usable_cpus(), 0)

    def test_it_never_exceeds_installed_memory(self):
        installed = resources.meminfo_bytes("MemTotal")
        self.assertLessEqual(resources.memory_limit_bytes(), installed)

    def test_repeated_reads_agree(self):
        self.assertEqual(resources.memory_limit_bytes(), resources.memory_limit_bytes())
        self.assertEqual(resources.usable_cpus(), resources.usable_cpus())


if __name__ == "__main__":
    unittest.main()
