"""The image definition, and the image itself where one has been built.

The image exists so that a container run executes the same tools as the desktop
run it will be compared against in validation. That only holds if the pins here
match what the native installation actually resolved to, and if the reference
databases stay outside: baking the ~51 GB MetaPhlAn database would make a fastp
bugfix a 51 GB re-download, stop the two backends sharing one copy, and put user
reference data inside a redistributable artefact.

The definition is checked always. The built image is checked when a runtime and
the image are both present, and skipped otherwise, so this suite still runs on a
machine that has never built it.
"""

from pathlib import Path
import json
import os
import shutil
import subprocess
import unittest

import support  # noqa: F401  (puts app/ on the path)

from backend.setup.registry import environment_specs  # noqa: E402


DOCKER = Path(__file__).resolve().parent.parent / "docker"
IMAGE = os.environ.get("BIOFLOW_TEST_IMAGE", "localhost/bioflow-tools:0.1.0")

#: Environments the released pipeline uses. "function" is withheld from this
#: release, so the image deliberately does not carry it.
IMAGE_ENVIRONMENTS = ("qc", "hostrem", "taxonomy")


def specification(name: str) -> list[str]:
    """The package pins for one environment, comments stripped."""
    text = (DOCKER / "environments" / f"{name}.txt").read_text(encoding="utf-8")
    return [
        line.strip() for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def runtime() -> str | None:
    for candidate in ("podman", "docker"):
        if shutil.which(candidate):
            return candidate
    return None


def image_present() -> bool:
    tool = runtime()
    if tool is None:
        return False
    result = subprocess.run(
        [tool, "image", "exists", IMAGE], capture_output=True, timeout=60
    )
    return result.returncode == 0


class DefinitionTests(unittest.TestCase):
    def test_there_is_a_specification_for_every_released_environment(self):
        for name in IMAGE_ENVIRONMENTS:
            with self.subTest(environment=name):
                self.assertTrue(
                    (DOCKER / "environments" / f"{name}.txt").is_file(),
                    f"the image has no package list for env:{name}",
                )

    def test_the_withheld_environment_is_not_in_the_image(self):
        # HUMAnN is out of scope for this release; shipping it would put a tool
        # in the image that the interface deliberately does not offer.
        self.assertFalse(
            (DOCKER / "environments" / "function.txt").exists(),
            "the image carries the withheld functional-profiling environment",
        )

    def test_every_package_the_registry_names_is_in_the_image(self):
        """The image must carry the tools the stages resolve against.

        A stage asks its environment for a tool by name. If the image's list
        drifted from the registry's, the container backend would fail at the
        point of running rather than at setup.
        """
        specs = environment_specs()
        for name in IMAGE_ENVIRONMENTS:
            declared = {
                package.split("=")[0] for package in specs[name].packages
            }
            pinned = {package.split("=")[0] for package in specification(name)}
            with self.subTest(environment=name):
                self.assertEqual(
                    declared - pinned,
                    set(),
                    f"env:{name} declares packages the image does not pin",
                )

    def test_every_package_is_pinned_to_a_version(self):
        # An unpinned image is not comparable with a native run: the two would
        # be free to solve to different builds of the same tool.
        for name in IMAGE_ENVIRONMENTS:
            for package in specification(name):
                with self.subTest(environment=name, package=package):
                    self.assertIn("=", package, f"{package} is not pinned")

    def test_no_database_is_referenced_by_the_build(self):
        text = (DOCKER / "Dockerfile").read_text(encoding="utf-8").lower()
        for forbidden in ("chocophlan", "grch38", "metaphlan_databases", "uniref"):
            with self.subTest(term=forbidden):
                # Prose may discuss them; a build instruction may not fetch one.
                instructions = [
                    line for line in text.splitlines()
                    if line.strip() and not line.strip().startswith("#")
                ]
                self.assertNotIn(
                    forbidden, " ".join(instructions),
                    f"the build refers to {forbidden}; databases are mounted, never built in",
                )

    def test_there_is_no_entrypoint(self):
        # BioFlow supplies the whole command. An entrypoint that rewrote
        # arguments would break the correspondence between what the run record
        # says ran and what actually ran.
        instructions = [
            line.strip() for line in (DOCKER / "Dockerfile").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertFalse(
            [line for line in instructions if line.upper().startswith("ENTRYPOINT")],
            "the image declares an ENTRYPOINT",
        )

    def test_the_image_runs_as_a_non_root_user(self):
        instructions = [
            line.strip() for line in (DOCKER / "Dockerfile").read_text(encoding="utf-8").splitlines()
            if line.strip().upper().startswith("USER")
        ]
        self.assertTrue(instructions, "the image never switches away from root")
        self.assertNotIn("root", instructions[-1].lower())
        self.assertNotIn("0:0", instructions[-1])

    def test_the_shim_forces_memory_mapping_through_micromamba(self):
        shim = (DOCKER / "bin" / "bowtie2-mm").read_text(encoding="utf-8")
        # --mm is the whole point; without it the 33 GB index is loaded into
        # anonymous memory and the run is killed on a small machine.
        self.assertIn("--mm", shim)
        # Through micromamba, because Bowtie2 is a Perl script that dies on a
        # missing Sys::Hostname when its environment is not activated.
        self.assertIn("micromamba", shim)
        self.assertIn("bioflow-taxonomy", shim)
        self.assertTrue(shim.startswith("#!"), "the shim has no interpreter line")

    def test_the_build_script_prefers_podman(self):
        script = (DOCKER / "build.sh").read_text(encoding="utf-8")
        self.assertLess(
            script.index("podman"), script.index("docker"),
            "docker is checked before podman; podman is the rootless default on Fedora",
        )


@unittest.skipUnless(image_present(), f"{IMAGE} has not been built on this machine")
class BuiltImageTests(unittest.TestCase):
    """Against the image itself, under the posture the backend will use."""

    SECURITY = [
        "--network=none",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        # Fedora and RHEL enforce SELinux on bind mounts. Relabelling with :z/:Z
        # would rewrite the labels of a ~51 GB reference database, and :Z would
        # leave it unusable by the native backend, so confinement is dropped for
        # the container instead and every host label is left alone.
        "--security-opt", "label=disable",
        "--read-only",
        "--tmpfs", "/tmp:rw,size=1g",
    ]

    def run_in_image(self, *command: str, timeout: int = 300) -> subprocess.CompletedProcess:
        return subprocess.run(
            [runtime(), "run", "--rm", *self.SECURITY, "-e", "HOME=/tmp", IMAGE, *command],
            capture_output=True, text=True, timeout=timeout,
        )

    def test_it_does_not_run_as_root(self):
        result = self.run_in_image("id", "-u")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(result.stdout.strip(), "0", "the image runs as root")

    def test_the_root_filesystem_is_read_only(self):
        result = self.run_in_image("/bin/bash", "-lc", "touch /probe 2>/dev/null && echo writable || echo readonly")
        self.assertIn("readonly", result.stdout)

    def test_tmp_is_writable(self):
        result = self.run_in_image("/bin/bash", "-lc", "touch /tmp/probe && echo ok")
        self.assertIn("ok", result.stdout)

    def test_every_tool_runs_through_micromamba(self):
        expected = {
            ("bioflow-qc", "fastqc", "--version"): "0.12.1",
            ("bioflow-qc", "fastp", "--version"): "1.3.6",
            ("bioflow-qc", "multiqc", "--version"): "1.35",
            ("bioflow-hostrem", "bowtie2", "--version"): "2.5.5",
            ("bioflow-hostrem", "samtools", "--version"): "1.24",
            ("bioflow-taxonomy", "bowtie2", "--version"): "2.5.5",
        }
        for (environment, tool, flag), version in expected.items():
            with self.subTest(environment=environment, tool=tool):
                result = self.run_in_image(
                    "micromamba", "run", "-r", "/opt/conda", "-n", environment, tool, flag
                )
                self.assertEqual(result.returncode, 0, result.stderr[-500:])
                self.assertIn(version, result.stdout + result.stderr)

    def test_metaphlan_reports_itself(self):
        result = self.run_in_image(
            "micromamba", "run", "-r", "/opt/conda", "-n", "bioflow-taxonomy",
            "metaphlan", "--version",
        )
        self.assertEqual(result.returncode, 0, result.stderr[-500:])
        self.assertIn("MetaPhlAn version", result.stdout + result.stderr)

    def test_the_shim_is_present_and_executable(self):
        result = self.run_in_image(
            "/bin/bash", "-lc", "test -x /opt/bioflow/bin/bowtie2-mm && echo ok"
        )
        self.assertIn("ok", result.stdout)

    def test_no_reference_database_is_inside_the_image(self):
        # The saving is the point: a Bowtie2 index or a marker table in here
        # would be tens of gigabytes that cannot be shared with native mode.
        result = self.run_in_image(
            "/bin/bash", "-lc",
            "find / -xdev \\( -name '*.bt2' -o -name '*.bt2l' \\) 2>/dev/null | wc -l; "
            "find / -xdev -name '*.pkl' -size +10M 2>/dev/null | wc -l",
        )
        indexes, markers = result.stdout.split()
        self.assertEqual(indexes, "0", "a Bowtie2 index is baked into the image")
        self.assertEqual(markers, "0", "a marker table is baked into the image")

    def test_the_solved_package_lists_are_recorded(self):
        # An explicit list names every package by URL and build, which is what
        # makes a rebuild reproducible rather than merely similar.
        result = self.run_in_image(
            "/bin/bash", "-lc", "ls /opt/bioflow/environments/*.lock | wc -l"
        )
        self.assertEqual(result.stdout.strip(), "3")


if __name__ == "__main__":
    unittest.main()
