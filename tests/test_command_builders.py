"""One builder per tool, shared by the pipeline stage and the standalone page.

Each tool used to be invoked from two places: the stage that runs it inside the
workflow, and the page that runs it on files a user picked. The two were
separate code, so a fix could land in one and not the other - the standalone
page reimplemented Bowtie2's -1/-2 versus -U branch, its % output template and
the shell quoting that template needs, none of which anything compared against
the stage.

The stage now owns a builder taking explicit paths, and both callers use it.
These tests hold that arrangement in place: that commands() is a thin wrapper
over the builder rather than a second copy, that both read layouts still reach
the branches they are supposed to, and that the protections which used to be
duplicated into the pages still apply to the paths a page supplies.
"""

from pathlib import Path
import unittest

import support  # noqa: F401  (puts app/ on the path)

from backend.execution.stage import RunContext, RunOptions  # noqa: E402
from backend.execution.stages.host_removal import HostRemovalStage  # noqa: E402
from backend.execution.stages.qc import MultiQCStage, fastqc_raw_stage  # noqa: E402
from backend.execution.stages.taxonomy import MetaPhlAnStage  # noqa: E402
from backend.execution.stages.trimming import FastpStage  # noqa: E402
from backend.execution.workspace import Workspace  # noqa: E402
from backend.samples import ReadLayout, Sample  # noqa: E402


ROOT = Path("/tmp/bioflow-builder-probe")
SINGLE = Sample("s", ReadLayout.SINGLE, ROOT / "a.fastq.gz")
PAIRED = Sample("p", ReadLayout.PAIRED, ROOT / "a_R1.fastq.gz", ROOT / "a_R2.fastq.gz")


def context(**overrides) -> RunContext:
    """A fully configured context, with any field overridable by name."""
    options = overrides.pop("options", {})
    fields = {
        "host_index_prefix": ROOT / "GRCh38_index",
        "metaphlan_database": ROOT / "db",
        "metaphlan_index": "mpa_vJan25_CHOCOPhlAnSGB_202503",
        "bowtie2_memory_mapped_shim": ROOT / "bowtie2-mm",
        "memory_limit_bytes": 14 * 1024 ** 3,
    }
    fields.update(overrides)
    return RunContext(
        workspace=Workspace(ROOT / "run"),
        options=RunOptions(threads=8, **options),
        **fields,
    )


class CommandsIsAThinWrapperTests(unittest.TestCase):
    """commands() must delegate, not carry a second copy of the flags."""

    def test_fastqc(self):
        ctx = context()
        for sample in (SINGLE, PAIRED):
            with self.subTest(layout=sample.layout.name):
                stage = fastqc_raw_stage()
                built = stage.report_command(
                    stage.inputs(sample, ctx), ctx.workspace.qc_raw, ctx
                )
                self.assertEqual(stage.commands(sample, ctx)[0].command, built.command)

    def test_fastp(self):
        ctx = context()
        for sample in (SINGLE, PAIRED):
            with self.subTest(layout=sample.layout.name):
                stage = FastpStage()
                workspace = ctx.workspace
                built = stage.trim_command(
                    reads=sample.reads(),
                    trimmed=workspace.trimmed_reads(sample),
                    html=workspace.fastp_report(sample, "html"),
                    report_json=workspace.fastp_report(sample, "json"),
                    context=ctx,
                    paired=sample.is_paired,
                    label=sample.name,
                )
                self.assertEqual(stage.commands(sample, ctx)[0].command, built.command)

    def test_host_removal(self):
        ctx = context()
        for sample in (SINGLE, PAIRED):
            with self.subTest(layout=sample.layout.name):
                stage = HostRemovalStage()
                workspace = ctx.workspace
                unmatched = (
                    workspace.host_removed_pattern(sample)
                    if sample.is_paired
                    else workspace.host_removed_reads(sample)[0]
                )
                built = stage.removal_command(
                    reads=stage.inputs(sample, ctx),
                    unmatched=unmatched,
                    log=workspace.bowtie2_log(sample),
                    context=ctx,
                    paired=sample.is_paired,
                    label=sample.name,
                )
                produced = stage.commands(sample, ctx)[0]
                self.assertEqual(produced.command, built.command)
                # Bowtie2's summary must still be redirected; losing it would
                # remove the alignment-rate line validation depends on.
                self.assertEqual(produced.stderr_to, workspace.bowtie2_log(sample))

    def test_multiqc(self):
        ctx = context()
        workspace = ctx.workspace
        built = MultiQCStage().aggregate_command(
            [workspace.qc_raw, workspace.qc_trimmed, workspace.trimmed], workspace.multiqc
        )
        self.assertEqual(
            MultiQCStage().commands(SINGLE, ctx)[0].command, built.command
        )

    def test_metaphlan(self):
        ctx = context()
        for sample in (SINGLE, PAIRED):
            with self.subTest(layout=sample.layout.name):
                stage = MetaPhlAnStage()
                workspace = ctx.workspace
                built = stage.profile_command(
                    reads=stage.inputs(sample, ctx),
                    profile=workspace.taxonomic_profile(sample),
                    mapout=workspace.taxonomic_map(sample),
                    context=ctx,
                    paired=sample.is_paired,
                    label=sample.name,
                )
                self.assertEqual(stage.commands(sample, ctx)[0].command, built.command)


class LayoutBranchTests(unittest.TestCase):
    """Both read layouts still reach the arguments they are supposed to."""

    def test_fastp_pairs_both_mates(self):
        ctx = context()
        single = FastpStage().trim_command(
            [ROOT / "a.fastq.gz"], [ROOT / "t.fastq.gz"],
            ROOT / "r.html", ROOT / "r.json", ctx, paired=False,
        ).command
        paired = FastpStage().trim_command(
            [ROOT / "a_R1.fastq.gz", ROOT / "a_R2.fastq.gz"],
            [ROOT / "t1.fastq.gz", ROOT / "t2.fastq.gz"],
            ROOT / "r.html", ROOT / "r.json", ctx, paired=True,
        ).command
        self.assertNotIn("-I", single)
        self.assertNotIn("-O", single)
        # Both mates trimmed together is what keeps them synchronised for Bowtie2.
        self.assertIn("-I", paired)
        self.assertIn("-O", paired)

    def test_host_removal_uses_the_right_output_option(self):
        ctx = context()
        single = HostRemovalStage().removal_command(
            [ROOT / "a.fastq.gz"], ROOT / "u.fastq.gz", ROOT / "b.log", ctx, paired=False
        ).command
        paired = HostRemovalStage().removal_command(
            [ROOT / "a_R1.fastq.gz", ROOT / "a_R2.fastq.gz"],
            ROOT / "u_R%.fastq.gz", ROOT / "b.log", ctx, paired=True,
        ).command
        # --un-gz writes one file; --un-conc-gz expands % into two and keeps only
        # pairs where neither mate aligned. Swapping them silently interleaves.
        self.assertIn("--un-gz", single)
        self.assertNotIn("--un-conc-gz", single)
        self.assertIn("--un-conc-gz", paired)
        self.assertNotIn("--un-gz", paired)
        self.assertIn("-U", single)
        self.assertIn("-1", paired)
        self.assertIn("-2", paired)

    def test_metaphlan_pairing_matches_the_subsampling_form(self):
        plain = MetaPhlAnStage().profile_command(
            [ROOT / "a_R1.fastq.gz", ROOT / "a_R2.fastq.gz"],
            ROOT / "p.txt", ROOT / "m.txt.bz2", context(), paired=True,
        ).command
        subsampled = MetaPhlAnStage().profile_command(
            [ROOT / "a_R1.fastq.gz", ROOT / "a_R2.fastq.gz"],
            ROOT / "p.txt", ROOT / "m.txt.bz2",
            context(options={"metaphlan_subsample_pairs": 1000}), paired=True,
        ).command
        # -1/-2 only alongside --subsampling_paired; MetaPhlAn refuses one
        # without the other, and comma-joined input is the form otherwise.
        self.assertNotIn("-1", plain)
        self.assertIn("-1", subsampled)
        self.assertIn("--subsampling_paired", subsampled)
        # --mapout is always named, which keeps MetaPhlAn 4.2.5 out of the
        # init_mapout() branch that cannot handle a comma-joined input path.
        self.assertIn("--mapout", plain)
        self.assertIn("--mapout", subsampled)


class ProtectionsReachPageSuppliedPathsTests(unittest.TestCase):
    """What the pages used to duplicate must still apply to their paths.

    The host-removal page called gzip_output_argument itself. It no longer
    does, so the quoting has to happen inside the builder or a user whose
    output folder contains a space gets a shell syntax error instead of reads.
    """

    def test_a_space_in_the_output_path_is_quoted(self):
        awkward = Path("/home/user/My Results/p_nohost_R%.fastq.gz")
        command = HostRemovalStage().removal_command(
            [ROOT / "a_R1.fastq.gz", ROOT / "a_R2.fastq.gz"],
            awkward, ROOT / "b.log", context(), paired=True,
        ).command
        value = command[command.index("--un-conc-gz") + 1]
        self.assertNotEqual(value, str(awkward), "the path reached Bowtie2's shell unquoted")
        self.assertIn("My Results", value)

    def test_an_ordinary_path_is_left_exactly_as_it_was(self):
        plain = ROOT / "p_nohost_R%.fastq.gz"
        command = HostRemovalStage().removal_command(
            [ROOT / "a_R1.fastq.gz", ROOT / "a_R2.fastq.gz"],
            plain, ROOT / "b.log", context(), paired=True,
        ).command
        self.assertEqual(command[command.index("--un-conc-gz") + 1], str(plain))

    def test_a_missing_index_is_refused_before_anything_runs(self):
        with self.assertRaises(ValueError):
            HostRemovalStage().removal_command(
                [ROOT / "a.fastq.gz"], ROOT / "u.fastq.gz", ROOT / "b.log",
                context(host_index_prefix=None), paired=False,
            )

    def test_a_missing_database_is_refused_before_anything_runs(self):
        with self.assertRaises(ValueError):
            MetaPhlAnStage().profile_command(
                [ROOT / "a.fastq.gz"], ROOT / "p.txt", ROOT / "m.txt.bz2",
                context(metaphlan_database=None), paired=False,
            )


class DeterminismTests(unittest.TestCase):
    def test_every_builder_repeats_itself(self):
        # The command is part of the checkpoint fingerprint, so a builder that
        # varied between two calls would invalidate results that are still good.
        ctx = context()
        stage = fastqc_raw_stage()
        for _ in range(3):
            self.assertEqual(
                stage.report_command([ROOT / "a.fastq.gz"], ROOT, ctx).command,
                stage.report_command([ROOT / "a.fastq.gz"], ROOT, ctx).command,
            )
            self.assertEqual(
                MultiQCStage().aggregate_command([ROOT], ROOT).command,
                MultiQCStage().aggregate_command([ROOT], ROOT).command,
            )


if __name__ == "__main__":
    unittest.main()
