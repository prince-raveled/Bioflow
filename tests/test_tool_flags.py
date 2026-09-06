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


class StandalonePageFlagTests(RealInstallationMixin, unittest.TestCase):
    """The tool pages construct commands independently of the pipeline stages.

    Only the stages were flag-checked, so a page could carry an option its tool
    had dropped and nothing would notice until someone pressed the button. That
    is exactly how MetaPhlAn's --bowtie2db survived: constructing the command
    proved nothing about whether the tool still accepted it.

    The flags are discovered from the source, so this keeps covering the pages
    as they change rather than pinning today's list.
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
        # If this ever finds nothing, the test below is silently vacuous.
        building = [path.name for path in self._pages() if flags_by_tool(path)]
        self.assertGreaterEqual(
            len(building), 4, f"expected several command-building pages, found {building}"
        )

    def test_every_flag_a_page_passes_is_accepted_by_its_tool(self):
        checked = 0
        for path in self._pages():
            for tool, flags in sorted(flags_by_tool(path).items()):
                environment = VERIFIABLE_TOOLS[tool]
                if not environment_ready(environment, tool):
                    continue
                help_text = tool_help(environment, tool)
                for flag in sorted(flags):
                    if flag in UNCHECKED_SHORT_FLAGS:
                        continue
                    with self.subTest(page=path.name, tool=tool, flag=flag):
                        assert_flag_supported(self, flag, help_text, tool)
                        checked += 1
        self.assertGreater(checked, 0, "no page flags were verified against any tool")

    def test_the_pages_and_the_stages_agree_on_their_shared_flags(self):
        """A flag used by both must mean the same thing in both.

        The pages and the stages are separate command builders for the same
        tools. They are allowed to differ - a page writes wherever the user
        chose - but a flag one of them uses and the other has abandoned is a
        sign that a fix landed in only one of the two.
        """
        page_flags: dict[str, set[str]] = {}
        for path in self._pages():
            for tool, flags in flags_by_tool(path).items():
                page_flags.setdefault(tool, set()).update(flags)

        stage_flags = {
            "fastqc": {"--threads", "--outdir"},
            "fastp": {"--thread", "--html", "--json"},
            "multiqc": {"--outdir", "--force"},
            "bowtie2": {"--very-sensitive", "--un-gz", "--un-conc-gz"},
        }
        for tool, expected in stage_flags.items():
            with self.subTest(tool=tool):
                self.assertTrue(
                    expected <= page_flags.get(tool, set()),
                    f"the {tool} page no longer passes {expected - page_flags.get(tool, set())}, "
                    f"which the pipeline stage still does",
                )
