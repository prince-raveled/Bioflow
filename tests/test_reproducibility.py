"""Two runs of the same analysis must profile the same reads.

Found by comparing a container run against a native one. The profiles differed -
41 clades against 47, abundances up to 1.09 points apart - and the backend was
not the reason. Host removal had produced the same reads in a different order,
because Bowtie2 with -p 8 writes them in whatever order its threads finish in.
MetaPhlAn then draws its subsample from the file in order, so the two runs
profiled different reads: of roughly 5,620 reads mapped by each, only 1,787 were
common to both, a Jaccard overlap of 0.189.

Nothing catches this. The reads are the same reads, every output check passes,
and the run is reported as a success. It surfaces only as two runs of one
analysis disagreeing about what is in the sample - which is the thing a
taxonomic profiler exists to tell you.

The fix is Bowtie2's --reorder. The seed was never the problem: MetaPhlAn
already defaults to 1992, and a fixed seed drawing from a differently ordered
file still selects different reads.
"""

from pathlib import Path
import unittest

import support  # noqa: F401  (puts app/ on the path)

from backend.execution.pipeline import stages_by_key  # noqa: E402
from backend.execution.stage import RunContext, RunOptions  # noqa: E402
from backend.execution.stages.taxonomy import SUBSAMPLING_SEED  # noqa: E402
from backend.execution.workspace import Workspace  # noqa: E402
from backend.samples import ReadLayout, Sample  # noqa: E402


ROOT = Path("/probe")
SINGLE = Sample("s", ReadLayout.SINGLE, ROOT / "a.fastq.gz")
PAIRED = Sample("p", ReadLayout.PAIRED, ROOT / "a_R1.fastq.gz", ROOT / "a_R2.fastq.gz")


def context(subsample=None, **fields) -> RunContext:
    return RunContext(
        workspace=Workspace(ROOT / "run"),
        options=RunOptions(threads=8, metaphlan_subsample_pairs=subsample),
        host_index_prefix=ROOT / "GRCh38_index",
        metaphlan_database=ROOT / "db",
        metaphlan_index="mpa_x",
        bowtie2_memory_mapped_shim=ROOT / "shim",
        memory_limit_bytes=14 * 1024 ** 3,
        **fields,
    )


def command_for(key, sample, ctx):
    return stages_by_key()[key].commands(sample, ctx)[0].command


class HostRemovalOrderTests(unittest.TestCase):
    """The output order must not depend on how the threads were scheduled."""

    def test_reads_are_written_in_input_order(self):
        for sample in (SINGLE, PAIRED):
            with self.subTest(layout=sample.layout.name):
                self.assertIn("--reorder", command_for("host_removal", sample, context()))

    def test_it_applies_even_on_a_single_thread(self):
        # Harmless there, and it means the command does not quietly change
        # meaning when someone raises the thread count.
        ctx = context()
        ctx.options.threads = 1
        self.assertIn("--reorder", command_for("host_removal", PAIRED, ctx))

    def test_alignment_is_left_deterministic(self):
        # Bowtie2 seeds its generator from read attributes by default, which is
        # already reproducible. --non-deterministic would undo that.
        command = command_for("host_removal", PAIRED, context())
        self.assertNotIn("--non-deterministic", command)

    def test_the_flag_does_not_disturb_the_rest_of_the_command(self):
        command = command_for("host_removal", PAIRED, context())
        self.assertEqual(command[0], "bowtie2")
        self.assertIn("--very-sensitive", command)
        self.assertEqual(command[command.index("-p") + 1], "8")
        self.assertIn("--un-conc-gz", command)
        self.assertEqual(command[-2:], ["-S", "/dev/null"])


class SubsamplingSeedTests(unittest.TestCase):
    """A subsampled run should say which reads it chose, not inherit it."""

    def test_the_seed_is_named_when_subsampling(self):
        for sample, subsample in ((PAIRED, 100000), (SINGLE, 50000)):
            with self.subTest(layout=sample.layout.name):
                command = command_for("metaphlan", sample, context(subsample))
                self.assertIn("--subsampling_seed", command)
                self.assertEqual(
                    command[command.index("--subsampling_seed") + 1], SUBSAMPLING_SEED
                )

    def test_no_seed_is_named_when_nothing_is_subsampled(self):
        # Profiling every read selects nothing, so a seed would describe a
        # choice that was never made.
        for sample in (SINGLE, PAIRED):
            with self.subTest(layout=sample.layout.name):
                self.assertNotIn(
                    "--subsampling_seed", command_for("metaphlan", sample, context(None))
                )

    def test_the_seed_matches_metaphlan_s_own_default(self):
        # So naming it changes no existing result.
        self.assertEqual(SUBSAMPLING_SEED, "1992")

    def test_the_seed_is_not_random(self):
        self.assertNotEqual(SUBSAMPLING_SEED.lower(), "random")


class CommandStabilityTests(unittest.TestCase):
    """The same analysis must produce the same command every time."""

    def test_commands_do_not_vary_between_builds(self):
        for key in stages_by_key():
            for sample in (SINGLE, PAIRED):
                with self.subTest(stage=key, layout=sample.layout.name):
                    first = command_for(key, sample, context(100000))
                    second = command_for(key, sample, context(100000))
                    self.assertEqual(first, second)

    def test_the_two_stages_that_decide_which_reads_are_profiled_are_pinned(self):
        """The pair that has to agree.

        Host removal decides the order the reads are written in; MetaPhlAn
        decides how many are drawn from that order. Either one left free makes
        the analysis unreproducible, so both are asserted together.
        """
        removal = command_for("host_removal", PAIRED, context(100000))
        profiling = command_for("metaphlan", PAIRED, context(100000))
        self.assertIn("--reorder", removal)
        self.assertIn("--subsampling_seed", profiling)


if __name__ == "__main__":
    unittest.main()
