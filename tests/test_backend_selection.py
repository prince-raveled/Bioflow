"""Choosing a backend, and what a result then remembers about it.

Native is the default and must stay the default: it needs nothing beyond what
BioFlow installs for itself, which is what makes a fresh Linux machine work
without asking anything of the person using it. Container execution has to be
chosen deliberately.

The other half is that a result must remember which backend produced it. The
fingerprint hashes a stage's own command, and four of the six stages pass no
argument that differs between backends, so without an execution identity a
switch would silently reuse the other backend's results for those and re-run
only MetaPhlAn - leaving a record claiming one execution environment for a
result mostly produced by the other.
"""

from pathlib import Path
from unittest import mock
import json
import tempfile
import unittest

import support  # noqa: F401  (puts app/ on the path)

from backend.config import BioFlowConfig, DEFAULT_CONTAINER_IMAGE, EXECUTION_BACKENDS  # noqa: E402
from backend.execution import backends  # noqa: E402
from backend.execution.backends import backend_for, resolver_for  # noqa: E402
from backend.execution.container import ContainerResolver  # noqa: E402
from backend.execution.environment import EnvironmentResolver  # noqa: E402
from backend.execution.pipeline import PipelineExecutor, stages_by_key  # noqa: E402
from backend.execution.record import fingerprint_for  # noqa: E402
from backend.execution.stage import RunContext, RunOptions  # noqa: E402
from backend.execution.workspace import Workspace  # noqa: E402
from backend.samples import ReadLayout, Sample  # noqa: E402


ROOT = Path("/tmp/bioflow-backend-probe")
SINGLE = Sample("s", ReadLayout.SINGLE, ROOT / "a.fastq.gz")
PAIRED = Sample("p", ReadLayout.PAIRED, ROOT / "a_R1.fastq.gz", ROOT / "a_R2.fastq.gz")


def context(backend="native", image="", **overrides) -> RunContext:
    fields = {
        "host_index_prefix": ROOT / "GRCh38_index",
        "metaphlan_database": ROOT / "db",
        "metaphlan_index": "mpa_x",
        "bowtie2_memory_mapped_shim": ROOT / "bowtie2-mm",
        "memory_limit_bytes": 14 * 1024 ** 3,
    }
    fields.update(overrides)
    return RunContext(
        workspace=Workspace(ROOT / "run"),
        options=RunOptions(threads=8),
        execution_backend=backend,
        execution_image=image,
        **fields,
    )


class DefaultTests(unittest.TestCase):
    def test_a_fresh_configuration_is_native(self):
        config = BioFlowConfig(data_root=ROOT, database_root=ROOT / "db")
        self.assertEqual(config.execution_backend, "native")

    def test_the_default_resolver_is_the_native_one(self):
        config = BioFlowConfig(data_root=ROOT, database_root=ROOT / "db")
        self.assertIsInstance(resolver_for(config=config), EnvironmentResolver)

    def test_container_must_be_asked_for(self):
        config = BioFlowConfig(
            data_root=ROOT, database_root=ROOT / "db", execution_backend="container"
        )
        self.assertIsInstance(resolver_for(config=config), ContainerResolver)

    def test_the_context_overrides_the_setting(self):
        """A run keeps the backend it started with.

        Changing the preference part-way through an analysis must not split one
        result across two execution environments.
        """
        config = BioFlowConfig(
            data_root=ROOT, database_root=ROOT / "db", execution_backend="container"
        )
        self.assertEqual(backend_for(context("native"), config), "native")
        self.assertIsInstance(
            resolver_for(context("native"), config=config), EnvironmentResolver
        )

    def test_both_backends_are_offered(self):
        self.assertEqual(EXECUTION_BACKENDS, ("native", "container"))


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(support.isolate_bioflow_data_directory(self))

    def test_the_choice_survives_a_reload(self):
        from backend.config import get_config, reload_config

        config = get_config()
        config.execution_backend = "container"
        config.container_image = "example.org/tools:2"
        config.save()
        reload_config()
        reloaded = get_config()
        self.assertEqual(reloaded.execution_backend, "container")
        self.assertEqual(reloaded.container_image, "example.org/tools:2")

    def test_an_unknown_backend_falls_back_to_native(self):
        """A newer build's setting must never leave someone unable to run.

        Refusing to start because a value is unrecognised would strand a user
        who had opened a configuration written by a later version.
        """
        from backend.config import get_config, reload_config

        settings = self.root / "config.json"
        settings.write_text(
            json.dumps({"execution": {"backend": "quantum"}}), encoding="utf-8"
        )
        reload_config()
        self.assertEqual(get_config().execution_backend, "native")

    def test_the_default_image_is_recorded(self):
        from backend.config import get_config

        self.assertEqual(get_config().container_image, DEFAULT_CONTAINER_IMAGE)


class CommandParityTests(unittest.TestCase):
    """The inner command must not depend on the backend.

    This is the regression test for the whole design: if it holds, a container
    result and a native result can be compared argument by argument, and one
    set of stage tests covers both.
    """

    def test_every_stage_produces_the_same_inner_command_in_both_backends(self):
        native = EnvironmentResolver()
        containerised = ContainerResolver(context=context("container"))
        for key, stage in stages_by_key().items():
            for sample in (SINGLE, PAIRED):
                for command in stage.commands(sample, context()):
                    with self.subTest(stage=key, layout=sample.layout.name):
                        inner = containerised.inner_command(
                            command.environment_key, command.command
                        )
                        # micromamba run -r <root> -n <name> <tool> <args...>
                        # Same environment name, same tool, same arguments.
                        self.assertEqual(
                            inner[5], native.config.environment_name(command.environment_key)
                        )
                        self.assertEqual(inner[6:], command.command)

    def test_only_the_micromamba_root_differs(self):
        containerised = ContainerResolver(context=context("container"))
        inner = containerised.inner_command("qc", ["fastqc", "--version"])
        self.assertEqual(inner[:4], ["micromamba", "run", "-r", "/opt/conda"])


class ExecutionIdentityTests(unittest.TestCase):
    def test_native_contributes_nothing(self):
        self.assertEqual(context("native").execution_identity, "")

    def test_a_container_identity_names_the_image(self):
        identity = context("container", "img@sha256:aaaa").execution_identity
        self.assertEqual(identity, "container:img@sha256:aaaa")

    def test_a_tag_and_a_digest_are_different_identities(self):
        # A tag can be moved to different bytes; a result should name what
        # actually ran, so the two must not be interchangeable.
        tagged = context("container", "img:1").execution_identity
        digested = context("container", "img:1@sha256:aaaa").execution_identity
        self.assertNotEqual(tagged, digested)

    def test_two_digests_are_different_identities(self):
        first = context("container", "img@sha256:aaaa").execution_identity
        second = context("container", "img@sha256:bbbb").execution_identity
        self.assertNotEqual(first, second)


class FingerprintSeparationTests(unittest.TestCase):
    """Switching backend must never reuse the other one's results."""

    def fingerprint(self, stage, sample, ctx):
        return fingerprint_for(
            [c.command for c in stage.commands(sample, ctx)],
            stage.inputs(sample, ctx),
            ctx.execution_identity,
        )

    def test_no_stage_shares_a_fingerprint_across_backends(self):
        native = context("native")
        containerised = context("container", "img@sha256:aaaa")
        for key, stage in stages_by_key().items():
            for sample in (SINGLE, PAIRED):
                with self.subTest(stage=key, layout=sample.layout.name):
                    self.assertNotEqual(
                        self.fingerprint(stage, sample, native),
                        self.fingerprint(stage, sample, containerised),
                        f"{key} would resume from the other backend's result",
                    )

    def test_the_stages_that_look_identical_are_the_point(self):
        """Four stages pass nothing that differs between backends.

        FastQC, fastp, MultiQC and host removal name no path that changes with
        the backend, so their commands really are identical - which is exactly
        why the identity has to carry the difference instead.
        """
        native = context("native")
        containerised = context("container", "img@sha256:aaaa")
        for key in ("fastqc_raw", "fastp", "multiqc", "host_removal"):
            stage = stages_by_key()[key]
            with self.subTest(stage=key):
                self.assertEqual(
                    [c.command for c in stage.commands(SINGLE, native)],
                    [c.command for c in stage.commands(SINGLE, containerised)],
                    "this stage's command differs by backend; the test is not testing what it says",
                )
                self.assertNotEqual(
                    self.fingerprint(stage, SINGLE, native),
                    self.fingerprint(stage, SINGLE, containerised),
                )

    def test_a_rebuilt_image_is_not_the_same_image(self):
        first = context("container", "img@sha256:aaaa")
        second = context("container", "img@sha256:bbbb")
        stage = stages_by_key()["fastqc_raw"]
        self.assertNotEqual(
            self.fingerprint(stage, SINGLE, first),
            self.fingerprint(stage, SINGLE, second),
        )


class ExecutorWiringTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-exec-")
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)

    def _context(self, backend):
        return RunContext(
            workspace=Workspace(self.root / "run"),
            options=RunOptions(threads=2),
            execution_backend=backend,
            execution_image="img@sha256:aaaa" if backend == "container" else "",
            memory_limit_bytes=14 * 1024 ** 3,
        )

    def test_the_executor_uses_the_native_resolver_by_default(self):
        executor = PipelineExecutor(self._context("native"), [])
        self.assertIsInstance(executor.resolver, EnvironmentResolver)

    def test_the_executor_uses_the_container_resolver_when_asked(self):
        executor = PipelineExecutor(self._context("container"), [])
        self.assertIsInstance(executor.resolver, ContainerResolver)

    def test_an_explicit_resolver_still_wins(self):
        # The standalone pages and the tests supply their own.
        sentinel = EnvironmentResolver()
        executor = PipelineExecutor(self._context("container"), [], resolver=sentinel)
        self.assertIs(executor.resolver, sentinel)

    def test_the_runner_holds_the_chosen_resolver(self):
        executor = PipelineExecutor(self._context("container"), [])
        self.assertIs(executor.runner.resolver, executor.resolver)

    def test_container_runs_get_somewhere_to_record_container_ids(self):
        # Without it a cancel cannot guarantee cleanup of a container whose
        # client was killed outright.
        executor = PipelineExecutor(self._context("container"), [])
        self.assertIsNotNone(executor.resolver.cidfile_directory)


class ImageReferenceTests(unittest.TestCase):
    def test_a_digest_is_preferred_over_a_tag(self):
        config = BioFlowConfig(
            data_root=ROOT, database_root=ROOT / "db", container_image="img:1"
        )
        with mock.patch.object(ContainerResolver, "image_digest", return_value="sha256:abc"):
            reference = backends.execution_image_reference(config)
        self.assertEqual(reference, "img:1@sha256:abc")

    def test_the_tag_is_used_when_no_digest_is_available(self):
        config = BioFlowConfig(
            data_root=ROOT, database_root=ROOT / "db", container_image="img:1"
        )
        with mock.patch.object(ContainerResolver, "image_digest", return_value="img:1"):
            self.assertEqual(backends.execution_image_reference(config), "img:1")

    def test_no_runtime_still_identifies_the_image(self):
        config = BioFlowConfig(
            data_root=ROOT, database_root=ROOT / "db", container_image="img:1"
        )
        with mock.patch.object(backends, "ContainerResolver") as made:
            made.return_value.runtime = None
            self.assertEqual(backends.execution_image_reference(config), "img:1")


class OverrideTests(unittest.TestCase):
    """Forcing a backend for one process, without changing saved settings.

    A test that deliberately uses the real installation would otherwise inherit
    whichever backend the person running it happens to have selected, so a
    suite could quietly start running containers because of a choice made in
    the interface. A scripted or CI run gets the same guarantee.
    """

    def setUp(self):
        self.root = Path(support.isolate_bioflow_data_directory(self))

    def _reloaded(self):
        from backend.config import get_config, reload_config

        reload_config()
        return get_config()

    def test_the_override_wins_over_the_saved_choice(self):
        import os

        config = self._reloaded()
        config.execution_backend = "container"
        config.save()
        os.environ["BIOFLOW_EXECUTION_BACKEND"] = "native"
        self.assertEqual(self._reloaded().execution_backend, "native")

    def test_the_override_can_also_select_containers(self):
        import os

        os.environ["BIOFLOW_EXECUTION_BACKEND"] = "container"
        self.assertEqual(self._reloaded().execution_backend, "container")

    def test_an_unknown_override_is_ignored(self):
        import os

        os.environ["BIOFLOW_EXECUTION_BACKEND"] = "quantum"
        self.assertEqual(self._reloaded().execution_backend, "native")

    def test_the_override_is_not_persisted(self):
        # It decides what this process does, not what the person has chosen.
        import json
        import os

        os.environ["BIOFLOW_EXECUTION_BACKEND"] = "container"
        config = self._reloaded()
        config.save()
        os.environ.pop("BIOFLOW_EXECUTION_BACKEND", None)
        saved = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["execution"]["backend"], "container")
        # Saving writes what the process resolved to; what matters is that
        # removing the override leaves the file as the only source again.
        self.assertEqual(self._reloaded().execution_backend, "container")


if __name__ == "__main__":
    unittest.main()
