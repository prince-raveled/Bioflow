"""Building a container command, and refusing to build a wrong one.

The resolver is a sibling of EnvironmentResolver: same two questions, different
answer. Everything above it is unchanged, so what has to be right is the command
it produces - the mounts it derives, the posture it imposes, and the inner
command, which must stay byte-identical to the native one so that a container
result and a native result can be compared argument by argument.
"""

from pathlib import Path
from unittest import mock
import os
import subprocess
import tempfile
import unittest

import support  # noqa: F401  (puts app/ on the path)

from backend.execution import container  # noqa: E402
from backend.execution.container import ContainerResolver  # noqa: E402
from backend.execution.environment import MissingBackend  # noqa: E402
from backend.execution.stage import RunContext, RunOptions  # noqa: E402
from backend.execution.workspace import Workspace  # noqa: E402


def resolver(root: Path, **fields) -> ContainerResolver:
    """A resolver whose preconditions are already satisfied."""
    context = RunContext(
        workspace=Workspace(root / "results"),
        options=RunOptions(threads=4),
        host_index_prefix=root / "db" / "hg38" / "GRCh38_index",
        metaphlan_database=root / "db" / "metaphlan",
        metaphlan_index="mpa_x",
        memory_limit_bytes=14 * 1024 ** 3,
    )
    made = ContainerResolver(context=context, runtime="podman", **fields)
    made.runtime = "podman"
    return made


class _Ready:
    """Satisfy require() without needing a runtime or an image present."""

    def __enter__(self):
        self._patches = [
            mock.patch.object(container, "runtime_available", return_value=(True, "")),
            mock.patch.object(ContainerResolver, "image_present", return_value=True),
            mock.patch.object(ContainerResolver, "check", return_value=[]),
        ]
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in self._patches:
            patch.stop()


class ResolverTestCase(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-container-")
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        for part in ("reads", "results", "db/hg38", "db/metaphlan"):
            (self.root / part).mkdir(parents=True, exist_ok=True)
        (self.root / "reads" / "a.fastq.gz").write_bytes(b"")

    def resolve(self, environment="qc", command=None):
        command = command or ["fastqc", "--outdir", str(self.root / "results"),
                              str(self.root / "reads" / "a.fastq.gz")]
        with _Ready():
            return resolver(self.root).resolve(environment, command)


class InnerCommandTests(ResolverTestCase):
    def test_the_inner_command_matches_the_native_one(self):
        """The whole design rests on this.

        Both backends run the tool through Micromamba in an environment of the
        same name. Only the root differs, because the image's is fixed.
        """
        from backend.execution.environment import EnvironmentResolver

        command = ["fastqc", "--outdir", "/out", "/in/a.fastq.gz"]
        made = resolver(self.root)
        inner = made.inner_command("qc", command)

        native_config = EnvironmentResolver().config
        self.assertEqual(
            inner,
            ["micromamba", "run", "-r", "/opt/conda",
             "-n", native_config.environment_name("qc"), *command],
        )
        # Everything after the environment name is the tool's own command,
        # untouched.
        self.assertEqual(inner[-len(command):], command)

    def test_the_environment_name_is_the_same_in_both_backends(self):
        made = resolver(self.root)
        for key in ("qc", "hostrem", "taxonomy"):
            with self.subTest(environment=key):
                self.assertEqual(
                    made.environment_name(key), made.config.environment_name(key)
                )


class CommandShapeTests(ResolverTestCase):
    def test_the_runtime_is_the_program_and_run_is_the_first_argument(self):
        program, arguments = self.resolve()
        self.assertEqual(program, "podman")
        self.assertEqual(arguments[0], "run")

    def test_the_image_precedes_the_inner_command(self):
        program, arguments = self.resolve()
        index = arguments.index(container.DEFAULT_IMAGE)
        self.assertEqual(arguments[index + 1], "micromamba")

    def test_the_command_is_deterministic(self):
        # It is not fingerprinted, but a command that varied between two
        # identical runs would make a container result impossible to reproduce.
        first = self.resolve()[1]
        second = self.resolve()[1]
        self.assertEqual(
            [a for a in first if not a.endswith(".cid")],
            [a for a in second if not a.endswith(".cid")],
        )

    def test_no_argument_is_a_shell_string(self):
        # Everything is executed as an argument list. A single string here
        # would be the beginning of a shell, and of injection.
        _program, arguments = self.resolve()
        for argument in arguments:
            self.assertNotIn("&&", argument)
            self.assertNotIn(";", argument)
            self.assertNotIn("|", argument)


class SecurityPostureTests(ResolverTestCase):
    def test_the_posture_is_complete(self):
        _program, arguments = self.resolve()
        joined = " ".join(arguments)
        for flag in ("--rm", "--network=none", "--cap-drop=ALL",
                     "--security-opt=no-new-privileges", "--read-only"):
            with self.subTest(flag=flag):
                self.assertIn(flag, arguments)
        self.assertIn("label=disable", joined)
        self.assertIn("/tmp:rw", joined)

    def test_analysis_never_gets_a_network(self):
        # A FASTQ is untrusted input. With no interface, a compromised tool has
        # nowhere to send anything - and MetaPhlAn's --offline becomes a fact.
        _program, arguments = self.resolve()
        self.assertIn("--network=none", arguments)
        self.assertNotIn("--network=host", arguments)

    def test_nothing_privileged_is_ever_requested(self):
        _program, arguments = self.resolve()
        joined = " ".join(arguments)
        for forbidden in ("--privileged", "--network=host", "docker.sock",
                          "--cap-add", "--user root", "--userns=host"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, joined)

    def test_podman_maps_the_invoking_user(self):
        made = resolver(self.root)
        made.runtime = "podman"
        self.assertIn("--userns=keep-id", made.security_arguments())

    def test_docker_passes_the_invoking_uid_instead(self):
        # Docker has no keep-id; without --user the results would be root's.
        made = resolver(self.root)
        made.runtime = "docker"
        arguments = made.security_arguments()
        self.assertIn("--user", arguments)
        self.assertIn(f"{os.getuid()}:{os.getgid()}", arguments)


class MountTests(ResolverTestCase):
    def plan(self, command):
        return resolver(self.root).mount_plan(command)

    def test_results_are_writable_and_references_are_not(self):
        plan = self.plan(["metaphlan", str(self.root / "reads" / "a.fastq.gz")])
        self.assertIn(self.root / "results", plan.writable)
        self.assertIn(self.root / "db" / "metaphlan", plan.read_only)
        self.assertIn(self.root / "db" / "hg38", plan.read_only)

    def test_every_path_in_the_command_is_reachable(self):
        # Derived from the arguments rather than a list kept beside them, so a
        # stage that names a new file cannot be forgotten here.
        elsewhere = self.root / "other"
        elsewhere.mkdir()
        plan = self.plan(["fastqc", str(elsewhere / "b.fastq.gz")])
        self.assertTrue(
            any(elsewhere == m or m in elsewhere.parents
                for m in plan.read_only + plan.writable),
            f"{elsewhere} is not covered by {plan}",
        )

    def test_a_path_is_never_both_readable_and_writable(self):
        plan = self.plan(["fastqc", "--outdir", str(self.root / "results"),
                          str(self.root / "reads" / "a.fastq.gz")])
        self.assertEqual(set(plan.read_only) & set(plan.writable), set())

    def test_nested_mounts_are_collapsed(self):
        nested = self.root / "reads" / "deep" / "deeper"
        nested.mkdir(parents=True)
        plan = self.plan(["fastqc", str(self.root / "reads" / "a.fastq.gz"),
                          str(nested / "c.fastq.gz")])
        self.assertNotIn(nested, plan.read_only)
        self.assertIn(self.root / "reads", plan.read_only)

    def test_the_image_is_never_mounted_over(self):
        # Mounting the host over /opt would replace the analysis tools with
        # whatever the host has there.
        plan = self.plan(["metaphlan", "--bowtie2_exe", "/opt/bioflow/bin/bowtie2-mm"])
        for path in plan.read_only + plan.writable:
            self.assertNotEqual(str(path), "/opt")

    def test_dev_null_is_not_treated_as_data(self):
        plan = self.plan(["bowtie2", "-S", "/dev/null"])
        self.assertNotIn(Path("/dev"), plan.read_only + plan.writable)

    def test_a_quoted_bowtie2_template_still_resolves(self):
        # gzip_output_argument may quote the value for Bowtie2's own sh -c.
        awkward = self.root / "My Results"
        awkward.mkdir()
        plan = self.plan(["bowtie2", "--un-conc-gz", f"'{awkward}/p_nohost_R%.fastq.gz'"])
        self.assertTrue(
            any(awkward == m or m in awkward.parents
                for m in plan.read_only + plan.writable),
            f"the quoted template was not resolved: {plan}",
        )

    def test_outputs_that_do_not_exist_yet_still_mount(self):
        plan = self.plan(["fastqc", "--outdir", str(self.root / "results" / "new" / "x.html")])
        self.assertTrue(plan.writable or plan.read_only)


class RefusedPathTests(ResolverTestCase):
    def test_system_directories_are_refused(self):
        for path in ("/usr", "/etc", "/opt", "/var", "/bin", "/lib"):
            with self.subTest(path=path):
                self.assertTrue(container.is_system_path(Path(path)))

    def test_tmp_is_refused_because_the_tmpfs_hides_it(self):
        """Found by running it, not by reading about it.

        The container gets a writable tmpfs at /tmp. With identity mounts a host
        directory under /tmp is shadowed by that tmpfs: the mount succeeds and
        the directory is empty, which is far worse than an error.
        """
        self.assertTrue(container.is_system_path(Path("/tmp")))

    def test_ordinary_data_locations_are_allowed(self):
        for path in ("/home/someone/reads", "/mnt/store", "/data/refs",
                     "/media/disk", "/srv/shared"):
            with self.subTest(path=path):
                self.assertFalse(container.is_system_path(Path(path)))

    def test_a_system_path_in_the_command_is_skipped_not_refused(self):
        """These two cases look alike and must be treated differently.

        A system path named in the command refers to the container's own
        filesystem - /tmp is the tmpfs it was given, /usr/bin/python is the
        image's interpreter. Mounting the host over either would be wrong, and
        refusing the run because a command mentions one would be wrong too.
        """
        made = resolver(self.root)
        plan = made.mount_plan([
            "python", "-c", "pass",
            "/tmp/scratch/work", "/usr/bin/python", "/etc/hosts",
        ])
        for path in plan.read_only + plan.writable:
            self.assertFalse(
                container.is_system_path(path), f"{path} should not be mounted"
            )

    def test_a_command_naming_only_system_paths_still_resolves(self):
        made = resolver(self.root)
        with _Ready():
            _program, arguments = made.resolve("qc", ["python", "-c", "open('/tmp/x','w')"])
        self.assertIn("run", arguments)

    def test_data_at_a_system_path_fails_with_an_explanation(self):
        # The fatal case: the user's results directory really is at /usr, so
        # there is nowhere to write and mounting over it would replace the
        # analysis tools.
        made = resolver(self.root)
        made.context.workspace = Workspace(Path("/usr"))
        with self.assertRaises(MissingBackend) as raised:
            made.mount_plan(["fastqc", "--outdir", "/usr"])
        self.assertIn("/usr", str(raised.exception))
        self.assertIn("run natively", str(raised.exception))


class PreconditionTests(ResolverTestCase):
    def test_a_missing_runtime_is_reported_not_guessed_at(self):
        made = resolver(self.root)
        made.runtime = None
        with self.assertRaises(MissingBackend) as raised:
            made.require("qc")
        self.assertIn("Podman", str(raised.exception))
        self.assertIn("container-runtime", raised.exception.components)

    def test_a_runtime_that_cannot_start_containers_is_explained(self):
        made = resolver(self.root)
        with mock.patch.object(
            container, "runtime_available", return_value=(False, "permission denied")
        ):
            with self.assertRaises(MissingBackend) as raised:
                made.require("qc")
        self.assertIn("permission denied", str(raised.exception))

    def test_a_missing_image_names_how_to_build_it(self):
        made = resolver(self.root)
        with mock.patch.object(container, "runtime_available", return_value=(True, "")), \
             mock.patch.object(ContainerResolver, "image_present", return_value=False):
            with self.assertRaises(MissingBackend) as raised:
                made.require("qc")
        self.assertIn("build.sh", str(raised.exception))

    def test_a_missing_database_is_still_a_setup_problem(self):
        # Databases are mounted from the host in both backends, so the same
        # component key applies and the Setup page can act on it.
        made = resolver(self.root)
        with mock.patch.object(container, "runtime_available", return_value=(True, "")), \
             mock.patch.object(ContainerResolver, "image_present", return_value=True), \
             mock.patch.object(ContainerResolver, "check", return_value=["db:metaphlan_chocophlan"]):
            with self.assertRaises(MissingBackend) as raised:
                made.require("taxonomy", ("metaphlan_chocophlan",))
        self.assertIn("db:metaphlan_chocophlan", raised.exception.components)

    def test_it_never_asks_whether_micromamba_is_installed_locally(self):
        # The environments are in the image; requiring a local one would make
        # container mode need the very installation it exists to avoid.
        made = resolver(self.root)
        self.assertEqual(made.check("qc", ()), [])


class RuntimeFailureTests(unittest.TestCase):
    def test_runtime_exit_codes_are_distinguished_from_tool_ones(self):
        for code in (125, 126, 127):
            with self.subTest(code=code):
                self.assertTrue(container.describe_runtime_exit(code))

    def test_a_tool_exit_code_is_left_alone(self):
        # Masking a tool's own status would blame the runtime for a real failure.
        for code in (0, 1, 2, 42):
            with self.subTest(code=code):
                self.assertEqual(container.describe_runtime_exit(code), "")

    def test_podman_is_preferred_over_docker(self):
        self.assertLess(
            container.RUNTIMES.index("podman"), container.RUNTIMES.index("docker")
        )


class CancellationTests(ResolverTestCase):
    def test_a_cidfile_is_requested_when_a_directory_is_given(self):
        directory = self.root / "cids"
        with _Ready():
            made = resolver(self.root, cidfile_directory=directory)
            _program, arguments = made.resolve("qc", ["fastqc", "--version"])
        self.assertIn("--cidfile", arguments)

    def test_cleanup_removes_a_recorded_container(self):
        directory = self.root / "cids"
        directory.mkdir()
        made = resolver(self.root, cidfile_directory=directory)
        cidfile = made.cidfile_for("qc")
        cidfile.write_text("abc123def456\n", encoding="utf-8")
        with mock.patch.object(subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            removed = made.cleanup_containers()
        self.assertEqual(removed, ["abc123def456"])
        arguments = run.call_args[0][0]
        self.assertEqual(arguments[:3], ["podman", "rm", "-f"])
        self.assertFalse(cidfile.exists(), "the cidfile outlived the container")

    def test_cleanup_is_safe_when_nothing_was_started(self):
        made = resolver(self.root, cidfile_directory=self.root / "cids")
        self.assertEqual(made.cleanup_containers(), [])

    def test_the_runner_sweeps_containers_on_cancel(self):
        """CommandRunner must clean up whatever its resolver can strand.

        Terminating the client usually stops the container; a client killed
        outright cannot, and the container would keep running with nothing
        attached to it.
        """
        from backend.execution.runner import CommandRunner

        swept = []

        class _Resolver:
            config = mock.Mock()

            def cleanup_containers(self):
                swept.append(True)
                return []

        CommandRunner(resolver=_Resolver()).cancel()
        self.assertEqual(swept, [True])

    def test_cancelling_a_native_run_is_unaffected(self):
        # The native resolver has nothing to clean and does not expose this.
        from backend.execution.environment import EnvironmentResolver
        from backend.execution.runner import CommandRunner

        CommandRunner(resolver=EnvironmentResolver()).cancel()  # must not raise


def live_container_available() -> bool:
    """A runtime and the image are both present on this machine."""
    made = ContainerResolver()
    return made.runtime is not None and made.image_present()


@unittest.skipUnless(live_container_available(), "no container runtime or image on this machine")
class LiveExecutionTests(unittest.TestCase):
    """The command the resolver builds must actually run.

    Everything above is about the shape of the command. This runs it, because a
    command that is correct on paper and rejected by the runtime is still a
    broken backend - and the two things that broke it during development,
    SELinux labelling and the tmpfs hiding a mount, were both invisible until
    something was executed.
    """

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(
            prefix="bioflow-live-", dir=Path.home()
        )
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        (self.root / "reads").mkdir()
        (self.root / "results").mkdir()
        import gzip
        with gzip.open(self.root / "reads" / "a.fastq.gz", "wt") as handle:
            for index in range(200):
                handle.write(f"@r{index}\n{'ACGT' * 30}\n+\n{'I' * 120}\n")

    def _resolver(self):
        context = RunContext(
            workspace=Workspace(self.root / "results"),
            options=RunOptions(threads=2),
            memory_limit_bytes=14 * 1024 ** 3,
        )
        return ContainerResolver(context=context)

    def _run(self, environment, command, timeout=300):
        program, arguments = self._resolver().resolve(environment, command)
        return subprocess.run(
            [program, *arguments], capture_output=True, text=True, timeout=timeout
        )

    def test_a_resolved_command_runs_and_produces_output(self):
        reads = self.root / "reads" / "a.fastq.gz"
        results = self.root / "results"
        finished = self._run(
            "qc", ["fastqc", "--threads", "2", "--outdir", str(results), str(reads)]
        )
        self.assertEqual(finished.returncode, 0, finished.stderr[-800:])
        produced = sorted(path.name for path in results.iterdir())
        self.assertIn("a_fastqc.html", produced)
        self.assertIn("a_fastqc.zip", produced)

    def test_output_is_owned_by_the_invoking_user(self):
        # The whole point of the user mapping. Results owned by root would be
        # undeletable without sudo, which is unacceptable on a desktop.
        results = self.root / "results"
        self._run("qc", ["fastqc", "--outdir", str(results),
                         str(self.root / "reads" / "a.fastq.gz")])
        produced = [path for path in results.iterdir()]
        self.assertTrue(produced, "nothing was produced to check ownership of")
        for path in produced:
            self.assertEqual(path.stat().st_uid, os.getuid(), f"{path} is not ours")

    def test_the_container_really_has_no_network(self):
        finished = self._run(
            "qc", ["python", "-c",
                   "import socket;socket.gethostbyname('deb.debian.org')"]
        )
        self.assertNotEqual(finished.returncode, 0, "the container resolved a hostname")

    def test_the_root_filesystem_really_is_read_only(self):
        finished = self._run("qc", ["python", "-c", "open('/probe','w')"])
        self.assertNotEqual(finished.returncode, 0, "the container root was writable")

    def test_a_read_only_mount_cannot_be_written_to(self):
        # References are mounted read-only so an analysis cannot damage a
        # database that other runs, and the native backend, depend on.
        reads = self.root / "reads"
        context = RunContext(
            workspace=Workspace(self.root / "results"),
            options=RunOptions(threads=2),
            metaphlan_database=reads,
            memory_limit_bytes=14 * 1024 ** 3,
        )
        made = ContainerResolver(context=context)
        program, arguments = made.resolve(
            "qc", ["python", "-c", f"open('{reads}/probe','w')"]
        )
        finished = subprocess.run([program, *arguments], capture_output=True, text=True, timeout=300)
        self.assertNotEqual(finished.returncode, 0, "a reference mount was writable")
        self.assertFalse((reads / "probe").exists())

    def test_no_container_is_left_behind(self):
        made = self._resolver()
        before = subprocess.run(
            [made.runtime, "ps", "-aq"], capture_output=True, text=True, timeout=60
        ).stdout.split()
        self._run("qc", ["fastqc", "--version"])
        after = subprocess.run(
            [made.runtime, "ps", "-aq"], capture_output=True, text=True, timeout=60
        ).stdout.split()
        self.assertEqual(set(after) - set(before), set(), "--rm left a container behind")

    def test_the_image_reports_a_digest(self):
        # What a run record should name: a tag can be moved, a digest cannot.
        digest = self._resolver().image_digest()
        self.assertTrue(digest.startswith("sha256:"), digest)


if __name__ == "__main__":
    unittest.main()
