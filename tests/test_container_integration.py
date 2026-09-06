"""The container backend against the real machine: real databases, real Podman.

Everything else about the backend is checked without executing anything. These
run it - mounting the actual GRCh38 index and the actual MetaPhlAn database
read-only, cancelling a container that is genuinely running, and confirming that
what comes back belongs to the person who asked for it.

Every one of these skips cleanly where a runtime, the image or a database is
absent, so this suite still runs on a machine that has none of them. Nothing
here writes to a database: the point of mounting them read-only is that it
cannot, and one of the tests proves the mount really is read-only.
"""

from pathlib import Path
import gzip
import os
import shutil
import subprocess
import tempfile
import time
import unittest

import support  # noqa: F401  (puts app/ on the path)

from backend.config import get_config  # noqa: E402
from backend.execution.container import ContainerResolver  # noqa: E402
from backend.execution.stage import RunContext, RunOptions  # noqa: E402
from backend.execution.workspace import Workspace  # noqa: E402
from backend.setup.manager import SetupManager  # noqa: E402
from backend.setup.registry import database_specs  # noqa: E402


def container_ready() -> bool:
    made = ContainerResolver()
    return made.runtime is not None and made.image_present()


def grch38_prefix() -> Path | None:
    resolved = get_config().resolve_grch38_index()
    return resolved.prefix if resolved.prefix else None


def metaphlan_database() -> Path | None:
    config = get_config()
    manager = SetupManager(config)
    specification = database_specs()["metaphlan_chocophlan"]
    if manager.database_installed(specification):
        return config.metaphlan_database_directory
    return None


class ContainerIntegrationTestCase(unittest.TestCase):
    """Work in a directory under $HOME, never under /tmp.

    The container is given a writable tmpfs at /tmp, and with identity mounts
    that tmpfs hides anything mounted beneath it: the mount succeeds and the
    directory is empty. A working directory under /tmp would make every test
    here fail for a reason that has nothing to do with what it is testing.
    """

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(
            prefix="bioflow-integration-", dir=Path.home()
        )
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        (self.root / "reads").mkdir()
        (self.root / "results").mkdir()
        self.reads = self.root / "reads" / "sample.fastq.gz"
        with gzip.open(self.reads, "wt") as handle:
            for index in range(400):
                handle.write(f"@r{index}\n{'ACGT' * 30}\n+\n{'I' * 120}\n")

    def context(self, **fields):
        return RunContext(
            workspace=Workspace(self.root / "results"),
            options=RunOptions(threads=2),
            memory_limit_bytes=14 * 1024 ** 3,
            **fields,
        )

    def resolver(self, **fields):
        return ContainerResolver(context=self.context(**fields))

    def run_resolved(self, environment, command, timeout=900, **fields):
        program, arguments = self.resolver(**fields).resolve(environment, command)
        return subprocess.run(
            [program, *arguments], capture_output=True, text=True, timeout=timeout
        )


@unittest.skipUnless(container_ready(), "no container runtime or image")
class DatabaseMountTests(ContainerIntegrationTestCase):
    """Reference data is mounted from this machine, never carried in the image."""

    @unittest.skipUnless(grch38_prefix(), "no GRCh38 index on this machine")
    def test_bowtie2_aligns_against_the_real_mounted_index(self):
        prefix = grch38_prefix()
        output = self.root / "results" / "nohost.fastq.gz"
        finished = self.run_resolved(
            "hostrem",
            ["bowtie2", "--very-sensitive", "-p", "2", "-x", str(prefix),
             "-U", str(self.reads), "--un-gz", str(output), "-S", "/dev/null"],
            host_index_prefix=prefix,
        )
        self.assertEqual(finished.returncode, 0, finished.stderr[-1500:])
        # The summary line validation depends on, proving reads were processed.
        self.assertIn("overall alignment rate", finished.stderr)
        self.assertTrue(output.exists(), "no unaligned reads were written")
        with gzip.open(output, "rb") as handle:
            self.assertTrue(handle.read(1), "the output is an empty stream")

    @unittest.skipUnless(grch38_prefix(), "no GRCh38 index on this machine")
    def test_the_index_mount_is_read_only(self):
        prefix = grch38_prefix()
        probe = prefix.parent / "bioflow-write-probe"
        finished = self.run_resolved(
            "hostrem", ["python", "-c", f"open({str(probe)!r}, 'w')"],
            host_index_prefix=prefix,
        )
        self.assertNotEqual(finished.returncode, 0, "the GRCh38 mount was writable")
        self.assertFalse(probe.exists(), "a file was created beside the real index")

    @unittest.skipUnless(metaphlan_database(), "no MetaPhlAn database on this machine")
    def test_metaphlan_sees_its_database_through_the_mount(self):
        database = metaphlan_database()
        finished = self.run_resolved(
            "taxonomy",
            ["python", "-c",
             "import sys,glob;"
             f"print(len(glob.glob({str(database)!r} + '/*.bt2l')))"],
            metaphlan_database=database,
        )
        self.assertEqual(finished.returncode, 0, finished.stderr[-800:])
        self.assertGreaterEqual(
            int(finished.stdout.strip()), 6,
            "the MetaPhlAn Bowtie2 index is not visible inside the container",
        )

    @unittest.skipUnless(metaphlan_database(), "no MetaPhlAn database on this machine")
    def test_the_database_mount_is_read_only(self):
        database = metaphlan_database()
        probe = database / "bioflow-write-probe"
        finished = self.run_resolved(
            "taxonomy", ["python", "-c", f"open({str(probe)!r}, 'w')"],
            metaphlan_database=database,
        )
        self.assertNotEqual(finished.returncode, 0, "the MetaPhlAn mount was writable")
        self.assertFalse(probe.exists(), "a file was created in the real database")

    @unittest.skipUnless(metaphlan_database(), "no MetaPhlAn database on this machine")
    def test_the_database_is_not_in_the_image(self):
        # Mounting it is the whole design. If a copy were also inside, the
        # image would be tens of gigabytes and the two backends could disagree.
        made = ContainerResolver()
        finished = subprocess.run(
            [made.runtime, "run", "--rm", "--network=none",
             "--security-opt", "label=disable", made.image,
             "/bin/bash", "-lc", "find / -xdev -name '*.bt2l' 2>/dev/null | wc -l"],
            capture_output=True, text=True, timeout=300,
        )
        self.assertEqual(finished.stdout.strip(), "0")


@unittest.skipUnless(container_ready(), "no container runtime or image")
class OwnershipTests(ContainerIntegrationTestCase):
    def test_every_output_belongs_to_the_invoking_user(self):
        """Results owned by root would need sudo to delete.

        On a desktop that is not a rough edge, it is a broken application.
        """
        results = self.root / "results"
        finished = self.run_resolved(
            "qc", ["fastqc", "--threads", "2", "--outdir", str(results), str(self.reads)]
        )
        self.assertEqual(finished.returncode, 0, finished.stderr[-800:])
        produced = list(results.iterdir())
        self.assertTrue(produced, "nothing was produced")
        for path in produced:
            with self.subTest(path=path.name):
                self.assertEqual(path.stat().st_uid, os.getuid())
                self.assertEqual(path.stat().st_gid, os.getgid())

    def test_outputs_can_be_removed_without_privileges(self):
        results = self.root / "results"
        self.run_resolved(
            "qc", ["fastqc", "--outdir", str(results), str(self.reads)]
        )
        for path in list(results.iterdir()):
            path.unlink()  # must not raise PermissionError
        self.assertEqual(list(results.iterdir()), [])

    def test_the_container_process_is_not_root(self):
        finished = self.run_resolved("qc", ["id", "-u"])
        self.assertNotEqual(finished.stdout.strip(), "0")


@unittest.skipUnless(container_ready(), "no container runtime or image")
class IsolationTests(ContainerIntegrationTestCase):
    def test_there_really_is_no_network(self):
        finished = self.run_resolved(
            "qc", ["python", "-c",
                   "import socket; socket.create_connection(('1.1.1.1', 53), timeout=5)"]
        )
        self.assertNotEqual(finished.returncode, 0, "the container reached the network")

    def test_dns_does_not_resolve_either(self):
        finished = self.run_resolved(
            "qc", ["python", "-c", "import socket; socket.gethostbyname('example.com')"]
        )
        self.assertNotEqual(finished.returncode, 0, "the container resolved a name")

    def test_the_root_filesystem_is_read_only(self):
        finished = self.run_resolved("qc", ["python", "-c", "open('/probe', 'w')"])
        self.assertNotEqual(finished.returncode, 0)

    def test_the_home_directory_is_not_reachable_by_default(self):
        # Only what a command names is mounted. Anything else stays invisible.
        marker = Path.home() / ".bashrc"
        if not marker.exists():
            self.skipTest("no marker file to check against")
        finished = self.run_resolved(
            "qc", ["python", "-c", f"import os;print(os.path.exists({str(marker)!r}))"]
        )
        self.assertEqual(finished.stdout.strip(), "False")

    def test_tmp_is_writable_and_is_not_the_hosts(self):
        finished = self.run_resolved(
            "qc", ["/bin/bash", "-lc", "touch /tmp/probe && ls /tmp | wc -l"]
        )
        self.assertEqual(finished.returncode, 0, finished.stderr[-500:])
        self.assertFalse((Path("/tmp") / "probe").exists(), "the host /tmp was written to")


@unittest.skipUnless(container_ready(), "no container runtime or image")
class CancellationTests(ContainerIntegrationTestCase):
    def running_containers(self, runtime) -> set[str]:
        result = subprocess.run(
            [runtime, "ps", "-q"], capture_output=True, text=True, timeout=60
        )
        return set(result.stdout.split())

    def test_terminating_the_client_stops_the_container(self):
        made = self.resolver()
        made.cidfile_directory = self.root / "cids"
        program, arguments = made.resolve("qc", ["sleep", "600"])
        before = self.running_containers(made.runtime)

        process = subprocess.Popen(
            [program, *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        # Wait for it to actually be running, rather than assuming.
        for _ in range(100):
            if self.running_containers(made.runtime) - before:
                break
            time.sleep(0.2)
        else:
            process.kill()
            self.skipTest("the container never started; nothing to cancel")

        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=30)
        made.cleanup_containers()

        for _ in range(50):
            if not (self.running_containers(made.runtime) - before):
                break
            time.sleep(0.2)
        self.assertEqual(
            self.running_containers(made.runtime) - before, set(),
            "a container outlived the client that started it",
        )

    def test_cleanup_removes_a_container_whose_client_was_killed_outright(self):
        """The case terminate() cannot cover.

        A client killed with SIGKILL cannot stop anything. The recorded id is
        what lets a cancel clean up regardless.
        """
        made = self.resolver()
        made.cidfile_directory = self.root / "cids"
        program, arguments = made.resolve("qc", ["sleep", "600"])
        before = self.running_containers(made.runtime)

        process = subprocess.Popen(
            [program, *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        for _ in range(100):
            if self.running_containers(made.runtime) - before:
                break
            time.sleep(0.2)
        else:
            process.kill()
            self.skipTest("the container never started; nothing to cancel")

        process.kill()
        process.wait(timeout=30)
        removed = made.cleanup_containers()
        self.assertTrue(removed, "cleanup did not report removing anything")

        for _ in range(50):
            if not (self.running_containers(made.runtime) - before):
                break
            time.sleep(0.2)
        self.assertEqual(
            self.running_containers(made.runtime) - before, set(),
            "a stranded container survived cleanup",
        )

    def test_an_ordinary_run_leaves_nothing_behind(self):
        made = self.resolver()
        before = subprocess.run(
            [made.runtime, "ps", "-aq"], capture_output=True, text=True, timeout=60
        ).stdout.split()
        self.run_resolved("qc", ["fastqc", "--version"])
        after = subprocess.run(
            [made.runtime, "ps", "-aq"], capture_output=True, text=True, timeout=60
        ).stdout.split()
        self.assertEqual(set(after) - set(before), set(), "--rm left a container")


@unittest.skipUnless(container_ready(), "no container runtime or image")
class HostStateTests(ContainerIntegrationTestCase):
    """Running a container must not change the machine it runs on."""

    @unittest.skipUnless(shutil.which("getenforce"), "SELinux tools are absent")
    @unittest.skipUnless(metaphlan_database(), "no MetaPhlAn database on this machine")
    def test_mounting_a_database_does_not_relabel_it(self):
        """The reason confinement is dropped rather than relabelled.

        Suffixing a mount with :z or :Z rewrites the host directory's SELinux
        label. Over a ~51 GB database that is slow, and :Z applies a private
        label that would leave it unreadable by the native backend.
        """
        database = metaphlan_database()
        before = subprocess.run(
            ["ls", "-Zd", str(database)], capture_output=True, text=True, timeout=60
        ).stdout.split()[0]
        self.run_resolved(
            "taxonomy", ["python", "-c", "print('ok')"], metaphlan_database=database
        )
        after = subprocess.run(
            ["ls", "-Zd", str(database)], capture_output=True, text=True, timeout=60
        ).stdout.split()[0]
        self.assertEqual(before, after, "the database's SELinux label was rewritten")

    def test_no_mount_argument_ever_asks_for_relabelling(self):
        made = self.resolver(metaphlan_database=self.root / "reads")
        _program, arguments = made.resolve("taxonomy", ["python", "-c", "pass"])
        for argument in arguments:
            if argument.count(":") >= 2 and argument.startswith("/"):
                mode = argument.rsplit(":", 1)[1]
                with self.subTest(mount=argument):
                    self.assertIn(mode, ("ro", "rw"))
                    self.assertNotIn("z", mode.lower())


if __name__ == "__main__":
    unittest.main()
