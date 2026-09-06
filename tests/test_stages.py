"""Command construction for every real stage, single-end and paired-end.

Pure construction tests: no tool is executed.
"""

from pathlib import Path
import unittest

import support  # noqa: F401  (puts app/ on the path)
from backend.execution.pipeline import default_stages, stages_by_key  # noqa: E402
from backend.execution.stage import RunContext, RunOptions  # noqa: E402
from backend.execution.workspace import Workspace  # noqa: E402
from backend.samples import ReadLayout, Sample  # noqa: E402


def make_context(root=Path("/w"), **overrides) -> RunContext:
    settings = dict(
        workspace=Workspace(root),
        options=RunOptions(threads=8),
        host_index_prefix=Path("/db/GRCh38_index"),
        metaphlan_database=Path("/db/metaphlan"),
        metaphlan_index="mpa_vJan25_CHOCOPhlAnSGB_202503",
    )
    settings.update(overrides)
    return RunContext(**settings)


SINGLE = Sample("s1", ReadLayout.SINGLE, Path("/in/s1.fastq.gz"))
PAIRED = Sample("p1", ReadLayout.PAIRED, Path("/in/p1_R1.fastq.gz"), Path("/in/p1_R2.fastq.gz"))


def flat(stage, sample, context) -> list[str]:
    return [" ".join(command.command) for command in stage.commands(sample, context)]


class StageOrderTests(unittest.TestCase):
    def test_the_documented_workflow_order(self):
        # The specification's order, including stages a given release withholds.
        from backend.execution.pipeline import all_stages

        self.assertEqual(
            [stage.key for stage in all_stages()],
            ["fastqc_raw", "fastp", "fastqc_trimmed", "host_removal", "metaphlan", "humann", "multiqc"],
        )

    def test_this_release_offers_the_workflow_up_to_taxonomic_profiling(self):
        # Functional profiling needs resources beyond what this version targets.
        self.assertEqual(
            [stage.key for stage in default_stages()],
            ["fastqc_raw", "fastp", "fastqc_trimmed", "host_removal", "metaphlan", "multiqc"],
        )

    def test_the_withheld_stage_is_still_built_and_reachable(self):
        # Withheld, not deleted: it must keep working for the release that ships it.
        from backend.execution.pipeline import stages_by_key

        stage = stages_by_key()["humann"]
        self.assertEqual(stage.environment_key, "function")

    def test_each_stage_declares_its_environment(self):
        expected = {
            "fastqc_raw": "qc", "fastp": "qc", "fastqc_trimmed": "qc",
            "host_removal": "hostrem", "metaphlan": "taxonomy",
            "humann": "function", "multiqc": "qc",
        }
        for stage in default_stages():
            self.assertEqual(stage.environment_key, expected[stage.key], stage.key)

    def test_each_stage_declares_its_databases(self):
        registry = stages_by_key()
        self.assertEqual(registry["host_removal"].required_databases, ("grch38",))
        self.assertEqual(registry["metaphlan"].required_databases, ("metaphlan_chocophlan",))
        self.assertEqual(registry["fastqc_raw"].required_databases, ())

    def test_only_multiqc_runs_once_per_project(self):
        per_project = [stage.key for stage in default_stages() if stage.per_project]
        self.assertEqual(per_project, ["multiqc"])


class SingleEndCommandTests(unittest.TestCase):
    def setUp(self):
        self.context = make_context()
        self.registry = stages_by_key()

    def test_fastqc_passes_one_file(self):
        command = flat(self.registry["fastqc_raw"], SINGLE, self.context)[0]
        self.assertIn("/in/s1.fastq.gz", command)
        self.assertNotIn("_R2", command)

    def test_fastp_uses_lowercase_i_and_o_only(self):
        command = flat(self.registry["fastp"], SINGLE, self.context)[0]
        self.assertIn("-i /in/s1.fastq.gz", command)
        self.assertNotIn(" -I ", command)
        self.assertNotIn(" -O ", command)

    def test_bowtie2_uses_unpaired_flags(self):
        command = flat(self.registry["host_removal"], SINGLE, self.context)[0]
        self.assertIn(" -U ", command)
        self.assertIn("--un-gz", command)
        self.assertNotIn("--un-conc-gz", command)
        self.assertNotIn(" -1 ", command)

    def test_metaphlan_receives_a_single_path(self):
        command = flat(self.registry["metaphlan"], SINGLE, self.context)[0]
        self.assertIn("/w/04_host_removed/s1_nohost.fastq.gz", command)
        self.assertNotIn(",", command.split()[1])

    def test_humann_reads_the_host_removed_file_directly(self):
        commands = flat(self.registry["humann"], SINGLE, self.context)
        self.assertIn("--input /w/04_host_removed/s1_nohost.fastq.gz", commands[0])
        self.assertNotIn("combined", commands[0])

    def test_no_fake_second_mate_is_ever_created(self):
        workspace = self.context.workspace
        self.assertEqual(len(workspace.trimmed_reads(SINGLE)), 1)
        self.assertEqual(len(workspace.host_removed_reads(SINGLE)), 1)


class PairedEndCommandTests(unittest.TestCase):
    def setUp(self):
        self.context = make_context()
        self.registry = stages_by_key()

    def test_fastqc_reports_on_both_mates(self):
        command = flat(self.registry["fastqc_raw"], PAIRED, self.context)[0]
        self.assertIn("/in/p1_R1.fastq.gz", command)
        self.assertIn("/in/p1_R2.fastq.gz", command)

    def test_fastp_trims_both_mates_together(self):
        command = flat(self.registry["fastp"], PAIRED, self.context)[0]
        self.assertIn("-i /in/p1_R1.fastq.gz", command)
        self.assertIn("-I /in/p1_R2.fastq.gz", command)
        self.assertIn("-o /w/02_trimmed/p1_R1.trim.fastq.gz", command)
        self.assertIn("-O /w/02_trimmed/p1_R2.trim.fastq.gz", command)

    def test_bowtie2_preserves_pairing_with_the_mate_template(self):
        command = flat(self.registry["host_removal"], PAIRED, self.context)[0]
        self.assertIn(" -1 ", command)
        self.assertIn(" -2 ", command)
        self.assertIn("--un-conc-gz /w/04_host_removed/p1_nohost_R%.fastq.gz", command)
        self.assertNotIn("--un-gz ", command)

    def test_trimmed_and_host_removed_stay_two_files(self):
        workspace = self.context.workspace
        self.assertEqual(len(workspace.trimmed_reads(PAIRED)), 2)
        self.assertEqual(len(workspace.host_removed_reads(PAIRED)), 2)

    def test_metaphlan_joins_mates_with_a_comma_not_a_merge(self):
        command = flat(self.registry["metaphlan"], PAIRED, self.context)[0]
        reads = command.split()[1]
        self.assertEqual(
            reads,
            "/w/04_host_removed/p1_nohost_R1.fastq.gz,/w/04_host_removed/p1_nohost_R2.fastq.gz",
        )

    def test_humann_uses_the_combined_file_because_the_tool_requires_it(self):
        commands = flat(self.registry["humann"], PAIRED, self.context)
        self.assertIn("--input /w/06_functional/p1/p1_combined.fastq.gz", commands[0])


class HumannModeTests(unittest.TestCase):
    def test_protein_only_mode_bypasses_and_needs_only_uniref(self):
        context = make_context(options=RunOptions(threads=4, humann_protein_only=True))
        stage = stages_by_key()["humann"]
        command = flat(stage, SINGLE, context)[0]
        self.assertIn("--bypass-prescreen", command)
        self.assertIn("--bypass-nucleotide-search", command)
        self.assertEqual(stage.databases_for(context), ("humann_uniref50",))

    def test_full_mode_drops_the_bypasses_and_needs_chocophlan(self):
        context = make_context(options=RunOptions(threads=4, humann_protein_only=False))
        stage = stages_by_key()["humann"]
        command = flat(stage, SINGLE, context)[0]
        self.assertNotIn("--bypass", command)
        self.assertEqual(
            stage.databases_for(context), ("humann_uniref50", "humann_chocophlan")
        )

    def test_normalisation_adds_the_renorm_commands(self):
        context = make_context(options=RunOptions(humann_normalise=True))
        commands = flat(stages_by_key()["humann"], SINGLE, context)
        self.assertEqual(len(commands), 3)
        self.assertTrue(all("--units cpm" in c for c in commands[1:]))

    def test_normalisation_can_be_switched_off(self):
        context = make_context(options=RunOptions(humann_normalise=False))
        self.assertEqual(len(flat(stages_by_key()["humann"], SINGLE, context)), 1)


class MissingReferenceTests(unittest.TestCase):
    def test_host_removal_refuses_without_an_index(self):
        context = make_context(host_index_prefix=None)
        with self.assertRaises(ValueError):
            stages_by_key()["host_removal"].commands(SINGLE, context)

    def test_metaphlan_refuses_without_a_database(self):
        context = make_context(metaphlan_database=None)
        with self.assertRaises(ValueError):
            stages_by_key()["metaphlan"].commands(SINGLE, context)


if __name__ == "__main__":
    unittest.main()
