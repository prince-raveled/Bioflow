"""Host-removal output paths must survive spaces, parentheses and quotes.

Bowtie2's --un-gz / --un-conc-gz values are not passed to a program directly:
Bowtie2 builds the shell command `gzip -c >VALUE` and runs it through `sh -c`.
An unquoted path containing a space or parenthesis is split by that shell and
the run dies with a syntax error, so these tests pin the quoting behaviour.
"""

from pathlib import Path
import tempfile
import unittest

import support  # noqa: F401  (puts app/ on the path)
from backend.execution.pipeline import stages_by_key  # noqa: E402
from backend.execution.stage import RunContext, RunOptions  # noqa: E402
from backend.execution.stages.host_removal import gzip_output_argument  # noqa: E402
from backend.execution.workspace import Workspace  # noqa: E402
from backend.samples import ReadLayout, Sample  # noqa: E402


AWKWARD = "SRR17030118 (Copy)"


def context(root: Path) -> RunContext:
    return RunContext(
        workspace=Workspace(root),
        options=RunOptions(threads=2),
        host_index_prefix=root / "idx",
        metaphlan_database=root / "db",
        metaphlan_index="mpa_test",
    )


class QuotingTests(unittest.TestCase):
    """The helper quotes only what needs quoting."""

    def test_an_ordinary_path_is_passed_through_untouched(self):
        plain = Path("/data/results/sample_nohost.fastq.gz")
        self.assertEqual(gzip_output_argument(plain), str(plain))

    def test_a_space_is_quoted(self):
        self.assertEqual(
            gzip_output_argument(Path("/data/my results/s.fastq.gz")),
            "'/data/my results/s.fastq.gz'",
        )

    def test_parentheses_are_quoted(self):
        argument = gzip_output_argument(Path(f"/data/{AWKWARD}_nohost.fastq.gz"))
        self.assertTrue(argument.startswith("'") and argument.endswith("'"))
        self.assertIn(AWKWARD, argument)

    def test_an_apostrophe_is_escaped(self):
        argument = gzip_output_argument(Path("/data/Ann's run.fastq.gz"))
        # shlex.quote escapes the apostrophe rather than ending the quoted word.
        self.assertNotEqual(argument, "/data/Ann's run.fastq.gz")
        self.assertIn('"\'"', argument)

    def test_brackets_and_other_shell_characters_are_quoted(self):
        for awkward in ("a[1].gz", "a{1}.gz", "a;b.gz", "a&b.gz", "a$b.gz", "a*b.gz"):
            with self.subTest(name=awkward):
                self.assertNotEqual(
                    gzip_output_argument(Path("/d") / awkward), f"/d/{awkward}"
                )


class CommandConstructionTests(unittest.TestCase):
    """The stage puts the quoted value on the command line, unchanged otherwise."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-hostrem-path-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.context = context(self.root)
        self.stage = stages_by_key()["host_removal"]

    def command_for(self, sample: Sample) -> list[str]:
        return self.stage.commands(sample, self.context)[0].command

    def test_single_end_awkward_name_is_quoted_for_bowtie2(self):
        sample = Sample(AWKWARD, ReadLayout.SINGLE, self.root / f"{AWKWARD}.fastq.gz")
        command = self.command_for(sample)
        value = command[command.index("--un-gz") + 1]
        self.assertTrue(value.startswith("'"), f"expected a quoted value, got {value}")
        # Host removal reads the trimmed output, and Bowtie2 opens input paths
        # itself, so the input must stay raw and unquoted.
        trimmed = self.context.workspace.trimmed_reads(sample)[0]
        self.assertEqual(command[command.index("-U") + 1], str(trimmed))

    def test_paired_end_awkward_name_is_quoted_for_bowtie2(self):
        sample = Sample(
            AWKWARD, ReadLayout.PAIRED,
            self.root / f"{AWKWARD}_R1.fastq.gz", self.root / f"{AWKWARD}_R2.fastq.gz",
        )
        command = self.command_for(sample)
        value = command[command.index("--un-conc-gz") + 1]
        self.assertTrue(value.startswith("'"))
        self.assertIn("R%", value, "Bowtie2's mate template must survive quoting")
        trimmed = self.context.workspace.trimmed_reads(sample)
        self.assertEqual(command[command.index("-1") + 1], str(trimmed[0]))
        self.assertEqual(command[command.index("-2") + 1], str(trimmed[1]))

    def test_ordinary_names_produce_an_unquoted_value(self):
        # Nothing about the common case may change.
        sample = Sample("plain", ReadLayout.SINGLE, self.root / "plain.fastq.gz")
        command = self.command_for(sample)
        value = command[command.index("--un-gz") + 1]
        self.assertFalse(value.startswith("'"))
        self.assertEqual(value, str(self.context.workspace.host_removed_reads(sample)[0]))

    def test_the_rest_of_the_command_is_unchanged(self):
        sample = Sample("plain", ReadLayout.SINGLE, self.root / "plain.fastq.gz")
        command = self.command_for(sample)
        self.assertEqual(command[:6], [
            "bowtie2", "--very-sensitive", "-p", "2", "-x", str(self.root / "idx"),
        ])
        self.assertEqual(command[-2:], ["-S", "/dev/null"])


if __name__ == "__main__":
    unittest.main()


def hostrem_available() -> bool:
    """True when BioFlow's managed host-removal environment is installed."""
    from backend.config import reload_config

    config = reload_config()
    return (
        config.micromamba_binary.is_file()
        and config.environment_is_installed("hostrem")
        and (config.environment_prefix("hostrem") / "bin" / "bowtie2").exists()
    )


@unittest.skipUnless(
    hostrem_available(), "BioFlow's managed host-removal environment is not installed"
)
class RealBowtie2PathTests(unittest.TestCase):
    """Runs Bowtie2 for real to prove the shell no longer splits the path.

    Builds its own throwaway reference and index inside a temporary directory;
    it never reads the user's GRCh38 index, data, or configuration.
    """

    index_prefix = None
    reads = None
    _temporary = None

    @classmethod
    def setUpClass(cls):
        import random
        import subprocess

        from backend.config import get_config

        cls._temporary = tempfile.TemporaryDirectory(prefix="bioflow-bt2-real-")
        root = Path(cls._temporary.name)
        generator = random.Random("bioflow-host-removal")
        reference = "".join(generator.choice("ACGT") for _ in range(4000))
        fasta = root / "ref.fa"
        fasta.write_text(
            ">chr_test\n"
            + "\n".join(reference[i:i + 60] for i in range(0, len(reference), 60))
            + "\n",
            encoding="utf-8",
        )
        records = []
        for index in range(120):
            start = (index * 7) % 3900
            sequence = (
                reference[start:start + 80]
                if index % 2 == 0
                else "".join(generator.choice("ACGT") for _ in range(80))
            )
            records += [f"@read{index}", sequence, "+", "I" * len(sequence)]
        cls.reads = root / f"{AWKWARD}.fastq"
        cls.reads.write_text("\n".join(records) + "\n", encoding="utf-8")

        config = get_config()
        cls.index_prefix = root / "idx"
        subprocess.run(
            [str(config.micromamba_binary), "run", "-r", str(config.micromamba_root),
             "-n", config.environment_name("hostrem"),
             "bowtie2-build", "-q", str(fasta), str(cls.index_prefix)],
            check=True, capture_output=True,
        )
        cls.root = root

    @classmethod
    def tearDownClass(cls):
        if cls._temporary is not None:
            cls._temporary.cleanup()

    def _run(self, unmapped: Path, quote: bool):
        import subprocess

        from backend.config import get_config

        config = get_config()
        value = gzip_output_argument(unmapped) if quote else str(unmapped)
        return subprocess.run(
            [str(config.micromamba_binary), "run", "-r", str(config.micromamba_root),
             "-n", config.environment_name("hostrem"),
             "bowtie2", "--very-sensitive", "-p", "2", "-x", str(self.index_prefix),
             "-U", str(self.reads), "--un-gz", value, "-S", "/dev/null"],
            capture_output=True, text=True,
        )

    def test_an_unquoted_awkward_path_still_fails(self):
        # Pins the defect itself: without quoting, Bowtie2's own shell splits it.
        target = self.root / f"unquoted {AWKWARD}_nohost.fastq.gz"
        result = self._run(target, quote=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("syntax error", result.stderr)
        self.assertFalse(target.is_file())

    def test_a_quoted_awkward_path_succeeds_and_keeps_its_name(self):
        target = self.root / f"quoted {AWKWARD}_nohost.fastq.gz"
        result = self._run(target, quote=True)
        self.assertEqual(result.returncode, 0, result.stderr[-400:])
        self.assertTrue(target.is_file(), "the file must be written under its real name")
        self.assertGreater(target.stat().st_size, 0)

    def test_an_ordinary_path_is_unaffected(self):
        target = self.root / "ordinary_nohost.fastq.gz"
        result = self._run(target, quote=True)
        self.assertEqual(result.returncode, 0, result.stderr[-400:])
        self.assertTrue(target.is_file())
