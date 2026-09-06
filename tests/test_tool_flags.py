"""Every flag BioFlow passes must be one the installed tool actually accepts.

A stale flag is invisible to command-construction tests: they assert the string
was built, not that the tool understands it. `metaphlan --install` was carrying
--bowtie2db, removed in MetaPhlAn 4.1 in favour of --db_dir, and it failed only
when a real download was attempted. These tests read each tool's own --help and
check the flags against it, so a renamed option is caught in seconds.

Each class skips unless its managed environment is installed.
"""

from pathlib import Path
import os
import re
import subprocess
import unittest

import support  # noqa: F401  (puts app/ on the path)
from backend.config import get_config, reload_config  # noqa: E402
from backend.execution.pipeline import stages_by_key  # noqa: E402
from backend.execution.stage import RunContext, RunOptions  # noqa: E402
from backend.execution.workspace import Workspace  # noqa: E402
from backend.samples import ReadLayout, Sample  # noqa: E402
from backend.setup.manager import SetupManager  # noqa: E402
from backend.setup.registry import database_specs  # noqa: E402


def environment_ready(key: str, tool: str) -> bool:
    config = reload_config()
    return (
        config.micromamba_binary.is_file()
        and config.environment_is_installed(key)
        and (config.environment_prefix(key) / "bin" / tool).exists()
    )


class RealInstallationMixin:
    """Pin these tests to the machine's real BioFlow installation.

    They are gated on genuinely installed environments, so unlike the rest of
    the suite they must not run against a temporary data directory. Another
    module may have left one in the environment and in the cached
    configuration, so both are cleared here and restored afterwards.
    """

    _saved_data_dir = None

    @classmethod
    def use_real_installation(cls) -> None:
        cls._saved_data_dir = os.environ.pop("BIOFLOW_DATA_DIR", None)
        reload_config()

    @classmethod
    def restore_environment(cls) -> None:
        if cls._saved_data_dir is not None:
            os.environ["BIOFLOW_DATA_DIR"] = cls._saved_data_dir
        reload_config()


def tool_help(environment_key: str, tool: str) -> str:
    """The tool's own help text, read through the managed environment."""
    config = get_config()
    result = subprocess.run(
        [str(config.micromamba_binary), "run", "-r", str(config.micromamba_root),
         "-n", config.environment_name(environment_key), tool, "--help"],
        capture_output=True, text=True, timeout=300,
    )
    return result.stdout + result.stderr


#: Bowtie2 documents --un / --al / --un-conc / --al-conc and states that a
#: compression suffix may be appended, rather than listing every combination.
#: Strip such a suffix before looking the flag up, or a valid option reads as
#: missing.
COMPRESSION_SUFFIXES = ("-gz", "-bz2", "-lz4")


def long_flags(command: list[str]) -> list[str]:
    """The --flags in a command, ignoring values and single-dash options."""
    return [part for part in command if re.fullmatch(r"--[a-z0-9][a-z0-9_-]*", part)]


def documented_forms(flag: str) -> list[str]:
    """The spellings that would prove a flag is supported."""
    forms = [flag]
    for suffix in COMPRESSION_SUFFIXES:
        if flag.endswith(suffix):
            forms.append(flag[: -len(suffix)])
    return forms


def assert_flag_supported(case, flag: str, help_text: str, tool: str) -> None:
    if not any(form in help_text for form in documented_forms(flag)):
        case.fail(f"{tool} does not accept {flag}")


@unittest.skipUnless(
    environment_ready("taxonomy", "metaphlan"),
    "the managed taxonomic-profiling environment is not installed",
)
class MetaPhlAnFlagTests(RealInstallationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.use_real_installation()
        cls.help_text = tool_help("taxonomy", "metaphlan")

    @classmethod
    def tearDownClass(cls):
        cls.restore_environment()

    def test_the_database_install_flags_are_accepted(self):
        manager = SetupManager()
        spec = database_specs()["metaphlan_chocophlan"]
        step = [s for s in manager._database_steps(spec) if hasattr(s, "arguments")][0]
        # Strip the micromamba wrapper: everything from the tool name onward.
        command = list(step.arguments[step.arguments.index("metaphlan"):])
        for flag in long_flags(command):
            with self.subTest(flag=flag):
                assert_flag_supported(self, flag, self.help_text, "metaphlan")

    def test_the_removed_bowtie2db_flag_is_not_used(self):
        manager = SetupManager()
        spec = database_specs()["metaphlan_chocophlan"]
        step = [s for s in manager._database_steps(spec) if hasattr(s, "arguments")][0]
        self.assertNotIn("--bowtie2db", step.arguments,
                         "--bowtie2db was removed in MetaPhlAn 4.1")

    def test_the_profiling_stage_flags_are_accepted(self):
        context = RunContext(
            workspace=Workspace(Path("/w")),
            options=RunOptions(threads=2),
            metaphlan_database=Path("/db"),
            metaphlan_index="mpa_test",
        )
        sample = Sample("s", ReadLayout.SINGLE, Path("/in/s.fastq.gz"))
        command = stages_by_key()["metaphlan"].commands(sample, context)[0].command
        for flag in long_flags(command):
            with self.subTest(flag=flag):
                assert_flag_supported(self, flag, self.help_text, "metaphlan")


@unittest.skipUnless(
    environment_ready("hostrem", "bowtie2"),
    "the managed host-removal environment is not installed",
)
class Bowtie2FlagTests(RealInstallationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.use_real_installation()
        cls.help_text = tool_help("hostrem", "bowtie2")

    @classmethod
    def tearDownClass(cls):
        cls.restore_environment()

    def test_the_host_removal_flags_are_accepted(self):
        context = RunContext(
            workspace=Workspace(Path("/w")),
            options=RunOptions(threads=2),
            host_index_prefix=Path("/db/idx"),
        )
        for layout, sample in (
            ("single", Sample("s", ReadLayout.SINGLE, Path("/in/s.fastq.gz"))),
            ("paired", Sample("p", ReadLayout.PAIRED,
                              Path("/in/p_R1.fastq.gz"), Path("/in/p_R2.fastq.gz"))),
        ):
            command = stages_by_key()["host_removal"].commands(sample, context)[0].command
            for flag in long_flags(command):
                with self.subTest(layout=layout, flag=flag):
                    assert_flag_supported(self, flag, self.help_text, "bowtie2")


@unittest.skipUnless(
    environment_ready("qc", "fastp"), "the managed quality-control environment is not installed",
)
class QualityControlFlagTests(RealInstallationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.use_real_installation()

    @classmethod
    def tearDownClass(cls):
        cls.restore_environment()

    def test_fastp_flags_are_accepted(self):
        help_text = tool_help("qc", "fastp")
        context = RunContext(workspace=Workspace(Path("/w")), options=RunOptions(threads=2))
        sample = Sample("p", ReadLayout.PAIRED,
                        Path("/in/p_R1.fastq.gz"), Path("/in/p_R2.fastq.gz"))
        command = stages_by_key()["fastp"].commands(sample, context)[0].command
        for flag in long_flags(command):
            with self.subTest(flag=flag):
                assert_flag_supported(self, flag, help_text, "fastp")


if __name__ == "__main__":
    unittest.main()


# ----------------------------------------------------------------------
# The standalone tool pages build their own commands
# ----------------------------------------------------------------------
#: Tools whose flags this module knows how to verify.
VERIFIABLE_TOOLS = {
    "fastqc": "qc",
    "fastp": "qc",
    "multiqc": "qc",
    "bowtie2": "hostrem",
    "metaphlan": "taxonomy",
    "humann": "function",
    "samtools": "hostrem",
    "seqkit": "hostrem",
    "diamond": "function",
}

#: Single-dash options a tool documents in a form this scan cannot match
#: literally, checked by the stage tests instead.
UNCHECKED_SHORT_FLAGS = {"-S", "-U", "-x", "-p", "-i", "-o", "-I", "-O", "-1", "-2"}


def flags_by_tool(path: Path) -> dict[str, set[str]]:
    """Every tool flag a module passes, found without running it.

    Read from the source rather than by calling the page, because the pages
    build their commands inside handlers that need a populated interface. Any
    function mentioning a known tool contributes its flag-shaped literals, so a
    flag added later is covered without editing this test.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, set[str]] = {}
    for function in [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]:
        literals = [
            node.value for node in ast.walk(function)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        tools = {literal for literal in literals if literal in VERIFIABLE_TOOLS}
        if not tools:
            continue
        flags = {
            literal for literal in literals
            if literal.startswith("-") and len(literal) > 1
            and not literal[1].isdigit() and " " not in literal
        }
        for tool in tools:
            found.setdefault(tool, set()).update(flags)
    return found


def page_command_builders() -> dict[str, list[list[str]]]:
    """The argv each standalone page builds, keyed by the page's module name.

    Read by calling the builders the pages call, rather than by scanning their
    source. Source scanning was the only option while the pages assembled their
    own flag lists inside Qt handlers that need a populated interface; now that
    each page delegates to its stage's builder, the exact argv is reachable
    directly - which checks what the page really passes rather than what its
    source happens to spell.

    Both layouts are built for every page that has them, because single-end and
    paired-end reach different branches and a flag can be missing from only one.
    """
    from backend.execution.stage import RunContext, RunOptions
    from backend.execution.stages.host_removal import HostRemovalStage
    from backend.execution.stages.qc import MultiQCStage, fastqc_raw_stage
    from backend.execution.stages.taxonomy import MetaPhlAnStage
    from backend.execution.stages.trimming import FastpStage
    from backend.execution.workspace import Workspace

    root = Path("/tmp/bioflow-flag-probe")
    reads = [root / "a_R1.fastq.gz", root / "a_R2.fastq.gz"]

    def context(**fields):
        return RunContext(
            workspace=Workspace(root),
            options=RunOptions(threads=4, **fields.pop("options", {})),
            memory_limit_bytes=14 * 1024 ** 3,
            **fields,
        )

    built: dict[str, list[list[str]]] = {}

    built["fastqc_page.py"] = [
        fastqc_raw_stage().report_command(reads, root, context()).command
    ]
    built["multiqc_page.py"] = [
        MultiQCStage().aggregate_command(reads, root).command
    ]
    built["fastp_page.py"] = [
        FastpStage().trim_command(
            reads=reads[:count], trimmed=[root / f"t{i}.fastq.gz" for i in range(count)],
            html=root / "r.html", report_json=root / "r.json",
            context=context(), paired=count == 2,
        ).command
        for count in (1, 2)
    ]
    host = context(host_index_prefix=root / "GRCh38_index")
    built["host_removal_page.py"] = [
        HostRemovalStage().removal_command(
            reads=reads[:count], unmatched=root / "u.fastq.gz", log=root / "b.log",
            context=host, paired=count == 2,
        ).command
        for count in (1, 2)
    ]
    taxonomy = context(
        metaphlan_database=root / "db",
        metaphlan_index="mpa_vJan25_CHOCOPhlAnSGB_202503",
        bowtie2_memory_mapped_shim=root / "bowtie2-mm",
    )
    subsampled = context(
        options={"metaphlan_subsample_pairs": 1000},
        metaphlan_database=root / "db",
        metaphlan_index="mpa_vJan25_CHOCOPhlAnSGB_202503",
        bowtie2_memory_mapped_shim=root / "bowtie2-mm",
    )
    built["metaphlan_page.py"] = [
        MetaPhlAnStage().profile_command(
            reads=reads[:count], profile=root / "p.txt", mapout=root / "m.txt.bz2",
            context=ctx, paired=count == 2,
        ).command
        for ctx in (taxonomy, subsampled)
        for count in (1, 2)
    ]
    return built


def flags_in(command: list[str]) -> set[str]:
    """Flag-shaped arguments of one argv, by the same rule as the source scan."""
    return {
        word for word in command
        if word.startswith("-") and len(word) > 1 and not word[1].isdigit()
    }


def tool_of(command: list[str]) -> str:
    return command[0]


class StandalonePageFlagTests(RealInstallationMixin, unittest.TestCase):
    """Every flag a standalone page passes must be one its tool still accepts.

    A page could once carry an option its tool had dropped and nothing would
    notice until someone pressed the button. That is exactly how MetaPhlAn's
    --bypass-nucleotide-search style of drift survives: constructing a command
    proves nothing about whether the tool accepts it.

    The pages no longer assemble their own flags - each delegates to its stage's
    builder - so these check the argv those builders actually produce. That is
    what the page will really run, and it cannot drift from the pipeline while
    both come from one function.
    """

    @classmethod
    def setUpClass(cls):
        cls.use_real_installation()

    @classmethod
    def tearDownClass(cls):
        cls.restore_environment()

    @staticmethod
    def _pages() -> list[Path]:
        return sorted((Path(__file__).resolve().parent.parent / "app" / "gui" / "pages").glob("*.py"))

    def test_the_scan_finds_the_pages_that_build_commands(self):
        # If this ever finds nothing, the tests below are silently vacuous.
        # An empty flag set counts as nothing found, which a dict of empty sets
        # did not: that is how this passed while proving less than it appeared.
        building = {
            name: commands for name, commands in page_command_builders().items()
            if any(flags_in(command) for command in commands)
        }
        self.assertGreaterEqual(
            len(building), 4,
            f"expected several command-building pages, found {sorted(building)}",
        )

    def test_every_page_still_delegates_to_a_stage_builder(self):
        """No page may reintroduce a hand-built command.

        The duplication these tests were written to police is gone because the
        pages call the stages. A page that started spelling its own flags again
        would restore it, so the source is checked for exactly that.
        """
        for path in self._pages():
            with self.subTest(page=path.name):
                self.assertEqual(
                    {tool: sorted(flags) for tool, flags in flags_by_tool(path).items() if flags},
                    {},
                    f"{path.name} builds tool flags itself instead of calling its stage",
                )

    def test_every_flag_a_page_passes_is_accepted_by_its_tool(self):
        checked = 0
        for name, commands in sorted(page_command_builders().items()):
            for command in commands:
                tool = tool_of(command)
                if tool not in VERIFIABLE_TOOLS:
                    continue
                environment = VERIFIABLE_TOOLS[tool]
                if not environment_ready(environment, tool):
                    continue
                help_text = tool_help(environment, tool)
                for flag in sorted(flags_in(command)):
                    if flag in UNCHECKED_SHORT_FLAGS:
                        continue
                    with self.subTest(page=name, tool=tool, flag=flag):
                        assert_flag_supported(self, flag, help_text, tool)
                        checked += 1
        self.assertGreater(checked, 0, "no page flags were verified against any tool")

    def test_the_pages_and_the_stages_agree_on_their_shared_flags(self):
        """A flag used by both must mean the same thing in both.

        Previously the pages and the stages were separate builders for the same
        tools, and this could only require that the page had not dropped a flag
        the stage still passed. They are now one builder, so the stronger
        statement holds and is asserted instead: for the same layout the two
        callers produce the same flags exactly.
        """
        from backend.execution.pipeline import stages_by_key
        from backend.execution.stage import RunContext, RunOptions
        from backend.execution.workspace import Workspace
        from backend.samples import ReadLayout, Sample

        root = Path("/tmp/bioflow-flag-probe")

        def context(subsample=None):
            return RunContext(
                workspace=Workspace(root),
                options=RunOptions(threads=4, metaphlan_subsample_pairs=subsample),
                host_index_prefix=root / "GRCh38_index",
                metaphlan_database=root / "db",
                metaphlan_index="mpa_vJan25_CHOCOPhlAnSGB_202503",
                bowtie2_memory_mapped_shim=root / "bowtie2-mm",
                memory_limit_bytes=14 * 1024 ** 3,
            )

        samples = [
            Sample("s", ReadLayout.SINGLE, root / "a.fastq.gz"),
            Sample("p", ReadLayout.PAIRED, root / "a_R1.fastq.gz", root / "a_R2.fastq.gz"),
        ]
        stage_flags: dict[str, set[str]] = {}
        # Both option sets, because subsampling adds flags on one branch only
        # and the page offers the same control. Comparing a stage that never
        # subsamples against a page that can would report a difference that is
        # only in the probe.
        for subsample in (None, 1000):
            for stage in stages_by_key().values():
                for sample in samples:
                    for command in stage.commands(sample, context(subsample)):
                        stage_flags.setdefault(tool_of(command.command), set()).update(
                            flags_in(command.command)
                        )

        page_flags: dict[str, set[str]] = {}
        for commands in page_command_builders().values():
            for command in commands:
                page_flags.setdefault(tool_of(command), set()).update(flags_in(command))

        for tool in ("fastqc", "fastp", "multiqc", "bowtie2", "metaphlan"):
            with self.subTest(tool=tool):
                self.assertEqual(
                    page_flags.get(tool, set()),
                    stage_flags.get(tool, set()),
                    f"the {tool} page and stage no longer produce the same flags",
                )
